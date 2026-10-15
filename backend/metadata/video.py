from __future__ import annotations

import datetime
import os
import struct
from dataclasses import dataclass
from pathlib import Path

# MP4/MOV (ISO-BMFF / QuickTime) timestamps count seconds from 1904-01-01 UTC
_MAC_EPOCH = datetime.datetime(1904, 1, 1, tzinfo=datetime.timezone.utc)
# Cameras often write 0 or a garbage value; treat anything earlier as missing
_MIN_PLAUSIBLE = datetime.datetime(1990, 1, 1, tzinfo=datetime.timezone.utc)


def _iter_boxes(f, start: int, end: int):
    """Yield (type, payload_offset, box_end) for each box in [start, end).

    Seeks past box payloads rather than reading them, so a multi-GB `mdat`
    before a trailing `moov` (non-faststart files) costs nothing.
    """
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        header = f.read(8)
        if len(header) < 8:
            return
        size, box_type = struct.unpack(">I4s", header)
        header_len = 8
        if size == 1:
            large = f.read(8)
            if len(large) < 8:
                return
            size = struct.unpack(">Q", large)[0]
            header_len = 16
        elif size == 0:
            size = end - pos  # box extends to end of container
        if size < header_len:
            return  # corrupt — stop rather than loop forever
        yield box_type, pos + header_len, min(pos + size, end)
        pos += size


def _find_box(f, box_type: bytes, start: int, end: int) -> tuple[int, int] | None:
    for t, payload, box_end in _iter_boxes(f, start, end):
        if t == box_type:
            return payload, box_end
    return None


def extract_video_date(video_path: str) -> str | None:
    """Return the mvhd creation date as YYYY-MM-DD, or None if missing/implausible."""
    try:
        with open(video_path, "rb") as f:
            f.seek(0, 2)
            file_end = f.tell()
            moov = _find_box(f, b"moov", 0, file_end)
            if not moov:
                return None
            mvhd = _find_box(f, b"mvhd", *moov)
            if not mvhd:
                return None
            f.seek(mvhd[0])
            version = f.read(4)[0]  # version byte + 3 flag bytes
            if version == 1:
                seconds = struct.unpack(">Q", f.read(8))[0]
            else:
                seconds = struct.unpack(">I", f.read(4))[0]
    except (OSError, IndexError, struct.error):
        return None

    if seconds == 0:
        return None
    try:
        dt = _MAC_EPOCH + datetime.timedelta(seconds=seconds)
    except OverflowError:
        return None
    if dt < _MIN_PLAUSIBLE or dt > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1):
        return None
    # creation_time is UTC; photo EXIF dates are local wall-clock time, so report the
    # server's local date to match (a clip shot at 23:00 local mustn't show the next day)
    return dt.astimezone().strftime("%Y-%m-%d")


def get_video_metadata(video_path: str) -> dict:
    # Video slots show the date pill only — other keys are intentionally absent
    return {"date_iso": extract_video_date(video_path)}


# --- Codec sniffing (no ffprobe) ---
# Enough of moov to decide "the browser can play this as-is" without spawning a process,
# so listing a folder of hundreds of clips stays cheap.

@dataclass(frozen=True)
class VideoSniff:
    codec: str                    # first video sample entry fourcc: avc1, hvc1, dvh1, apcn, ...
    width: int                    # tkhd (display) size, pre-rotation
    height: int
    duration_s: float | None      # mvhd duration
    profile: int | None = None    # avcC AVCProfileIndication (H.264 only)
    transfer: int | None = None   # colr nclx/nclc transfer characteristics (16 = PQ, 18 = HLG)
    audio: tuple[str, ...] = ()   # sample entry fourccs of the sound tracks


_VISUAL_SAMPLE_ENTRY_LEN = 78  # fixed fields before a visual sample entry's child boxes
_sniff_cache: dict[tuple[str, int, int], VideoSniff | None] = {}
_SNIFF_CACHE_MAX = 4096


def _read_at(f, offset: int, n: int) -> bytes:
    f.seek(offset)
    data = f.read(n)
    if len(data) < n:
        raise struct.error("truncated box")
    return data


def _sample_entry(f, stbl: tuple[int, int]) -> tuple[str, int, int] | None:
    """(fourcc, payload offset, end) of the first stsd sample entry."""
    stsd = _find_box(f, b"stsd", *stbl)
    if not stsd:
        return None
    entries = _iter_boxes(f, stsd[0] + 8, stsd[1])  # skip version/flags + entry_count
    for fourcc, payload, end in entries:
        return fourcc.decode("latin-1"), payload, end
    return None


def _sniff_visual_entry(f, payload: int, end: int) -> tuple[int | None, int | None]:
    profile = transfer = None
    for t, child, child_end in _iter_boxes(f, payload + _VISUAL_SAMPLE_ENTRY_LEN, end):
        if t == b"avcC" and child_end - child >= 2:
            profile = _read_at(f, child, 2)[1]
        elif t == b"colr" and child_end - child >= 10:
            data = _read_at(f, child, 10)
            if data[:4] in (b"nclx", b"nclc"):
                transfer = struct.unpack(">H", data[6:8])[0]
    return profile, transfer


def _sniff(f, file_end: int) -> VideoSniff | None:
    moov = _find_box(f, b"moov", 0, file_end)
    if not moov:
        return None
    duration = None
    video = None
    audio: list[str] = []
    for t, payload, end in _iter_boxes(f, *moov):
        if t == b"mvhd":
            version = _read_at(f, payload, 1)[0]
            if version == 1:
                timescale, dur = struct.unpack(">IQ", _read_at(f, payload + 20, 12))
            else:
                timescale, dur = struct.unpack(">II", _read_at(f, payload + 12, 8))
            if timescale:
                duration = dur / timescale
        elif t == b"trak":
            mdia = _find_box(f, b"mdia", payload, end)
            hdlr = mdia and _find_box(f, b"hdlr", *mdia)
            if not hdlr:
                continue
            handler = _read_at(f, hdlr[0] + 8, 4)
            minf = _find_box(f, b"minf", *mdia)
            stbl = minf and _find_box(f, b"stbl", *minf)
            entry = stbl and _sample_entry(f, stbl)
            if not entry:
                continue
            if handler == b"soun":
                audio.append(entry[0])
            elif handler == b"vide" and video is None:
                tkhd = _find_box(f, b"tkhd", payload, end)
                if not tkhd:
                    continue
                version = _read_at(f, tkhd[0], 1)[0]
                dims_at = tkhd[0] + (88 if version == 1 else 76)
                w, h = struct.unpack(">II", _read_at(f, dims_at, 8))
                profile, transfer = _sniff_visual_entry(f, entry[1], entry[2])
                video = (entry[0], w >> 16, h >> 16, profile, transfer)
    if video is None:
        return None
    codec, w, h, profile, transfer = video
    return VideoSniff(codec, w, h, duration, profile, transfer, tuple(audio))


def sniff_video(path: str | Path) -> VideoSniff | None:
    """Codec, size, duration and audio codecs from the container, or None if unreadable.

    Memoised by (path, mtime, size), so re-listing a folder doesn't re-read every file.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key in _sniff_cache:
        return _sniff_cache[key]
    try:
        with open(path, "rb") as f:
            result = _sniff(f, st.st_size)
    except (OSError, IndexError, struct.error, UnicodeDecodeError):
        result = None
    if len(_sniff_cache) >= _SNIFF_CACHE_MAX:
        _sniff_cache.clear()
    _sniff_cache[key] = result
    return result
