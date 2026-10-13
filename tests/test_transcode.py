"""HEVC → H.264 transcoding: codec sniffing, ffmpeg command building, the background queue,
and the /api/video, /api/images/list and /api/videos/status integration.

Integration tests generate real HEVC / 10-bit HLG / rotated clips with ffmpeg (libx265) and
convert them for real. Each test gets a fresh transcoder whose worker doesn't auto-start
(``video_transcoder`` in conftest); tests convert synchronously with ``run_until_idle()``.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pytest

import cache
import heic
import transcode
from metadata.video import VideoSniff, _iter_boxes, sniff_video
from transcode import (
    Capabilities,
    Probe,
    TIER_HW,
    TIER_SW,
    TIER_X264,
    build_command,
    needs_transcode,
    output_size,
    parse_probe,
    tiers_for,
)
from video_fixtures import (
    FFMPEG,
    corner_pixel,
    ffprobe_streams,
    make_clip,
    make_hdr_card,
    patch_centers,
    pixel_at,
    requires_ffmpeg,
    requires_libx265,
)

GREEN, RED, BLUE = (0, 255, 0), (255, 0, 0), (0, 0, 255)

ALL_CAPS = Capabilities("test", frozenset({"libx264", "h264_videotoolbox"}),
                        frozenset({"scale_vt", "transpose_vt", "colorspace", "scale"}))
SW_CAPS = Capabilities("test", frozenset({"libx264", "h264_videotoolbox"}), frozenset({"colorspace"}))
X264_CAPS = Capabilities("test", frozenset({"libx264"}), frozenset({"colorspace"}))
LUT_CAPS = Capabilities("test", frozenset({"libx264", "h264_videotoolbox"}),
                        frozenset({"scale_vt", "transpose_vt", "lut3d", "zscale"}))
ZSCALE_CAPS = Capabilities("test", frozenset({"libx264"}), frozenset({"zscale", "tonemap"}))


def close(rgb, expected, tol=60):
    return all(abs(a - b) <= tol for a, b in zip(rgb, expected))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def video_stream(path: Path) -> dict:
    (v,) = [s for s in ffprobe_streams(path) if s["codec_type"] == "video"]
    return v


@pytest.fixture
def lib(tmp_path) -> Path:
    d = tmp_path / "videos"
    d.mkdir()
    return d


@pytest.fixture
def video_cache(heic_cache_dir) -> Path:
    return heic_cache_dir.parent / "video"


def converted(path: Path) -> Path:
    clip = transcode.transcoder.clip(path)
    assert clip.state == transcode.READY, clip.state
    assert clip.serve != path
    return clip.serve


# --- Cache dir ---

def test_video_cache_is_sibling_of_heic_cache(heic_cache_dir):
    assert transcode.cache_dir() == heic_cache_dir.parent / "video"
    assert heic.cache_dir() == heic_cache_dir
    assert transcode.cache_dir().is_dir()


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_cache_root_os_defaults(monkeypatch, tmp_path, platform):
    monkeypatch.delenv("PICME_CACHE_DIR")
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / ".cache"))
    expected = {"darwin": tmp_path / "Library" / "Caches" / "PicMe",
                "win32": tmp_path / "AppData" / "Local" / "PicMe" / "Cache",
                "linux": tmp_path / ".cache" / "picme"}[platform]
    assert cache.cache_root() == expected
    assert cache.cache_subdir("video") == expected / "video"


# --- Sniffing ---

@requires_ffmpeg
def test_sniff_h264(tmp_path):
    clip = make_clip(tmp_path / "a.mp4", size=(320, 180), duration=2, audio="aac")
    s = sniff_video(clip)
    assert (s.codec, s.width, s.height) == ("avc1", 320, 180)
    assert s.duration_s == pytest.approx(2, abs=0.1)
    assert s.profile == 100  # libx264 default: High
    assert s.audio == ("mp4a",)


@requires_libx265
def test_sniff_hevc_hlg(tmp_path):
    clip = make_clip(tmp_path / "IMG_0001.MOV", hdr="hlg", rotation=90)
    s = sniff_video(clip)
    assert s.codec == "hvc1"
    assert s.transfer == 18
    assert s.audio == ()


@pytest.mark.parametrize("data", [b"", b"garbage" * 10, b"\0\0\0\x10ftypqt  \0\0\0\0"])
def test_sniff_unreadable_is_none(tmp_path, data):
    p = tmp_path / "x.mov"
    p.write_bytes(data)
    assert sniff_video(p) is None


@requires_ffmpeg
def test_sniff_is_refreshed_when_file_changes(tmp_path):
    p = make_clip(tmp_path / "a.mp4", size=(160, 120))
    assert sniff_video(p).width == 160
    make_clip(tmp_path / "b.mp4", size=(320, 240))
    os.replace(tmp_path / "b.mp4", p)
    assert sniff_video(p).width == 320


# --- Classification ---

def _sniff(**kw) -> VideoSniff:
    base = dict(codec="avc1", width=1920, height=1080, duration_s=5.0, profile=100, transfer=1, audio=("mp4a",))
    return VideoSniff(**{**base, **kw})


@pytest.mark.parametrize("kw, max_edge, expected", [
    ({}, 1920, False),                                 # plain H.264/AAC 1080p plays as-is
    ({"audio": ()}, 1920, False),                      # silent
    ({"codec": "avc3"}, 1920, False),
    ({"codec": "hvc1"}, 1920, True),                   # iPhone HEVC
    ({"codec": "hev1"}, 1920, True),
    ({"codec": "dvh1"}, 1920, True),                   # Dolby Vision
    ({"codec": "apcn"}, 1920, True),                   # ProRes
    ({"profile": 110}, 1920, True),                    # High 10
    ({"transfer": 18}, 1920, True),                    # HLG H.264
    ({"transfer": 16}, 1920, True),                    # PQ
    ({"width": 3840, "height": 2160}, 1920, True),     # 4K over the cap
    ({"width": 3840, "height": 2160}, 0, False),       # no cap
    ({"audio": ("apac",)}, 1920, True),                # iPhone 16 spatial audio
    ({"audio": ("lpcm",)}, 1920, True),
])
def test_needs_transcode(kw, max_edge, expected):
    assert needs_transcode(_sniff(**kw), max_edge) is expected


@pytest.mark.parametrize("w, h, rot, cap, expected", [
    (3840, 2160, 0, 1920, (1920, 1080)),
    (3840, 2160, 90, 1920, (1080, 1920)),    # portrait iPhone clip
    (3840, 2160, -90, 1920, (1080, 1920)),
    (640, 360, 90, 1920, (360, 640)),        # never upscaled
    (3840, 2160, 0, 0, (3840, 2160)),        # no cap
    (1001, 563, 0, 0, (1002, 564)),          # even dims for yuv420p
])
def test_output_size(w, h, rot, cap, expected):
    assert output_size(w, h, rot, cap) == expected


def _probe(**kw) -> Probe:
    base = dict(codec="hevc", pix_fmt="yuv420p10le", width=3840, height=2160, rotation=0,
                transfer="arib-std-b67", primaries="bt2020", fps=30.0, duration=10.0)
    return Probe(**{**base, **kw})


def _sdr(**kw) -> Probe:
    return _probe(**{"pix_fmt": "yuv420p", "transfer": "bt709", "primaries": "bt709", **kw})


def _cmd(p: Probe, caps=ALL_CAPS, tier=TIER_HW, max_edge=1920) -> list[str]:
    return build_command("ffmpeg", Path("/in.mov"), Path("/out.tmp.mp4"), p, caps, max_edge, tier)


def _vf(cmd: list[str]) -> str:
    return cmd[cmd.index("-vf") + 1]


@pytest.mark.parametrize("rotation, direction", [(90, "cclock"), (-90, "clock"), (270, "clock")])
def test_hw_command_rotates_explicitly(rotation, direction):
    cmd = _cmd(_sdr(rotation=rotation))
    assert "videotoolbox" in cmd[cmd.index("-hwaccel") + 1]
    vf = _vf(cmd)
    assert vf.startswith(f"transpose_vt=dir={direction},")
    assert "scale_vt=w=1080:h=1920:" in vf and "color_transfer=bt709" in vf
    assert cmd[cmd.index("-c:v") + 1] == "h264_videotoolbox"
    assert "-colorspace" not in cmd  # breaks the hardware filter chain


def test_hw_command_unrotated_has_explicit_size():
    vf = _vf(_cmd(_sdr(width=640, height=360)))
    assert "transpose_vt" not in vf
    assert "scale_vt=w=640:h=360:" in vf


def test_hw_tier_skipped_for_180_degrees():
    assert tiers_for(_sdr(rotation=180), ALL_CAPS) == [TIER_SW, TIER_X264]
    assert tiers_for(_sdr(rotation=90), ALL_CAPS) == [TIER_HW, TIER_SW, TIER_X264]
    assert tiers_for(_sdr(), X264_CAPS) == [TIER_X264]
    assert tiers_for(_sdr(), Capabilities()) == []


@pytest.mark.parametrize("hdr", [dict(), dict(transfer="smpte2084"), dict(transfer="bt709", dolby_vision=True),
                                 dict(transfer="")])  # untagged 10-bit BT.2020
def test_hdr_never_uses_scale_vt(hdr):
    # scale_vt's color_* options only re-tag HDR frames; they don't tone-map
    assert tiers_for(_probe(**hdr), ALL_CAPS) == [TIER_SW, TIER_X264]


def test_untagged_8bit_or_bt709_is_not_hdr():
    assert not _probe(transfer="", pix_fmt="yuv420p").hdr
    assert not _probe(transfer="", primaries="bt709").hdr


@pytest.mark.parametrize("transfer, curve", [("arib-std-b67", "hlg"), ("smpte2084", "pq"), ("", "hlg")])
def test_sw_command_hdr_tonemaps_with_lut(transfer, curve):
    cmd = _cmd(_probe(transfer=transfer), caps=LUT_CAPS, tier=TIER_SW)
    assert cmd[cmd.index("-hwaccel") + 1] == "videotoolbox"   # hardware decode, CPU filters
    assert "-hwaccel_output_format" not in cmd
    assert _vf(cmd) == ("scale=1920:1080:in_color_matrix=bt2020,format=gbrp16le,"
                        f"lut3d=file={curve}-v{transcode.LUT_VERSION}-{transcode.LUT_SIZE}.cube:interp=tetrahedral,"
                        "setparams=color_primaries=bt709:color_trc=bt709:colorspace=gbr,"
                        "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p")
    assert cmd[cmd.index("-colorspace") + 1] == "bt709"


def test_sw_command_hdr_without_lut3d_or_zscale_uses_colorspace():
    cmd = _cmd(_probe(), caps=SW_CAPS, tier=TIER_SW)
    assert _vf(cmd) == "colorspace=all=bt709:iall=bt2020:itrc=bt709:format=yuv420p,scale=1920:1080,format=yuv420p"


def test_sw_command_hdr_with_zscale_tonemaps():
    vf = _vf(_cmd(_probe(), caps=ZSCALE_CAPS, tier=TIER_X264))
    assert "tonemap=hable" in vf and vf.index("zscale=t=linear") < vf.index("tonemap")


def test_sw_command_sdr_has_no_tonemap():
    vf = _vf(_cmd(_probe(transfer="bt709", pix_fmt="yuv420p"), caps=ZSCALE_CAPS, tier=TIER_X264))
    assert vf == "scale=1920:1080,format=yuv420p"


def test_x264_command():
    cmd = _cmd(_probe(), caps=X264_CAPS, tier=TIER_X264)
    i = cmd.index("-c:v")
    assert cmd[i:i + 6] == ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]


def test_common_output_flags():
    cmd = _cmd(_probe())
    for flag, value in [("-movflags", "+faststart"), ("-tag:v", "avc1"), ("-color_trc", "bt709"),
                        ("-map", "0:v:0"), ("-f", "mp4")]:
        assert cmd[cmd.index(flag) + 1] == value
    assert "-an" in cmd
    assert cmd[-1] == "/out.tmp.mp4"
    assert "0" not in [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]  # never -map 0


@pytest.mark.parametrize("codec, expected", [("aac", ["-c:a", "copy"]), ("alac", ["-c:a", "aac"])])
def test_audio_mapping(codec, expected):
    cmd = _cmd(_probe(audio_index=2, audio_codec=codec))
    assert cmd[cmd.index("-map", cmd.index("-map") + 1) + 1] == "0:2"
    i = cmd.index("-c:a")
    assert cmd[i:i + 2] == expected


def test_fps_capped_at_60():
    assert _vf(_cmd(_probe(fps=240))).endswith(",fps=60")
    assert "fps=" not in _vf(_cmd(_probe(fps=59.94)))


def _stream(**kw):
    return {"index": 0, "codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv420p10le",
            "width": 3840, "height": 2160, "avg_frame_rate": "30000/1001", "duration": "4.5", **kw}


def test_parse_probe_iphone_streams():
    data = {"streams": [
        _stream(color_transfer="arib-std-b67", side_data_list=[{"side_data_type": "Display Matrix", "rotation": -90}]),
        {"index": 1, "codec_type": "audio", "codec_name": "apac"},   # spatial audio: not decodable
        {"index": 2, "codec_type": "audio", "codec_name": "aac"},
        {"index": 3, "codec_type": "data", "codec_name": "none"},
    ]}
    p = parse_probe(data)
    assert (p.codec, p.rotation, p.audio_index, p.audio_codec) == ("hevc", -90, 2, "aac")
    assert p.hdr and p.bit_depth == 10
    assert p.fps == pytest.approx(29.97, abs=0.01)
    assert p.duration == 4.5


def test_parse_probe_legacy_rotate_tag_and_dolby_vision():
    p = parse_probe({"streams": [_stream(codec_tag_string="dvh1", tags={"rotate": "90"})]})
    assert p.rotation == -90 and p.dolby_vision and p.hdr and p.audio_index is None
    assert parse_probe({"streams": [{"codec_type": "audio", "codec_name": "aac", "index": 0}]}) is None


# --- ffmpeg discovery ---

def _fake_tool(d: Path, name: str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("#!/bin/sh\n")
    p.chmod(0o755)
    return p


def test_find_tool_env_override(monkeypatch, tmp_path):
    tool = _fake_tool(tmp_path, "my-ffmpeg")
    monkeypatch.setenv("PICME_FFMPEG", str(tool))
    assert transcode.find_tool("ffmpeg") == str(tool)
    monkeypatch.setenv("PICME_FFMPEG", str(tmp_path / "missing"))
    assert transcode.find_tool("ffmpeg") is None


@pytest.mark.skipif(sys.platform == "win32", reason="Homebrew fallback is POSIX only")
def test_find_tool_homebrew_fallback_when_not_on_path(monkeypatch, tmp_path):
    monkeypatch.delenv("PICME_FFMPEG", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(transcode, "_HOMEBREW_DIRS", ())
    assert transcode.find_tool("ffmpeg") is None
    brew = tmp_path / "brew" / "bin"
    tool = _fake_tool(brew, "ffmpeg")
    monkeypatch.setattr(transcode, "_HOMEBREW_DIRS", (str(brew),))
    assert transcode.find_tool("ffmpeg") == str(tool)


def test_find_tool_prefers_path(monkeypatch, tmp_path):
    monkeypatch.delenv("PICME_FFPROBE", raising=False)
    tool = _fake_tool(tmp_path / "bin", "ffprobe")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert transcode.find_tool("ffprobe") == str(tool)


@requires_ffmpeg
def test_ffmpeg_installed_while_running_is_picked_up(monkeypatch, video_transcoder):
    real = transcode.find_tool
    monkeypatch.setattr(transcode, "find_tool", lambda name: None)
    assert video_transcoder.available is False
    monkeypatch.setattr(transcode, "find_tool", real)
    assert video_transcoder.available is False      # re-checked at most every 10 s
    video_transcoder._tools_checked -= 11
    assert video_transcoder.available is True


@requires_ffmpeg
def test_capabilities_parses_real_ffmpeg():
    caps = transcode.capabilities(FFMPEG)
    assert "scale" in caps.filters and "colorspace" in caps.filters
    assert caps.encoders & {"libx264", "h264_videotoolbox"}
    assert caps.version.startswith("ffmpeg version")


# --- Live Photos & cache key ---

@requires_libx265
def test_live_photo_companion_needs_still_and_short_duration(lib, video_transcoder):
    make_clip(lib / "IMG_0001.MOV", codec="hevc", duration=2)
    (lib / "IMG_0001.HEIC").write_bytes(b"x")   # pairing only looks at names
    make_clip(lib / "IMG_0002.MOV", codec="hevc", duration=6)
    (lib / "img_0002.jpg").write_bytes(b"x")     # long clip that shares a photo's name
    make_clip(lib / "IMG_0003.MOV", codec="hevc", duration=2)   # short clip, no photo
    states = {c.path.name: c.state for c in video_transcoder.scan(lib)}
    assert states == {"IMG_0001.MOV": transcode.LIVE_PHOTO, "IMG_0002.MOV": transcode.PENDING,
                      "IMG_0003.MOV": transcode.PENDING}
    # Companions aren't queued — no wasted conversions
    assert {p.name for p in video_transcoder._pending} == {"IMG_0002.MOV", "IMG_0003.MOV"}

    video_transcoder.configure({"include_live_photos": True})
    assert video_transcoder.clip(lib / "IMG_0001.MOV").state == transcode.PENDING
    assert all(c.state == transcode.PENDING for c in video_transcoder.scan(lib))


def test_cache_key_changes_with_mtime_size_cap_and_version(tmp_path, monkeypatch):
    p = tmp_path / "a.mov"
    p.write_bytes(b"1234")
    k = transcode.cache_key(p, 1920)
    assert transcode.cache_key(p, 1920) == k
    assert transcode.cache_key(p, 1280) != k
    monkeypatch.setattr(transcode, "PIPELINE_VERSION", transcode.PIPELINE_VERSION + 1)
    assert transcode.cache_key(p, 1920) != k
    monkeypatch.undo()
    os.utime(p, ns=(1, 1))
    assert transcode.cache_key(p, 1920) != k


# --- Real conversions ---

@requires_libx265
def test_hlg_hevc_converted_to_sdr_h264_faststart(lib, video_transcoder, video_cache):
    src = make_clip(lib / "IMG_0100.MOV", hdr="hlg", size=(320, 180), audio="aac", duration=1)
    before_sha, before_mtime = sha256(src), src.stat().st_mtime_ns
    before_files = sorted(lib.iterdir())

    assert video_transcoder.clip(src).state == transcode.PENDING
    video_transcoder.run_until_idle()
    out = converted(src)

    assert out.parent == video_cache and out.suffix == ".mp4"
    v = video_stream(out)
    assert (v["codec_name"], v["pix_fmt"], v.get("color_transfer")) == ("h264", "yuv420p", "bt709")
    assert (v["width"], v["height"]) == (320, 180)
    assert [s["codec_name"] for s in ffprobe_streams(out) if s["codec_type"] == "audio"] == ["aac"]
    with open(out, "rb") as f:
        top = [t for t, _, _ in _iter_boxes(f, 0, out.stat().st_size)]
    assert top.index(b"moov") < top.index(b"mdat")   # +faststart: playable while downloading
    # Originals are only read; nothing is written next to them
    assert sha256(src) == before_sha and src.stat().st_mtime_ns == before_mtime
    assert sorted(lib.iterdir()) == before_files
    meta = json.loads(out.with_suffix(".json").read_text())
    assert meta["source"] == str(src.resolve()) and meta["tier"] in (TIER_HW, TIER_SW, TIER_X264)
    assert not list(video_cache.glob("*.tmp.mp4"))


# HDR test cards: BT.2020 R'G'B' code values → SDR BT.709 sRGB expected from an independent
# numpy implementation of BT.2100 (HLG OOTF at 1000 nits / PQ EOTF) → BT.709 gamut → 203-nit
# reference white → extended-Reinhard roll-off to a 1000-nit peak → gamma 2.4 (QA's reference).
# "naive" is what you get by showing the code values unconverted, which is what scale_vt's
# re-tagging produced.
HDR_CARDS = {
    "hlg": {
        "patches": [(0.75, 0.75, 0.75), (0.5, 0.5, 0.5), (0.95, 0.95, 0.95), (0.7, 0.4, 0.35),
                    (0.45, 0.68, 0.45), (0.4, 0.45, 0.72), (0.72, 0.58, 0.5), (0.2, 0.2, 0.2)],
        "expected": [(194, 194, 194), (131, 131, 131), (243, 243, 243), (225, 90, 93),
                     (0, 185, 108), (88, 120, 206), (220, 140, 124), (57, 57, 57)],
    },
    "pq": {
        "patches": [(0.58, 0.58, 0.58), (0.45, 0.45, 0.45), (0.70, 0.70, 0.70), (0.62, 0.42, 0.38),
                    (0.45, 0.6, 0.45), (0.42, 0.45, 0.62), (0.6, 0.52, 0.48), (0.25, 0.25, 0.25)],
        "expected": [(194, 194, 194), (135, 135, 135), (238, 238, 238), (255, 58, 82),
                     (0, 220, 96), (72, 133, 255), (254, 151, 131), (55, 55, 55)],
    },
}


def _saturation(rgb) -> float:
    return (max(rgb) - min(rgb)) / max(max(rgb), 1)


@requires_libx265
@pytest.mark.parametrize("pipeline", ["default", "libx264"])
@pytest.mark.parametrize("curve", HDR_CARDS)
def test_hdr_pixels_tone_mapped_to_sdr(lib, video_transcoder, monkeypatch, curve, pipeline):
    """Actual pixel values, not just colour tags: HDR must be converted, not re-labelled."""
    if pipeline == "libx264":
        _caps_without(monkeypatch, filters=("scale_vt", "transpose_vt"), encoders=("h264_videotoolbox",))
    card = HDR_CARDS[curve]
    size = (320, 180)
    src = make_hdr_card(lib / f"card_{curve}.MOV", curve, card["patches"], size)
    video_transcoder.configure({"video_max_edge": 0})
    video_transcoder.clip(src)
    video_transcoder.run_until_idle()
    out = converted(src)
    got = [pixel_at(out, x, y) for x, y in patch_centers(len(card["patches"]), size)]
    errors = [sum(abs(a - b) for a, b in zip(g, e)) / 3 for g, e in zip(got, card["expected"])]
    assert sum(errors) / len(errors) <= 4, list(zip(got, card["expected"]))
    assert max(errors) <= 10, list(zip(got, card["expected"]))
    # Colours keep their saturation (washed-out output is the classic HDR→SDR failure)
    for g, e in zip(got[3:7], card["expected"][3:7]):
        assert _saturation(g) >= 0.85 * _saturation(e), (g, e)


@requires_libx265
def test_hdr_lut_written_once_to_cache(lib, video_transcoder, video_cache):
    for i in range(2):
        src = make_hdr_card(lib / f"c{i}.MOV", "hlg", [(0.5, 0.5, 0.5)], (64, 36))
        video_transcoder.clip(src)
    video_transcoder.run_until_idle()
    luts = list((video_cache / "luts").iterdir())
    assert [p.name for p in luts] == [f"hlg-v{transcode.LUT_VERSION}-{transcode.LUT_SIZE}.cube"]
    assert luts[0].read_text().splitlines()[0] == f"LUT_3D_SIZE {transcode.LUT_SIZE}"
    assert not list(lib.glob("*.cube"))   # nothing next to the originals


def _caps_without(monkeypatch, *, filters=(), encoders=()):
    real = transcode.capabilities

    def limited(ffmpeg):
        c = real(ffmpeg)
        return Capabilities(c.version, c.encoders - set(encoders), c.filters - set(filters))

    monkeypatch.setattr(transcode, "capabilities", limited)


PIPELINES = {
    "default": {},  # VideoToolbox on macOS
    "software": {"filters": ("scale_vt", "transpose_vt")},
    "libx264": {"filters": ("scale_vt", "transpose_vt"), "encoders": ("h264_videotoolbox",)},
}


@requires_libx265
@pytest.mark.parametrize("pipeline", PIPELINES)
@pytest.mark.parametrize("rotation, top_left", [(90, GREEN), (-90, BLUE), (0, RED)])
def test_rotated_clip_comes_out_upright(lib, video_transcoder, monkeypatch, pipeline, rotation, top_left):
    _caps_without(monkeypatch, **PIPELINES[pipeline])
    src = make_clip(lib / "IMG_0200.MOV", hdr="hlg", size=(320, 180), rotation=rotation, quadrants=True)
    video_transcoder.clip(src)
    video_transcoder.run_until_idle()
    out = converted(src)
    v = video_stream(out)
    expected_size = (320, 180) if rotation == 0 else (180, 320)
    assert (v["width"], v["height"]) == expected_size
    assert not any("rotation" in sd for sd in v.get("side_data_list", []))  # baked in, not re-applied
    assert close(corner_pixel(out, "tl"), top_left), corner_pixel(out, "tl")
    if pipeline != "default":
        assert json.loads(out.with_suffix(".json").read_text())["tier"] == \
            (TIER_SW if pipeline == "software" else TIER_X264)


@requires_ffmpeg
def test_h264_over_cap_is_downscaled_and_small_clips_never_upscaled(lib, video_transcoder):
    video_transcoder.configure({"video_max_edge": 160})
    big = make_clip(lib / "big.mp4", size=(320, 240))
    small = make_clip(lib / "small.mp4", size=(128, 96))
    states = {c.path.name: c.state for c in video_transcoder.scan(lib)}
    assert states == {"big.mp4": transcode.PENDING, "small.mp4": transcode.READY}
    assert video_transcoder.clip(small).serve == small   # as-is: the original is streamed
    video_transcoder.run_until_idle()
    v = video_stream(converted(big))
    assert (v["width"], v["height"]) == (160, 120)


@requires_ffmpeg
def test_high_fps_capped(lib, video_transcoder):
    video_transcoder.configure({"video_max_edge": 100})   # force a conversion
    src = make_clip(lib / "slomo.mp4", size=(160, 120), rate=120)
    video_transcoder.clip(src)
    video_transcoder.run_until_idle()
    assert video_stream(converted(src))["r_frame_rate"] == "60/1"


@requires_libx265
def test_failed_conversion_marked_bad_until_file_changes(lib, video_transcoder, video_cache):
    good = make_clip(tmp := lib / "tmp.mov", codec="hevc", faststart=True)
    data = good.read_bytes()
    broken = lib / "broken.mov"
    broken.write_bytes(data[:data.index(b"mdat") + 20])   # moov intact, frames missing
    tmp.unlink()

    assert video_transcoder.clip(broken).state == transcode.PENDING
    video_transcoder.run_until_idle()
    assert video_transcoder.clip(broken).state == transcode.FAILED
    assert len(list(video_cache.glob("*.bad"))) == 1
    assert not video_transcoder._pending   # not re-queued while it's unchanged

    broken.write_bytes(data)   # replaced with a good copy → new key → retried
    assert video_transcoder.clip(broken).state == transcode.PENDING
    video_transcoder.run_until_idle()
    assert video_transcoder.clip(broken).state == transcode.READY


def test_prune_removes_outputs_of_changed_or_deleted_sources(tmp_path, video_cache, video_transcoder):
    keep, gone, edited = (tmp_path / n for n in ("keep.mov", "gone.mov", "edited.mov"))
    for p in (keep, gone, edited):
        p.write_bytes(b"x")
    video_cache.mkdir(parents=True, exist_ok=True)
    for name, p in (("k", keep), ("g", gone), ("e", edited)):
        st = p.stat()
        (video_cache / f"{name}.mp4").write_bytes(b"mp4")
        (video_cache / f"{name}.json").write_text(json.dumps(
            {"source": str(p), "mtime_ns": st.st_mtime_ns, "size": st.st_size,
             "version": transcode.PIPELINE_VERSION, "max_edge": transcode.DEFAULT_MAX_EDGE}))
    old_cap = json.loads((video_cache / "k.json").read_text()) | {"max_edge": 3840}
    (video_cache / "c.mp4").write_bytes(b"mp4")
    (video_cache / "c.json").write_text(json.dumps(old_cap))
    gone.unlink()
    edited.write_bytes(b"longer")
    (video_cache / "x.tmp.mp4").write_bytes(b"half")

    video_transcoder.cleanup_tmp()
    video_transcoder.prune()
    assert sorted(f.name for f in video_cache.iterdir()) == ["k.json", "k.mp4"]


_HARD_KILL_CHILD = """
import sys, time
sys.path.insert(0, sys.argv[1])
import transcode
t = transcode.Transcoder()
t.configure({"video_max_edge": 100})
t.clip(sys.argv[2])
t.start()
deadline = time.monotonic() + 15
while not list(transcode.cache_dir().glob("ffmpeg-*.pid")) and time.monotonic() < deadline:
    time.sleep(0.05)
