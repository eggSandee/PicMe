"""Background HEVC → H.264 transcoding for video slots.

iPhones record HEVC (often 10-bit HDR) that Chrome and TV browsers can't decode. Clips the
browser can't play as-is are converted, one at a time at low priority, to an H.264 yuv420p
MP4 (SDR, rotation baked in, long edge capped) in the OS cache dir. /api/video serves the
cached copy in place of the original; originals are only ever read.

Until a clip is converted it's left out of /api/images/list, so the display never waits on
it. Without ffmpeg PicMe still runs: H.264 clips play, everything else is skipped.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from cache import cache_subdir
from heic import HEIC_EXTENSIONS
from metadata.video import VideoSniff, sniff_video

log = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
PIPELINE_VERSION = 1      # bump when the ffmpeg recipe changes so stale cache entries are ignored
DEFAULT_MAX_EDGE = 1920   # long-edge cap for converted (and too-large H.264) clips; 0 = no cap
MAX_FPS = 60              # slo-mo clips are 120/240 fps; TV decoders choke on several of those
LIVE_PHOTO_MAX_SECONDS = 4.0
LIVE_PHOTO_STILL_EXTENSIONS = HEIC_EXTENSIONS | {".jpg", ".jpeg"}
PROBE_TIMEOUT_S = 30
INSTALL_HINT = "brew install ffmpeg (macOS) / winget install Gyan.FFmpeg (Windows)"

# Plays in every browser we target without conversion
_PLAYABLE_VIDEO = {"avc1", "avc3"}
_PLAYABLE_AUDIO = {"mp4a"}
_8BIT_420_PROFILES = {66, 77, 88, 100}   # Baseline/Main/Extended/High (not High 10/4:2:2/4:4:4)
_HDR_TRANSFERS = {16, 18}                # SMPTE ST 2084 (PQ), ARIB STD-B67 (HLG)
# Audio ffmpeg can decode and re-encode to AAC. iPhone 16 spatial audio (apac) isn't one.
_DECODABLE_AUDIO = {"aac", "mp3", "alac", "ac3", "eac3", "opus", "flac", "vorbis"}

_HOMEBREW_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


# --- ffmpeg discovery ---

def find_tool(name: str) -> str | None:
    """Locate ffmpeg/ffprobe: PICME_FFMPEG / PICME_FFPROBE, then PATH, then Homebrew's
    dirs (apps started by launchd or a login item don't get Homebrew on PATH)."""
    override = os.environ.get(f"PICME_{name.upper()}")
    if override:
        return override if Path(override).is_file() else None
    found = shutil.which(name)
    if found:
        return found
    if sys.platform != "win32":
        for d in _HOMEBREW_DIRS:
            candidate = Path(d) / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


@dataclass(frozen=True)
class Capabilities:
    version: str = ""
    encoders: frozenset[str] = frozenset()
    filters: frozenset[str] = frozenset()

    @property
    def hw_pipeline(self) -> bool:
        """VideoToolbox decode → scale_vt (tone-map) → h264_videotoolbox, all on the GPU."""
        return {"scale_vt", "transpose_vt"} <= self.filters and "h264_videotoolbox" in self.encoders


def _list_names(ffmpeg: str, what: str) -> frozenset[str]:
    out = subprocess.run([ffmpeg, "-hide_banner", what], capture_output=True, text=True,
                         timeout=PROBE_TIMEOUT_S).stdout
    # Lines look like " V....D libx264   description" / " ... scale_vt   V->V   description"
    return frozenset(parts[1] for line in out.splitlines()
                     if len(parts := line.split()) >= 2 and re.fullmatch(r"[A-Z.|]{2,}", parts[0]))


@lru_cache(maxsize=4)
def capabilities(ffmpeg: str) -> Capabilities:
    try:
        version = subprocess.run([ffmpeg, "-hide_banner", "-version"], capture_output=True, text=True,
                                 timeout=PROBE_TIMEOUT_S).stdout.split("\n", 1)[0]
        return Capabilities(version, _list_names(ffmpeg, "-encoders"), _list_names(ffmpeg, "-filters"))
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("Could not query ffmpeg capabilities (%s): %s", ffmpeg, e)
        return Capabilities()


# --- ffprobe ---

@dataclass
class Probe:
    codec: str
    pix_fmt: str
    width: int
    height: int
    rotation: int = 0              # display-matrix rotation, counter-clockwise degrees
    transfer: str = ""
    primaries: str = ""
    fps: float = 30.0
    duration: float = 0.0
    audio_index: int | None = None  # first decodable audio stream
    audio_codec: str = ""
    dolby_vision: bool = False

    @property
    def hdr(self) -> bool:
        return self.transfer in ("arib-std-b67", "smpte2084") or self.dolby_vision

    @property
    def bit_depth(self) -> int:
        m = re.search(r"p(\d+)", self.pix_fmt)
        return int(m.group(1)) if m else 8


def _rate(value: str | None) -> float:
    try:
        num, _, den = (value or "").partition("/")
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def parse_probe(data: dict) -> Probe | None:
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if video is None:
        return None
    rotation = 0
    side = video.get("side_data_list") or []
    for sd in side:
        if "rotation" in sd:
            rotation = int(round(float(sd["rotation"])))
    if not rotation and (video.get("tags") or {}).get("rotate"):
        rotation = -int((video["tags"])["rotate"])  # legacy tag is clockwise
    audio = next((s for s in streams if s.get("codec_type") == "audio"
                  and s.get("codec_name") in _DECODABLE_AUDIO), None)
    try:
        duration = float(video.get("duration") or (data.get("format") or {}).get("duration") or 0)
    except ValueError:
        duration = 0.0
    return Probe(
        codec=video.get("codec_name", ""),
        pix_fmt=video.get("pix_fmt", ""),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        rotation=rotation,
        transfer=video.get("color_transfer", ""),
        primaries=video.get("color_primaries", ""),
        fps=_rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate")) or 30.0,
        duration=duration,
        audio_index=audio["index"] if audio else None,
        audio_codec=audio["codec_name"] if audio else "",
        dolby_vision=video.get("codec_tag_string") in ("dvh1", "dvhe")
        or any("DOVI" in (sd.get("side_data_type") or "") for sd in side),
    )


def probe(ffprobe: str, path: Path) -> Probe | None:
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_S)
        if out.returncode != 0:
            return None
        return parse_probe(json.loads(out.stdout))
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        log.warning("ffprobe failed on %s: %s", path, e)
        return None


