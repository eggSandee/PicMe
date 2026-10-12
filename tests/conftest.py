"""Shared pytest fixtures for PicMe.

Every test runs against an isolated config file and a generated photo
library under pytest's tmp dirs — the real ``config.json`` and ``photos/``
are never touched, and no test reaches the network.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import piexif
import pytest
from PIL import Image, ImageCms

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import heic  # noqa: E402  (registers the HEIF opener, so Image.save(format="HEIF") works)
import main  # noqa: E402  (needs backend/ on sys.path)
from metadata import exif as exif_module  # noqa: E402

LAYOUTS_DIR = REPO_ROOT / "layouts"

# --- EXIF fixture values (tests assert against these) ---

EXIF_DATE = "2019:07:04 18:30:00"
EXIF_DATE_ISO = "2019-07-04"
EXIF_MAKE = "Canon"
EXIF_MODEL = "Canon EOS R5"
EXIF_FNUMBER = (28, 10)          # f/2.8
EXIF_EXPOSURE = (1, 250)         # 1/250s
EXIF_ISO = 200
EXIF_FOCAL = (50, 1)             # 50mm
# 37°46'29.64"N, 122°25'9.84"W — San Francisco
GPS_LAT = ((37, 1), (46, 1), (2964, 100))
GPS_LON = ((122, 1), (25, 1), (984, 100))
GPS_LAT_DECIMAL = 37 + 46 / 60 + 29.64 / 3600
GPS_LON_DECIMAL = -(122 + 25 / 60 + 9.84 / 3600)

LANDSCAPE = (300, 200)
PORTRAIT = (200, 300)


def _exif_bytes(*, date: bool = False, gps: bool = False, camera: bool = False) -> bytes:
    zeroth: dict = {}
    exif: dict = {}
    gps_ifd: dict = {}
    if date:
        zeroth[piexif.ImageIFD.DateTime] = EXIF_DATE
        exif[piexif.ExifIFD.DateTimeOriginal] = EXIF_DATE
        exif[piexif.ExifIFD.DateTimeDigitized] = EXIF_DATE
    if camera:
        zeroth[piexif.ImageIFD.Make] = EXIF_MAKE
        zeroth[piexif.ImageIFD.Model] = EXIF_MODEL
        exif[piexif.ExifIFD.FNumber] = EXIF_FNUMBER
        exif[piexif.ExifIFD.ExposureTime] = EXIF_EXPOSURE
        exif[piexif.ExifIFD.ISOSpeedRatings] = EXIF_ISO
        exif[piexif.ExifIFD.FocalLength] = EXIF_FOCAL
    if gps:
        gps_ifd[piexif.GPSIFD.GPSLatitudeRef] = "N"
        gps_ifd[piexif.GPSIFD.GPSLatitude] = GPS_LAT
        gps_ifd[piexif.GPSIFD.GPSLongitudeRef] = "W"
        gps_ifd[piexif.GPSIFD.GPSLongitude] = GPS_LON
    return piexif.dump({"0th": zeroth, "Exif": exif, "GPS": gps_ifd, "1st": {}, "thumbnail": None})


def _make_image(path: Path, size=LANDSCAPE, fmt: str | None = None, color=(200, 80, 40), exif: bytes | None = None) -> Path:
    mode = "P" if fmt == "GIF" else "RGB"
    img = Image.new("RGB", size, color)
    if mode == "P":
        img = img.convert("P")
    kwargs = {"exif": exif} if exif else {}
    img.save(path, format=fmt, **kwargs)
    return path


def display_p3_icc() -> bytes:
    """A minimal ICC v2 matrix/TRC profile with Display P3 primaries (what iPhones tag photos
    with). Pillow can only create sRGB/LAB/XYZ profiles, and the OS P3 file isn't portable."""
    def s15(v):
        return struct.pack(">i", round(v * 65536))

    def xyz(x, y, z):
        return b"XYZ \0\0\0\0" + s15(x) + s15(y) + s15(z)

    desc_txt = b"Display P3 (PicMe tests)\0"
    desc = b"desc\0\0\0\0" + struct.pack(">I", len(desc_txt)) + desc_txt + b"\0" * (8 + 3 + 67)
    curv = b"curv\0\0\0\0" + struct.pack(">IH", 1, int(2.2 * 256)) + b"\0\0"
    tags = [
        (b"desc", desc), (b"cprt", b"text\0\0\0\0PicMe tests\0"),
        (b"wtpt", xyz(0.9642, 1.0, 0.8249)),
        (b"rXYZ", xyz(0.5151, 0.2412, -0.0011)),
        (b"gXYZ", xyz(0.2920, 0.6922, 0.0419)),
        (b"bXYZ", xyz(0.1571, 0.0666, 0.7841)),
        (b"rTRC", curv), (b"gTRC", curv), (b"bTRC", curv),
    ]
    offset = 128 + 4 + 12 * len(tags)
    table, data = b"", b""
    for sig, body in tags:
        body += b"\0" * (-len(body) % 4)
        table += sig + struct.pack(">II", offset + len(data), len(body))
        data += body
    header = (struct.pack(">I", offset + len(data)) + b"\0" * 4 + struct.pack(">I", 0x02100000)
              + b"mntrRGB XYZ " + b"\0" * 12 + b"acsp" + b"\0" * 28 + s15(0.9642) + s15(1.0) + s15(0.8249))
    header += b"\0" * (128 - len(header))
    return header + struct.pack(">I", len(tags)) + table + data


