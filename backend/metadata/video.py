from __future__ import annotations

import datetime
import struct

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