print("converting" if t._proc else "not converting", flush=True)
time.sleep(60)
"""


@requires_ffmpeg
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_hard_killed_picme_leaves_no_orphan_ffmpeg(lib, video_transcoder, video_cache, tmp_path, monkeypatch):
    import signal
    import subprocess

    from conftest import REPO_ROOT
    # -re makes ffmpeg read at real-time speed, so the 20 s conversion is still running when killed
    wrapper = tmp_path / "ffmpeg"
    wrapper.write_text(f'#!/bin/sh\nexec "{FFMPEG}" -re "$@"\n')
    wrapper.chmod(0o755)
    monkeypatch.setenv("PICME_FFMPEG", str(wrapper))
    src = make_clip(lib / "long.mp4", duration=20)
    meta = {}
    child = subprocess.Popen([sys.executable, "-c", _HARD_KILL_CHILD, str(REPO_ROOT / "backend"), str(src)],
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "converting"
        (pidfile,) = video_cache.glob("ffmpeg-*.pid")
        meta = json.loads(pidfile.read_text())
        assert meta["owner"] == child.pid
        (old_tmp,) = video_cache.glob("*.tmp.mp4")
        assert str(child.pid) in old_tmp.name   # per-process tmp name
        child.send_signal(signal.SIGKILL)       # no shutdown hook runs
        child.wait(10)
        time.sleep(0.5)
        orphan = meta["ffmpeg"]
        assert transcode._pid_alive(orphan) and transcode._is_ffmpeg(orphan)   # still running
        live_other = video_cache / f"other.{os.getppid()}.tmp.mp4"   # a PicMe that's still alive
        live_other.write_bytes(b"in progress")

        video_transcoder.start()   # the next PicMe starting up
        deadline = time.monotonic() + 10
        while transcode._pid_alive(orphan) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not transcode._pid_alive(orphan)
        assert not old_tmp.exists() and not pidfile.exists()
        assert live_other.exists()
    finally:
        child.kill()
        if transcode._pid_alive(meta.get("ffmpeg", 0)):
            os.kill(meta["ffmpeg"], signal.SIGKILL)


def test_tmp_cleanup_keeps_outputs_of_running_picme(video_transcoder, video_cache):
    video_cache.mkdir(parents=True, exist_ok=True)
    mine = video_cache / f"a.{os.getpid()}.tmp.mp4"
    legacy, dead = video_cache / "b.tmp.mp4", video_cache / "c.999999999.tmp.mp4"
    for f in (mine, legacy, dead):
        f.write_bytes(b"x")
    (video_cache / "ffmpeg-999999999.pid").write_text(json.dumps({"owner": 999999999, "ffmpeg": os.getpid()}))
    video_transcoder.cleanup_tmp()
    assert mine.exists() and not legacy.exists() and not dead.exists()
    assert not (video_cache / "ffmpeg-999999999.pid").exists()   # and it didn't kill us (not ffmpeg)


@requires_libx265
def test_progress_and_shortest_first(lib, video_transcoder, monkeypatch):
    long_clip = make_clip(lib / "long.mov", codec="hevc", duration=3)
    short_clip = make_clip(lib / "short.mov", codec="hevc", duration=1)
    order, percents = [], []
    real_run = video_transcoder._run

    def spy(cmd, duration, cwd=None):
        order.append(Path(video_transcoder._current["src"]).name)
        ok, err = real_run(cmd, duration, cwd)
        percents.append(video_transcoder._current["percent"])
        return ok, err

    monkeypatch.setattr(video_transcoder, "_run", spy)
    video_transcoder.clip(long_clip)
    video_transcoder.clip(short_clip)
    video_transcoder.run_until_idle()
    assert order == ["short.mov", "long.mov"]
    assert all(p > 50 for p in percents)


@requires_libx265
def test_background_worker_converts_and_stop_kills_ffmpeg(lib, video_transcoder):
    src = make_clip(lib / "IMG_0300.MOV", codec="hevc", duration=1)
    video_transcoder.start()
    video_transcoder.clip(src)
    deadline = time.monotonic() + 60
    while video_transcoder.clip(src).state != transcode.READY:
        assert time.monotonic() < deadline, "worker never converted the clip"
        time.sleep(0.1)
    video_transcoder.stop()
    assert video_transcoder._thread is None


# --- API ---

@requires_libx265
def test_api_lists_only_ready_clips_and_serves_converted_copy(client, lib, video_transcoder):
    h264 = make_clip(lib / "plain.mp4", duration=1)
    hevc = make_clip(lib / "IMG_0400.MOV", codec="hevc", duration=1)
    make_clip(lib / "IMG_0401.MOV", codec="hevc", duration=2)
    (lib / "IMG_0401.HEIC").write_bytes(b"x")

    r = client.get("/api/images/list", params={"folder": str(lib), "kind": "video"}).json()
    assert r == {"images": [str(h264)], "count": 1, "pending": 1}
    assert client.get("/api/video", params={"path": str(hevc)}).status_code == 409

    status = client.get("/api/videos/status", params={"folder": str(lib)}).json()
    assert status == {"ffmpeg": True, "total": 2, "ready": 1, "pending": 1, "failed": 0,
                      "skipped_no_ffmpeg": 0, "live_photos_hidden": 1, "converting": None}

    video_transcoder.run_until_idle()
    r = client.get("/api/images/list", params={"folder": str(lib), "kind": "video"}).json()
    assert sorted(r["images"]) == sorted([str(h264), str(hevc)]) and r["pending"] == 0
    full = client.get("/api/video", params={"path": str(hevc)})
    assert full.status_code == 200 and full.headers["content-type"] == "video/mp4"
    assert full.content == converted(hevc).read_bytes() != hevc.read_bytes()
    part = client.get("/api/video", params={"path": str(hevc)}, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100
    # H.264 is streamed untouched
    assert client.get("/api/video", params={"path": str(h264)}).content == h264.read_bytes()
    assert client.get("/api/videos/status", params={"folder": str(lib)}).json()["ready"] == 2


@requires_libx265
def test_api_failed_clip_is_415(client, lib, video_transcoder):
    data = make_clip(lib / "tmp.mov", codec="hevc", faststart=True).read_bytes()
    (lib / "tmp.mov").unlink()
    broken = lib / "broken.mov"
    broken.write_bytes(data[:data.index(b"mdat") + 20])
    client.get("/api/images/list", params={"folder": str(lib), "kind": "video"})
    video_transcoder.run_until_idle()
    assert client.get("/api/video", params={"path": str(broken)}).status_code == 415
    assert client.get("/api/videos/status", params={"folder": str(lib)}).json()["failed"] == 1


@requires_ffmpeg
def test_api_truncated_clip_without_moov_is_probed_and_failed(client, lib, video_transcoder, video_cache):
    data = make_clip(lib / "tmp.mov").read_bytes()   # moov after mdat (no faststart)
    (lib / "tmp.mov").unlink()
    broken = lib / "IMG_0600.MOV"
    broken.write_bytes(data[:data.index(b"moov") - 4])   # cut before the moov box
    assert sniff_video(broken) is None
    r = client.get("/api/images/list", params={"folder": str(lib), "kind": "video"}).json()
    assert (r["count"], r["pending"]) == (0, 1)   # not served raw while unknown
    video_transcoder.run_until_idle()
    assert client.get("/api/video", params={"path": str(broken)}).status_code == 415
    status = client.get("/api/videos/status", params={"folder": str(lib)}).json()
    assert (status["ready"], status["failed"]) == (0, 1)
    assert len(list(video_cache.glob("*.bad"))) == 1
    assert client.get("/api/images/list", params={"folder": str(lib), "kind": "video"}).json()["count"] == 0


@requires_ffmpeg
def test_unreadable_container_passes_through_without_ffmpeg(client, lib, monkeypatch):
    monkeypatch.setattr(transcode, "find_tool", lambda name: None)
    odd = lib / "odd.mp4"
    odd.write_bytes(b"\x00" * 64)
    r = client.get("/api/images/list", params={"folder": str(lib), "kind": "video"}).json()
    assert r["images"] == [str(odd)]   # milestone-4 behaviour: the browser tries, the display skips


@requires_libx265
def test_api_without_ffmpeg(client, lib, monkeypatch):
    monkeypatch.setattr(transcode, "find_tool", lambda name: None)
    h264 = make_clip(lib / "plain.mp4")
    hevc = make_clip(lib / "IMG_0500.MOV", codec="hevc")
    r = client.get("/api/images/list", params={"folder": str(lib), "kind": "video"}).json()
    assert r == {"images": [str(h264)], "count": 1, "pending": 0}
    assert client.get("/api/video", params={"path": str(h264)}).status_code == 200
    assert client.get("/api/video", params={"path": str(hevc)}).status_code == 415
    status = client.get("/api/videos/status", params={"folder": str(lib)}).json()
    assert status["ffmpeg"] is False and status["skipped_no_ffmpeg"] == 1
    assert "brew install ffmpeg" in status["hint"] and "winget install Gyan.FFmpeg" in status["hint"]
    assert not transcode.transcoder._pending


@requires_libx265
def test_api_without_ffmpeg_still_plays_h264_that_would_only_be_resized(client, lib, monkeypatch):
    """Without ffmpeg, H.264 over the size cap or with non-AAC audio plays as-is (as it did
    before conversion existed); only codecs browsers can't decode are skipped."""
    monkeypatch.setattr(transcode, "find_tool", lambda name: None)
    transcode.transcoder.configure({"video_max_edge": 160})
    big = make_clip(lib / "big.mp4", size=(320, 240))
    alac = make_clip(lib / "alac.mov", audio="alac")
    hevc = make_clip(lib / "IMG_0501.MOV", codec="hevc")
    assert transcode.needs_transcode(sniff_video(big), 160) and transcode.needs_transcode(sniff_video(alac), 160)
    r = client.get("/api/images/list", params={"folder": str(lib), "kind": "video"}).json()
    assert sorted(r["images"]) == sorted([str(big), str(alac)]) and r["pending"] == 0
    for clip in (big, alac):
        resp = client.get("/api/video", params={"path": str(clip)})
        assert resp.status_code == 200 and resp.content == clip.read_bytes()   # the original
    assert client.get("/api/video", params={"path": str(hevc)}).status_code == 415
    status = client.get("/api/videos/status", params={"folder": str(lib)}).json()
    assert (status["ready"], status["skipped_no_ffmpeg"]) == (2, 1)