# --- What needs converting, and how ---

def needs_transcode(sniff: VideoSniff, max_edge: int) -> bool:
    """False only for 8-bit SDR H.264 within the size cap with AAC (or no) audio."""
    return not (
        sniff.codec in _PLAYABLE_VIDEO
        and sniff.profile in _8BIT_420_PROFILES
        and sniff.transfer not in _HDR_TRANSFERS
        and (not max_edge or max(sniff.width, sniff.height) <= max_edge)
        and all(a in _PLAYABLE_AUDIO for a in sniff.audio)
    )


def output_size(width: int, height: int, rotation: int, max_edge: int) -> tuple[int, int]:
    """Displayed (post-rotation) size, long edge capped (never upscaled), both even."""
    if abs(rotation) % 180 == 90:
        width, height = height, width
    scale = min(1.0, max_edge / max(width, height)) if max_edge else 1.0

    def even(v: float) -> int:
        return max(2, int(round(v / 2)) * 2)

    return even(width * scale), even(height * scale)


def _transpose_dir(rotation: int) -> str | None:
    """transpose_vt direction matching ffmpeg's autorotate for a display-matrix rotation."""
    r = rotation % 360
    return {90: "cclock", 270: "clock"}.get(r)


TIER_HW = "videotoolbox"        # GPU decode + scale_vt tone-map + h264_videotoolbox
TIER_SW = "software"            # CPU decode/filters + h264_videotoolbox
TIER_X264 = "libx264"           # CPU decode/filters + libx264


