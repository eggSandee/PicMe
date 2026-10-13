"""Video fixture helpers: tiny ffmpeg-generated clips and handcrafted ISO-BMFF bytes.

Kept out of ``conftest.py`` so video tests don't depend on (or collide with)
changes to the shared photo fixtures. ffmpeg-backed helpers are guarded by
``requires_ffmpeg`` so the suite still runs where ffmpeg isn't installed.
"""
from __future__ import annotations

import datetime
import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")


def _has_encoder(name: str) -> bool:
    if FFMPEG is None:
        return False
    out = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return any(line.split()[1:2] == [name] for line in out.splitlines())


HAS_LIBX265 = _has_encoder("libx265")
requires_libx265 = pytest.mark.skipif(not HAS_LIBX265, reason="ffmpeg without libx265 (HEVC fixtures)")

MAC_EPOCH = datetime.datetime(1904, 1, 1, tzinfo=datetime.timezone.utc)

# Encoder args per codec. hvc1 tagging is what Apple devices write for HEVC .mov files.
_CODEC_ARGS = {
    "h264": ["-c:v", "libx264", "-pix_fmt", "yuv420p"],
    "hevc": ["-c:v", "libx265", "-pix_fmt", "yuv420p", "-tag:v", "hvc1", "-x265-params", "log-level=error"],
}

# iPhone HDR: 10-bit BT.2020 with HLG (most clips) or PQ (Dolby Vision profile 5-like)
_HDR_TRANSFER = {"hlg": "arib-std-b67", "pq": "smpte2084"}

# Top-left red, top-right green, bottom-left blue, bottom-right yellow — so orientation
# can be checked from a decoded corner pixel
QUADRANT_COLORS = {"tl": (255, 0, 0), "tr": (0, 255, 0), "bl": (0, 0, 255), "br": (255, 255, 0)}


def _quadrant_source(w: int, h: int, rate: int, duration: float) -> str:
    qw, qh = w // 2, h // 2
    cells = "".join(f"color=c={c}:s={qw}x{qh}:r={rate}:d={duration}[{n}];"
                    for n, c in (("a", "red"), ("b", "lime"), ("c", "blue"), ("d", "yellow")))
    return cells + "[a][b][c][d]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0"