@requires_ffmpeg
def test_h264_whose_conversion_failed_plays_as_is(lib, video_transcoder, video_cache):
    video_transcoder.configure({"video_max_edge": 160})
    big = make_clip(lib / "big.mp4", size=(320, 240))
    key = transcode.cache_key(big.resolve(), 160)
    (transcode.cache_dir() / f"{key}.bad").write_text("boom")
    clip = video_transcoder.clip(big)
    assert (clip.state, clip.serve) == (transcode.READY, big)
    assert not video_transcoder._pending


def test_app_starts_without_ffmpeg(config_path, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import main
    monkeypatch.setattr(transcode, "find_tool", lambda name: None)
    monkeypatch.setenv("PICME_TRANSCODE_AUTOSTART", "1")
    config_path.write_text(json.dumps({"source_path": str(tmp_path), "slots": [{"id": 1, "mode": "video"}]}))
    with TestClient(main.app) as c:
        assert c.get("/api/videos/status").json()["ffmpeg"] is False


@requires_libx265
def test_api_config_settings_apply(client, lib, video_transcoder):
    make_clip(lib / "IMG_0600.MOV", codec="hevc", duration=1)
    (lib / "IMG_0600.JPG").write_bytes(b"x")
    big = make_clip(lib / "big.mp4", size=(320, 240))
    assert client.get("/api/images/list", params={"folder": str(lib), "kind": "video"}).json()["images"] == [str(big)]

    cfg = {"source_path": str(lib), "video_max_edge": 160, "include_live_photos": True,
           "slots": [{"id": 1, "mode": "video", "use_global": {"source": True}}]}
    assert client.post("/api/config", json=cfg).status_code == 200
    r = client.get("/api/images/list", params={"folder": str(lib), "kind": "video"}).json()
    assert r["count"] == 0 and r["pending"] == 2   # Live Photo shown, 320px clip over the cap
    video_transcoder.run_until_idle()
    assert client.get("/api/images/list", params={"folder": str(lib), "kind": "video"}).json()["count"] == 2
    assert video_stream(converted(big))["width"] == 160


@requires_libx265
def test_startup_converts_video_slot_folders_in_background(config_path, lib, monkeypatch):
    from fastapi.testclient import TestClient

    import main
    hevc = make_clip(lib / "IMG_0700.MOV", codec="hevc", duration=1)
    photos_only = lib.parent / "photos"
    photos_only.mkdir()
    other = make_clip(photos_only / "IMG_0701.MOV", codec="hevc", duration=1)
    config_path.write_text(json.dumps({
        "source_path": str(photos_only),
        "slots": [{"id": 1, "mode": "video", "source_path": str(lib), "use_global": {"source": False}},
                  {"id": 2, "mode": "photos", "source_path": str(photos_only)}],
    }))
    monkeypatch.setenv("PICME_TRANSCODE_AUTOSTART", "1")
    with TestClient(main.app) as c:
        deadline = time.monotonic() + 60
        while c.get("/api/video", params={"path": str(hevc)}).status_code != 200:
            assert time.monotonic() < deadline, "startup didn't queue the video slot's folder"
            time.sleep(0.2)
        # The photo slot's folder isn't scanned
        assert other.resolve() not in transcode.transcoder._pending
        key = transcode.cache_key(other.resolve(), transcode.DEFAULT_MAX_EDGE)
        assert not (transcode.cache_dir() / f"{key}.mp4").exists()
    assert transcode.transcoder._thread is None   # shutdown stopped the worker


def test_symlink_named_mov_to_text_file_still_rejected(client, lib):
    secret = lib / "secret.txt"
    secret.write_text("nope")
    link = lib / "link.mov"
    link.symlink_to(secret)
    assert client.get("/api/video", params={"path": str(link)}).status_code == 400