def tiers_for(p: Probe, caps: Capabilities) -> list[str]:
    tiers = []
    if caps.hw_pipeline and p.rotation % 360 in (0, 90, 270):
        tiers.append(TIER_HW)
    if "h264_videotoolbox" in caps.encoders:
        tiers.append(TIER_SW)
    if "libx264" in caps.encoders:
        tiers.append(TIER_X264)
    return tiers


_approx_tonemap_logged = False


def _sw_tonemap(p: Probe, caps: Capabilities) -> list[str]:
    global _approx_tonemap_logged
    if not p.hdr:
        return []
    if "zscale" in caps.filters:
        return ["zscale=t=linear:npl=100", "format=gbrpf32le", "zscale=p=bt709",
                "tonemap=hable:desat=0", "zscale=t=bt709:m=bt709:r=tv"]
    if not _approx_tonemap_logged:
        _approx_tonemap_logged = True
        log.info("ffmpeg has no zscale filter — HDR clips are tone-mapped approximately in software")
    return ["colorspace=all=bt709:iall=bt2020:itrc=bt709:format=yuv420p"]


def build_command(ffmpeg: str, src: Path, dst: Path, p: Probe, caps: Capabilities, max_edge: int,
                  tier: str) -> list[str]:
    w, h = output_size(p.width, p.height, p.rotation, max_edge)
    fps_cap = p.fps > MAX_FPS + 0.5
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-y"]
    if tier == TIER_HW:
        # Hardware frames skip autorotate, so rotate explicitly. Explicit w/h, because
        # scale_vt=-2:1080 upscales small clips.
        cmd += ["-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld", "-i", str(src)]
        filters = []
        if (direction := _transpose_dir(p.rotation)):
            filters.append(f"transpose_vt=dir={direction}")
        filters.append(f"scale_vt=w={w}:h={h}:color_matrix=bt709:color_primaries=bt709:color_transfer=bt709")
    else:
        cmd += ["-i", str(src)]  # autorotate applies on this path
        filters = [*_sw_tonemap(p, caps), f"scale={w}:{h}", "format=yuv420p"]
    if fps_cap:
        filters.append(f"fps={MAX_FPS}")
    cmd += ["-map", "0:v:0", "-vf", ",".join(filters)]

    if tier == TIER_X264:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-profile:v", "high", "-threads", "2"]
    else:
        out_fps = min(p.fps or 30.0, MAX_FPS)
        bitrate = int(min(12e6, max(3e6, 0.1 * w * h * out_fps)))
        cmd += ["-c:v", "h264_videotoolbox", "-b:v", str(bitrate), "-profile:v", "high", "-allow_sw", "1"]

    if p.audio_index is None:
        cmd += ["-an"]
    else:
        cmd += ["-map", f"0:{p.audio_index}"]
        cmd += ["-c:a", "copy"] if p.audio_codec == "aac" else ["-c:a", "aac", "-b:a", "128k", "-ac", "2"]

    # Tag the output SDR. (-colorspace breaks the hardware filter chain; scale_vt sets the matrix.)
    cmd += ["-color_primaries", "bt709", "-color_trc", "bt709"]
    if tier != TIER_HW:
        cmd += ["-colorspace", "bt709"]
    cmd += ["-tag:v", "avc1", "-movflags", "+faststart", "-map_metadata", "0",
            "-progress", "pipe:1", "-nostats", "-f", "mp4", str(dst)]
    return cmd


# --- Live Photos ---

def is_live_photo_companion(path: Path, still_stems: set[str], sniff: VideoSniff | None) -> bool:
    """The short .MOV an iPhone saves next to a Live Photo's HEIC/JPG (same name, ~1.5–3 s).
    A long clip that happens to share a name with a photo is a real video."""
    return (path.stem.lower() in still_stems and sniff is not None and sniff.duration_s is not None
            and sniff.duration_s <= LIVE_PHOTO_MAX_SECONDS)


# --- Cache ---

def cache_dir() -> Path:
    return cache_subdir("video")


