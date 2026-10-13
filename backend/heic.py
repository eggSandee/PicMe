"""HEIC/HEIF (iPhone photo) support.

Browsers other than Safari can't render HEIC, so /api/image serves a converted
JPEG instead: orientation applied, colors converted to sRGB, long edge capped at
MAX_EDGE. Converted files are cached in the OS cache dir (never next to the
originals, which are only ever read), keyed by path + mtime + size.

Importing this module registers pillow-heif's opener, so plain Image.open()
reads HEIC everywhere — metadata.exif relies on that.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path

import pillow_heif
from PIL import Image, ImageCms, ImageOps

# Defaults decode 10-bit HDR to 8-bit. iPhone "HDR" HEICs are an SDR base image
# plus a gain map; the base image is what we show, the gain map is ignored.
pillow_heif.register_heif_opener()

log = logging.getLogger(__name__)

HEIC_EXTENSIONS = {".heic", ".heif"}

MAX_EDGE = 3840          # long-edge cap for converted copies (4K TVs)
JPEG_QUALITY = 88
CONVERTER_VERSION = 2    # bump when the pipeline changes so stale cache entries are ignored
MAX_CONCURRENT_CONVERSIONS = 2  # a cold 48MP conversion peaks around 650MB; a 6-slot first start would otherwise run 6 at once

_SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_convert_slots = threading.BoundedSemaphore(MAX_CONCURRENT_CONVERSIONS)


class HeicConversionError(Exception):
    pass


def is_heic(path: str | Path) -> bool:
    return Path(path).suffix.lower() in HEIC_EXTENSIONS


def cache_dir() -> Path:
    """Per-user cache dir for converted files, outside the repo and photo folders."""
    override = os.environ.get("PICME_CACHE_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "PicMe"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "PicMe" / "Cache"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "picme"
    path = base / "heic"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(src: Path) -> str:
    st = src.stat()
    raw = f"{src}|{st.st_mtime_ns}|{st.st_size}|v{CONVERTER_VERSION}|{MAX_EDGE}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _to_srgb(img: Image.Image) -> Image.Image:
    """Convert an ICC-tagged image (iPhones tag Display P3) to sRGB.
    Untagged / nclx-only images are treated as sRGB already."""
    icc = img.info.get("icc_profile")
    if icc and img.mode in ("RGB", "RGBA"):
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            return ImageCms.profileToProfile(img, src, _SRGB, outputMode="RGB")
        except (ImageCms.PyCMSError, OSError) as e:
            log.warning("Ignoring unusable ICC profile: %s", e)
    return img.convert("RGB")


def _convert(src: Path, dst: Path) -> None:
    # Downscale first so the full-size decode is the only full-size buffer: thumbnail() and
    # exif_transpose(in_place=True) replace the pixels in place, and the color transform
    # then runs on the small copy.
    with Image.open(src) as img:
        img.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
        ImageOps.exif_transpose(img, in_place=True)  # pillow-heif already applies irot/imir; this is a safety net
        img = _to_srgb(img)
    fd, tmp = tempfile.mkstemp(dir=dst.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            # No EXIF/ICC: orientation is baked in and pixels are sRGB.
            img.save(f, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        os.replace(tmp, dst)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def get_converted(path: str | Path) -> Path:
    """Return the cached JPEG for a HEIC file, converting it on first use.

    Raises HeicConversionError if the file can't be decoded. Failures are remembered
    (a .bad marker) so a corrupt file isn't re-decoded on every slot cycle; editing or
    replacing the file changes its mtime and clears that.
    """
    src = Path(path).resolve()
    try:
        key = _cache_key(src)
        out_dir = cache_dir()
    except OSError as e:
        log.warning("HEIC cache unavailable for %s: %s", src, e)
        raise HeicConversionError(str(e)) from e
    dst = out_dir / f"{key}.jpg"
    bad = out_dir / f"{key}.bad"
    if dst.exists():
        return dst
    if bad.exists():
        raise HeicConversionError(f"{src} previously failed to decode")
    with _lock_for(key):
        if dst.exists():
            return dst
        if bad.exists():
            raise HeicConversionError(f"{src} previously failed to decode")
        try:
            with _convert_slots:
                _convert(src, dst)
        except Exception as e:
            log.warning("Could not convert HEIC %s: %s", src, e)
            try:
                bad.touch()
            except OSError:
                pass
            raise HeicConversionError(str(e)) from e
    return dst
