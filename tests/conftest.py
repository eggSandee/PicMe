"""Shared pytest fixtures for PicMe.

Every test runs against an isolated config file and a generated photo
library under pytest's tmp dirs — the real ``config.json`` and ``photos/``
are never touched, and no test reaches the network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import piexif
import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

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

    supported = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
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