def cache_key(src: Path, max_edge: int) -> str:
    st = src.stat()
    raw = f"{src}|{st.st_mtime_ns}|{st.st_size}|v{PIPELINE_VERSION}|{max_edge}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# Per-clip state as seen by listing / serving
READY = "ready"            # plays as-is, or converted copy is cached
PENDING = "pending"        # queued or converting
FAILED = "failed"          # ffmpeg couldn't convert it
NO_FFMPEG = "no_ffmpeg"    # needs converting but ffmpeg isn't installed
LIVE_PHOTO = "live_photo"  # Live Photo companion, hidden


@dataclass
class Clip:
    path: Path              # as listed (not resolved)
    state: str
    serve: Path | None = None   # file to stream when READY
    duration: float = 0.0


@dataclass
class Settings:
    max_edge: int = DEFAULT_MAX_EDGE
    include_live_photos: bool = False


class Transcoder:
    """One worker thread, one ffmpeg at a time. Clips are queued shortest first, so the
    ready count climbs quickly on a fresh library."""

    def __init__(self) -> None:
        self.settings = Settings()
        self._cond = threading.Condition()
        self._pending: dict[Path, float] = {}   # resolved source -> duration (sort key)
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._proc: subprocess.Popen | None = None
        self._current: dict | None = None
        self._tools_checked = 0.0
        self._ffmpeg: str | None = None
        self._ffprobe: str | None = None
        self._warned_missing = False

    # -- configuration --

    def configure(self, config: dict) -> None:
        try:
            max_edge = int(config.get("video_max_edge", DEFAULT_MAX_EDGE))
        except (TypeError, ValueError):
            max_edge = DEFAULT_MAX_EDGE
        self.settings = Settings(max(0, max_edge), bool(config.get("include_live_photos", False)))

    def tools(self) -> tuple[str | None, str | None]:
        """(ffmpeg, ffprobe). Re-checked at most every 10 s while missing, so installing ffmpeg
        while PicMe runs is picked up without a restart."""
        if self._ffmpeg and self._ffprobe:
            return self._ffmpeg, self._ffprobe
        now = time.monotonic()
        if self._tools_checked and now - self._tools_checked < 10:
            return None, None
        self._tools_checked = now
        self._ffmpeg, self._ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
        if not (self._ffmpeg and self._ffprobe):
            self._ffmpeg = self._ffprobe = None
            if not self._warned_missing:
                self._warned_missing = True
                log.warning("ffmpeg not found — iPhone (HEVC) videos will be skipped. Install: %s", INSTALL_HINT)
            return None, None
        log.info("Video transcoding with %s (%s)", self._ffmpeg, capabilities(self._ffmpeg).version)
        return self._ffmpeg, self._ffprobe

    @property
    def available(self) -> bool:
        return self.tools()[0] is not None

    # -- classification --

    def scan(self, folder: str | Path, enqueue: bool = True) -> list[Clip]:
        """Every video in ``folder`` with its state; queues the ones that need converting."""
        try:
            files = [f for f in Path(folder).iterdir() if not f.name.startswith(".") and f.is_file()]
        except OSError:
            return []
        still_stems = {f.stem.lower() for f in files if f.suffix.lower() in LIVE_PHOTO_STILL_EXTENSIONS}
        return [self._classify(f, still_stems, enqueue) for f in files if f.suffix.lower() in VIDEO_EXTENSIONS]

    def clip(self, path: str | Path) -> Clip:
        """State of one video (for /api/video), queueing it if needed. Live Photo pairing
        doesn't apply here — a direct request gets whatever it asked for."""
        return self._classify(Path(path), set(), True)

    def _classify(self, f: Path, still_stems: set[str], enqueue: bool) -> Clip:
        sniff = sniff_video(f)
        duration = (sniff.duration_s or 0.0) if sniff else 0.0
        if not self.settings.include_live_photos and is_live_photo_companion(f, still_stems, sniff):
            return Clip(f, LIVE_PHOTO, duration=duration)
        # Not a container we can read (corrupt, or not really MP4/MOV): pass it through as
        # before and let the browser try — the display skips files it can't play
        if sniff is None or not needs_transcode(sniff, self.settings.max_edge):
            return Clip(f, READY, f, duration)
        try:
            src = f.resolve()
            key = cache_key(src, self.settings.max_edge)
            out_dir = cache_dir()
        except OSError as e:
            log.warning("Video cache unavailable for %s: %s", f, e)
            return Clip(f, FAILED, duration=duration)
        mp4 = out_dir / f"{key}.mp4"
        if mp4.exists():
            return Clip(f, READY, mp4, duration)
        if (out_dir / f"{key}.bad").exists():
            return Clip(f, FAILED, duration=duration)
        if not self.available:
            return Clip(f, NO_FFMPEG, duration=duration)
        if enqueue:
            self.enqueue(src, duration)
        return Clip(f, PENDING, duration=duration)

    # -- queue --

    def enqueue(self, src: Path, duration: float = 0.0) -> None:
        with self._cond:
            current = self._current and self._current.get("src")
            if src not in self._pending and src != current:
                self._pending[src] = duration
                self._cond.notify()

    def enqueue_folders(self, folders) -> None:
        """Scan folders in the background (listing many files shouldn't block a request)."""
        folders = [f for f in dict.fromkeys(folders) if f]
        if folders:
            threading.Thread(target=lambda: [self.scan(f) for f in folders],
                             name="picme-video-scan", daemon=True).start()

    def _next(self) -> Path | None:
        with self._cond:
            while not self._pending and not self._stopping:
                self._cond.wait()
            if self._stopping:
                return None
            src = min(self._pending, key=self._pending.get)
            del self._pending[src]
            self._current = {"src": src, "name": src.name, "percent": 0}
            return src

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stopping = False
        self.cleanup_tmp()
        self.prune()
        self._thread = threading.Thread(target=self._worker, name="picme-transcoder", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the worker and kill a running ffmpeg (uvicorn reload restarts the process)."""
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self._thread:
            self._thread.join(10)
        self._thread = None

    def _worker(self) -> None:
        while (src := self._next()) is not None:
            try:
                self._convert(src)
            except Exception:
                log.exception("Video conversion crashed on %s", src)
            finally:
                with self._cond:
                    self._current = None
                    self._cond.notify_all()

    def run_until_idle(self) -> None:
        """Convert everything queued on the calling thread (tests, scripts)."""
        while True:
            with self._cond:
                if not self._pending:
                    return
                src = min(self._pending, key=self._pending.get)
                del self._pending[src]
                self._current = {"src": src, "name": src.name, "percent": 0}
            try:
                self._convert(src)
            finally:
                with self._cond:
                    self._current = None

    # -- conversion --

    def _convert(self, src: Path) -> None:
        ffmpeg, ffprobe = self.tools()
        if not ffmpeg:
            return
        max_edge = self.settings.max_edge
        try:
            key = cache_key(src, max_edge)
            out_dir = cache_dir()
        except OSError as e:
            log.warning("Skipping %s: %s", src, e)
            return
        dst, bad = out_dir / f"{key}.mp4", out_dir / f"{key}.bad"
        if dst.exists() or bad.exists():
            return
        p = probe(ffprobe, src)
        if p is None or not p.width or not p.height:
            self._fail(bad, src, "ffprobe found no video stream")
            return
        if self._current is not None:
            self._current["duration"] = p.duration
        caps = capabilities(ffmpeg)
        tiers = tiers_for(p, caps)
        if not tiers:
            self._fail(bad, src, "ffmpeg has no H.264 encoder (libx264 or h264_videotoolbox)")
            return
        tmp = out_dir / f"{key}.tmp.mp4"
        error = ""
        started = time.monotonic()
        for tier in tiers:
            if self._stopping:
                tmp.unlink(missing_ok=True)
                return
            ok, error = self._run(build_command(ffmpeg, src, tmp, p, caps, max_edge, tier), p.duration)
            if ok and tmp.exists() and tmp.stat().st_size > 0:
                os.replace(tmp, dst)
                self._write_sidecar(out_dir / f"{key}.json", src, p, tier, max_edge)
                log.info("Converted %s (%s, %.1fs)", src.name, tier, time.monotonic() - started)
                return
            tmp.unlink(missing_ok=True)
            if self._stopping:
                return
            log.info("Conversion of %s with %s failed, trying next pipeline: %s", src.name, tier, error[-300:])
        self._fail(bad, src, error)

    def _run(self, cmd: list[str], duration: float) -> tuple[bool, str]:
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.BELOW_NORMAL_PRIORITY_CLASS
        else:
            kwargs["preexec_fn"] = lambda: os.nice(10)
        with tempfile.TemporaryFile() as err:
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL,
                                        text=True, **kwargs)
            except OSError as e:
                return False, str(e)
            self._proc = proc
            try:
                for line in proc.stdout:
                    if line.startswith("out_time_us=") and duration > 0 and self._current is not None:
                        try:
                            done = int(line.split("=", 1)[1]) / 1e6
                        except ValueError:
                            continue
                        self._current["percent"] = max(0, min(99, int(done / duration * 100)))
                proc.wait()
            finally:
                self._proc = None
            err.seek(0)
            stderr = err.read()[-2048:].decode("utf-8", "replace")
        return proc.returncode == 0, stderr

    def _fail(self, bad: Path, src: Path, error: str) -> None:
        log.warning("Could not convert video %s: %s", src, error.strip()[-500:])
        try:
            bad.write_text(f"{src}\n{error[-2048:]}", encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _write_sidecar(path: Path, src: Path, p: Probe, tier: str, max_edge: int) -> None:
        st = src.stat()
        data = {"source": str(src), "mtime_ns": st.st_mtime_ns, "size": st.st_size, "duration": p.duration,
                "tier": tier, "max_edge": max_edge, "version": PIPELINE_VERSION, "created": time.time()}
        try:
            path.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass

    # -- maintenance --

    def cleanup_tmp(self) -> None:
        """Remove half-written outputs left by a crash or a killed process."""
        try:
            for f in cache_dir().glob("*.tmp.mp4"):
                f.unlink(missing_ok=True)
        except OSError:
            pass

    def prune(self) -> None:
        """Delete converted copies whose original was deleted, edited or replaced."""
        try:
            sidecars = list(cache_dir().glob("*.json"))
        except OSError:
            return
        for sidecar in sidecars:
            try:
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
                st = Path(meta["source"]).stat()
                stale = (st.st_mtime_ns != meta["mtime_ns"] or st.st_size != meta["size"]
                         or meta.get("version") != PIPELINE_VERSION)
            except FileNotFoundError:
                stale = True
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if stale:
                key = sidecar.name[:-len(".json")]
                for suffix in (".mp4", ".json"):
                    (sidecar.parent / f"{key}{suffix}").unlink(missing_ok=True)

    # -- status --

    def status(self, folders) -> dict:
        counts = {READY: 0, PENDING: 0, FAILED: 0, NO_FFMPEG: 0, LIVE_PHOTO: 0}
        pending = set()
        for folder in dict.fromkeys(f for f in folders if f):
            for c in self.scan(folder):
                counts[c.state] += 1
                if c.state == PENDING:
                    pending.add(c.path.resolve())
        with self._cond:
            # Only report the clip being converted if it belongs to the folders asked about
            current = dict(self._current) if self._current and self._current["src"] in pending else None
        ffmpeg = self.available
        result = {
            "ffmpeg": ffmpeg,
            "total": counts[READY] + counts[PENDING] + counts[FAILED] + counts[NO_FFMPEG],
            "ready": counts[READY],
            "pending": counts[PENDING],
            "failed": counts[FAILED],
            "skipped_no_ffmpeg": counts[NO_FFMPEG],
            "live_photos_hidden": counts[LIVE_PHOTO],
            "converting": {"name": current["name"], "percent": current["percent"]} if current else None,
        }
        if not ffmpeg:
            result["hint"] = f"ffmpeg isn't installed, so iPhone (HEVC) videos are skipped. Install it with {INSTALL_HINT}."
        return result


transcoder = Transcoder()