# A P3 orange: the same numbers mean a more saturated color in P3 than in sRGB
P3_ORANGE = (200, 100, 50)


def _make_heic(path: Path, size=LANDSCAPE, color=(200, 80, 40), exif: bytes | None = None,
               icc_profile: bytes | None = None) -> Path:
    """Save a real HEIC (HEVC via pillow-heif). With EXIF Orientation 6, pillow-heif stores the
    rotation as an irot box like an iPhone does, so ``size`` is the sensor (pre-rotation) size."""
    kwargs: dict = {"quality": -1}  # lossless, so color assertions aren't at the mercy of HEVC
    if exif:
        kwargs["exif"] = exif
    if icc_profile:
        kwargs["icc_profile"] = icc_profile
    Image.new("RGB", size, color).save(path, format="HEIF", **kwargs)
    return path


def build_photo_library(root: Path) -> SimpleNamespace:
    """Generate a photo library covering every format/EXIF edge case.

    Layout::

        root/photos/        mixed library (see ``files`` below)
        root/empty/         empty folder
        root/odd dir ü/     folder whose name has a space and unicode
    """
    photos = root / "photos"
    empty = root / "empty"
    odd = root / "odd dir ü"
    for d in (photos, empty, odd):
        d.mkdir(parents=True, exist_ok=True)

    files = {
        # JPEG with full EXIF: date + GPS + camera settings (landscape)
        "jpeg_full": _make_image(photos / "full_exif.jpg", fmt="JPEG",
                                 exif=_exif_bytes(date=True, gps=True, camera=True)),
        # JPEG with only an EXIF date, no GPS (portrait)
        "jpeg_date_only": _make_image(photos / "date_only_portrait.jpg", size=PORTRAIT, fmt="JPEG",
                                      exif=_exif_bytes(date=True)),
        # JPEG with GPS but no date
        "jpeg_gps_only": _make_image(photos / "gps_only.jpeg", fmt="JPEG", exif=_exif_bytes(gps=True)),
        # JPEG with no EXIF at all (portrait)
        "jpeg_no_exif": _make_image(photos / "no_exif_portrait.jpg", size=PORTRAIT, fmt="JPEG"),
        # Upper-case extension
        "jpeg_upper": _make_image(photos / "UPPER.JPG", fmt="JPEG"),
        "png": _make_image(photos / "plain.png", fmt="PNG", color=(40, 120, 200)),
        "gif": _make_image(photos / "plain.gif", fmt="GIF", color=(40, 200, 80)),
        "webp": _make_image(photos / "dated.webp", fmt="WEBP", exif=_exif_bytes(date=True)),
    }
    # iPhone-style HEIC/HEIF (upper-case extension like iPhone exports)
    files["heic_full"] = _make_heic(photos / "IMG_0001.HEIC",
                                    exif=_exif_bytes(date=True, gps=True, camera=True))
    # Portrait shot: landscape sensor data + Orientation 6 (rotate 90° CW)
    portrait_exif = piexif.dump({"0th": {piexif.ImageIFD.Orientation: 6}, "Exif": {}, "GPS": {},
                                 "1st": {}, "thumbnail": None})
    files["heic_portrait"] = _make_heic(photos / "IMG_0002.HEIC", size=LANDSCAPE, exif=portrait_exif)
    files["heic_p3"] = _make_heic(photos / "IMG_0003.heic", color=P3_ORANGE, icc_profile=display_p3_icc())
    files["heif"] = _make_heic(photos / "plain.heif", color=P3_ORANGE)
    corrupt_heic = photos / "corrupt.heic"
    corrupt_heic.write_bytes(b"\0\0\0\x18ftypheic not really a heic")
    files["heic_corrupt"] = corrupt_heic
    # Corrupt file with a supported extension
    corrupt = photos / "corrupt.jpg"
    corrupt.write_bytes(b"this is not really a jpeg\x00\xff\xd8")
    files["corrupt"] = corrupt
    # Wrong extension: PNG bytes saved as .jpg
    files["png_as_jpg"] = _make_image(photos / "really_a_png.jpg", fmt="PNG")
    # Unsupported extensions that must be ignored by listings
    notes = photos / "notes.txt"
    notes.write_text("not an image")
    files["txt"] = notes
    files["jpeg_as_txt"] = _make_image(photos / "jpeg_named.txt", fmt="JPEG")
    # Subdirectories are not images either
    (photos / "subdir").mkdir(exist_ok=True)
    _make_image(photos / "subdir" / "nested.jpg", fmt="JPEG")

    odd_image = _make_image(odd / "café photo ü.jpg", fmt="JPEG", exif=_exif_bytes(date=True))

    supported = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif"}
    listed = sorted(str(p) for p in photos.iterdir() if p.is_file() and p.suffix.lower() in supported)
    return SimpleNamespace(
        root=root,
        dir=photos,
        empty=empty,
        odd_dir=odd,
        odd_image=odd_image,
        files=files,
        listed=listed,  # what /api/images/list should return for ``dir`` (any order)
    )