def make_clip(
    path: Path,
    *,
    codec: str = "h264",
    duration: float = 1,
    creation_time: str | None = None,
    faststart: bool = False,
    color: str | None = None,
    size: tuple[int, int] = (160, 120),
    rate: int = 10,
    hdr: str | None = None,
    rotation: int = 0,
    audio: str | None = None,
    quadrants: bool = False,
) -> Path:
    """Encode a tiny test clip (160x120, 10 fps, silent by default) to ``path``.

    ``creation_time`` (ISO 8601, e.g. ``2021-06-15T03:30:00Z``) is written to the
    container's mvhd. ``faststart`` puts ``moov`` before ``mdat``; ffmpeg's default
    is ``moov`` after ``mdat``. ``hdr`` ("hlg"/"pq", HEVC only) makes a 10-bit BT.2020
    clip, ``rotation`` adds an iPhone-style display matrix (counter-clockwise degrees),
    ``audio`` adds a tone in that codec (e.g. "aac"), ``quadrants`` draws four colored
    quarters (see QUADRANT_COLORS) instead of a test pattern.
    """
    if FFMPEG is None:
        pytest.skip("ffmpeg not installed")
    if codec == "hevc" and not HAS_LIBX265:
        pytest.skip("ffmpeg without libx265")
    w, h = size
    if quadrants:
        cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", _quadrant_source(w, h, rate, duration)]
    else:
        source = f"color=c={color}:" if color else "testsrc="
        cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"{source}size={w}x{h}:rate={rate}:duration={duration}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=duration={duration}", "-c:a", audio, "-shortest"]
    if hdr:
        trc = _HDR_TRANSFER[hdr]
        cmd += ["-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1", "-x265-params",
                f"log-level=error:colorprim=bt2020:transfer={trc}:colormatrix=bt2020nc",
                "-color_primaries", "bt2020", "-color_trc", trc, "-colorspace", "bt2020nc"]
    else:
        cmd += _CODEC_ARGS[codec]
    if creation_time:
        cmd += ["-metadata", f"creation_time={creation_time}"]
    if faststart:
        cmd += ["-movflags", "+faststart"]
    if rotation:
        # -display_rotation is an input option, so the matrix is added in a second (copy) pass
        tmp = path.with_name(f".unrotated-{path.name}")
        subprocess.run([*cmd, str(tmp)], check=True, capture_output=True)
        try:
            subprocess.run([FFMPEG, "-v", "error", "-y", "-display_rotation", str(rotation), "-i", str(tmp),
                            "-c", "copy", *(["-movflags", "+faststart"] if faststart else []), str(path)],
                           check=True, capture_output=True)
        finally:
            tmp.unlink(missing_ok=True)
        return path
    cmd.append(str(path))
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def make_hdr_card(path: Path, curve: str, patches: list[tuple[float, float, float]],
                  size: tuple[int, int] = (320, 180)) -> Path:
    """A 10-bit BT.2020 HLG/PQ HEVC clip of flat patches with exact R'G'B' code values
    (0–1), laid out 4 per row left to right, top to bottom (see ``patch_centers``)."""
    if FFMPEG is None or not HAS_LIBX265:
        pytest.skip("ffmpeg with libx265 needed")
    w, h = size
    cols, rows = 4, -(-len(patches) // 4)
    planes = [bytearray(), bytearray(), bytearray()]
    for y in range(h):
        for x in range(w):
            r, g, b = patches[min(len(patches) - 1, (y * rows // h) * cols + x * cols // w)]
            luma = 0.2627 * r + 0.6780 * g + 0.0593 * b   # BT.2020 non-constant luminance
            codes = (64 + 876 * luma, 512 + 896 * (b - luma) / 1.8814, 512 + 896 * (r - luma) / 1.4746)
            for plane, v in zip(planes, codes):
                plane += struct.pack("<H", max(0, min(1023, round(v))))
    trc = _HDR_TRANSFER[curve]
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv444p10le", "-s", f"{w}x{h}",
                    "-r", "10", "-i", "-", "-vf", "loop=4:1:0", "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
                    "-tag:v", "hvc1", "-x265-params",
                    f"log-level=error:crf=4:colorprim=bt2020:transfer={trc}:colormatrix=bt2020nc",
                    "-color_primaries", "bt2020", "-color_trc", trc, "-colorspace", "bt2020nc", str(path)],
                   input=b"".join(planes), check=True, capture_output=True)
    return path


def patch_centers(n: int, size: tuple[int, int]) -> list[tuple[int, int]]:
    w, h = size
    rows = -(-n // 4)
    return [((i % 4 * 2 + 1) * w // 8, (i // 4 * 2 + 1) * h // (2 * rows)) for i in range(n)]


def pixel_at(path: Path, x: int, y: int) -> tuple[int, int, int]:
    """Average RGB of the 8x8 block centred on (x, y) in the first decoded frame."""
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vf", f"crop=8:8:{x - 4}:{y - 4},scale=1:1",
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    return tuple(raw[:3])


def ffprobe_streams(path: Path) -> list[dict]:
    out = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", str(path)],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)["streams"]


def corner_pixel(path: Path, corner: str = "tl") -> tuple[int, int, int]:
    """Average RGB of a small patch near a corner of the first decoded frame."""
    (v,) = [s for s in ffprobe_streams(path) if s["codec_type"] == "video"]
    w, h = v["width"], v["height"]
    x = 2 if corner[1] == "l" else w - 10
    y = 2 if corner[0] == "t" else h - 10
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vf", f"crop=8:8:{x}:{y},scale=1:1",
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    return tuple(raw[:3])


# --- ISO-BMFF builders ---

def box(box_type: bytes, payload: bytes = b"") -> bytes:
    """A box with a normal 32-bit size header."""
    return struct.pack(">I4s", 8 + len(payload), box_type) + payload


def large_box(box_type: bytes, payload: bytes = b"") -> bytes:
    """A box using size==1 + a 64-bit largesize header."""
    return struct.pack(">I4sQ", 1, box_type, 16 + len(payload)) + payload


def open_box(box_type: bytes, payload: bytes = b"") -> bytes:
    """A box with size==0: it runs to the end of its container (here, end of file)."""
    return struct.pack(">I4s", 0, box_type) + payload


def mac_seconds(dt: datetime.datetime) -> int:
    """Seconds since 1904-01-01 UTC, as stored in mvhd creation_time."""
    return int((dt - MAC_EPOCH).total_seconds())


def mvhd(seconds: int, version: int = 0) -> bytes:
    """An mvhd box with the given creation_time (modification_time mirrors it)."""
    if version == 1:
        times = struct.pack(">QQIQ", seconds, seconds, 1000, 0)  # creation, modification, timescale, duration
    else:
        times = struct.pack(">IIII", seconds, seconds, 1000, 0)
    # rate, volume, reserved, matrix, pre_defined, next_track_ID — values don't matter to the parser
    rest = struct.pack(">IH10x36s24xI", 0x00010000, 0x0100, b"\x00" * 36, 2)
    return box(b"mvhd", bytes([version, 0, 0, 0]) + times + rest)


FTYP = box(b"ftyp", b"isom" + struct.pack(">I", 0x200) + b"isomiso2mp41")
