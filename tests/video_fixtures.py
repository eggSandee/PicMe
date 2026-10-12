"""Video fixture helpers: tiny ffmpeg-generated clips and handcrafted ISO-BMFF bytes.

Kept out of ``conftest.py`` so video tests don't depend on (or collide with)
changes to the shared photo fixtures. ffmpeg-backed helpers are guarded by
``requires_ffmpeg`` so the suite still runs where ffmpeg isn't installed.
"""
from __future__ import annotations

import datetime
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

FFMPEG = shutil.which("ffmpeg")
requires_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")

MAC_EPOCH = datetime.datetime(1904, 1, 1, tzinfo=datetime.timezone.utc)

# Encoder args per codec. hvc1 tagging is what Apple devices write for HEVC .mov files.
_CODEC_ARGS = {
    "h264": ["-c:v", "libx264", "-pix_fmt", "yuv420p"],
    "hevc": ["-c:v", "libx265", "-pix_fmt", "yuv420p", "-tag:v", "hvc1", "-x265-params", "log-level=error"],
}


def make_clip(
    path: Path,
    *,
    codec: str = "h264",
    duration: float = 1,
    creation_time: str | None = None,
    faststart: bool = False,
    color: str | None = None,
) -> Path:
    """Encode a tiny silent test clip (160x120, 10 fps) to ``path``.

    ``creation_time`` (ISO 8601, e.g. ``2021-06-15T03:30:00Z``) is written to the
    container's mvhd. ``faststart`` puts ``moov`` before ``mdat``; ffmpeg's default
    is ``moov`` after ``mdat``.
    """
    if FFMPEG is None:
        pytest.skip("ffmpeg not installed")
    source = f"color=c={color}:size=160x120:rate=10" if color else "testsrc=size=160x120:rate=10"
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"{source}:duration={duration}",
           *_CODEC_ARGS[codec]]
    if creation_time:
        cmd += ["-metadata", f"creation_time={creation_time}"]
    if faststart:
        cmd += ["-movflags", "+faststart"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True, capture_output=True)
    return path


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