@pytest.fixture(scope="session")
def photo_library(tmp_path_factory) -> SimpleNamespace:
    """Session-wide generated photo library (read-only — don't mutate)."""
    return build_photo_library(tmp_path_factory.mktemp("library"))


@pytest.fixture(scope="session", autouse=True)
def heic_cache_session(tmp_path_factory):
    """Keep converted-HEIC files out of the real OS cache, including for e2e server subprocesses."""
    mp = pytest.MonkeyPatch()
    mp.setenv("PICME_CACHE_DIR", str(tmp_path_factory.mktemp("heic-cache")))
    yield
    mp.undo()


@pytest.fixture(autouse=True)
def heic_cache_dir(tmp_path, monkeypatch) -> Path:
    """A fresh HEIC cache per test, so cache hits/misses are deterministic."""
    path = tmp_path / "picme-cache"
    monkeypatch.setenv("PICME_CACHE_DIR", str(path))
    return path / "heic"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Block real reverse-geocoding calls and reset the geocode cache."""
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is disabled in tests")

    monkeypatch.setattr(exif_module.requests, "get", _blocked)
    exif_module._geocode_cache.clear()
    yield
    exif_module._geocode_cache.clear()


@pytest.fixture
def config_path(tmp_path, monkeypatch) -> Path:
    """Point ``main.CONFIG_PATH`` at a throwaway file (not created yet)."""
    path = tmp_path / "config.json"
    monkeypatch.setattr(main, "CONFIG_PATH", path)
    return path


def make_config(folder: Path | str, layout: str = "classic-4", slots: int = 4, cycle_seconds: int = 30) -> dict:
    return {
        "layout": layout,
        "wall_color": "#1a1a1a",
        "show_metadata": True,
        "date_format": "long",
        "slots": [
            {"id": i, "mode": "photos", "source": "local", "source_path": str(folder),
             "cycle_seconds": cycle_seconds}
            for i in range(1, slots + 1)
        ],
    }


@pytest.fixture
def sample_config(photo_library) -> dict:
    """A classic-4 config with every slot pointed at the generated library."""
    return make_config(photo_library.dir)


@pytest.fixture
def written_config(config_path, sample_config) -> dict:
    """``sample_config`` already saved to the isolated config file."""
    config_path.write_text(json.dumps(sample_config, indent=2))
    return sample_config


@pytest.fixture
def client(config_path):
    """FastAPI TestClient bound to the isolated config."""
    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        yield c
