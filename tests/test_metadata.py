"""Tests for backend/metadata/exif.py — EXIF extraction and reverse geocoding.

Geocoding never touches the network: the autouse ``no_network`` fixture makes
``requests.get`` raise, and tests that need a location patch it with a fake.
"""
from __future__ import annotations

from pathlib import Path

import piexif
import pytest
from PIL import Image

from conftest import (
    EXIF_DATE_ISO,
    EXIF_MODEL,
    GPS_LAT_DECIMAL,
    GPS_LON_DECIMAL,
    exif_module,
)

METADATA_KEYS = {
    "date_iso", "location", "camera", "megapixels",
    "fstop", "shutter_speed", "iso", "focal_length",
}


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture
def fake_geocoder(monkeypatch):
    """Patch ``requests.get`` with a fake Nominatim; returns the call log."""
    calls: list[dict] = []
    state = {"address": {"city": "San Francisco", "state": "California", "country": "United States"},
             "status": 200}

    def _get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return FakeResponse({"address": state["address"]}, state["status"])

    monkeypatch.setattr(exif_module.requests, "get", _get)
    return state | {"calls": calls}


def _jpeg_with_exif(path: Path, size=(300, 200), **ifds) -> str:
    """Write a JPEG whose EXIF is built from piexif IFD dicts (``zeroth=``, ``exif=``, ``gps=``)."""
    exif_bytes = piexif.dump({
        "0th": ifds.get("zeroth", {}), "Exif": ifds.get("exif", {}),
        "GPS": ifds.get("gps", {}), "1st": {}, "thumbnail": None,
    })
    Image.new("RGB", size, (10, 20, 30)).save(path, format="JPEG", exif=exif_bytes)
    return str(path)


# --- date -----------------------------------------------------------------

def test_date_from_full_exif(photo_library):
    assert exif_module.extract_date(str(photo_library.files["jpeg_full"])) == EXIF_DATE_ISO


def test_date_only_jpeg(photo_library):
    assert exif_module.extract_date(str(photo_library.files["jpeg_date_only"])) == EXIF_DATE_ISO


def test_date_from_webp_exif(photo_library):
    assert exif_module.extract_date(str(photo_library.files["webp"])) == EXIF_DATE_ISO


def test_date_falls_back_to_datetime_tag(tmp_path):
    path = _jpeg_with_exif(tmp_path / "dt.jpg", zeroth={piexif.ImageIFD.DateTime: "2001:02:03 04:05:06"})
    assert exif_module.extract_date(path) == "2001-02-03"


def test_date_skips_malformed_original(tmp_path):
    path = _jpeg_with_exif(
        tmp_path / "bad_date.jpg",
        zeroth={piexif.ImageIFD.DateTime: "2001:02:03 04:05:06"},
        exif={piexif.ExifIFD.DateTimeOriginal: "not a date"},
    )
    assert exif_module.extract_date(path) == "2001-02-03"


def test_date_missing(photo_library):
    assert exif_module.extract_date(str(photo_library.files["jpeg_gps_only"])) is None
    assert exif_module.extract_date(str(photo_library.files["jpeg_no_exif"])) is None


# --- GPS / location ---------------------------------------------------------

def test_gps_decimal_coordinates(photo_library):
    lat, lon = exif_module.extract_gps(str(photo_library.files["jpeg_full"]))
    assert lat == pytest.approx(GPS_LAT_DECIMAL, abs=1e-6)
    assert lon == pytest.approx(GPS_LON_DECIMAL, abs=1e-6)


def test_gps_southern_eastern_hemisphere(tmp_path):
    path = _jpeg_with_exif(tmp_path / "sydney.jpg", gps={
        piexif.GPSIFD.GPSLatitudeRef: "S",
        piexif.GPSIFD.GPSLatitude: ((33, 1), (52, 1), (0, 1)),
        piexif.GPSIFD.GPSLongitudeRef: "E",
        piexif.GPSIFD.GPSLongitude: ((151, 1), (12, 1), (36, 1)),
    })
    lat, lon = exif_module.extract_gps(path)
    assert lat == pytest.approx(-(33 + 52 / 60), abs=1e-6)
    assert lon == pytest.approx(151 + 12 / 60 + 36 / 3600, abs=1e-6)


def test_gps_incomplete_returns_none(tmp_path):
    path = _jpeg_with_exif(tmp_path / "half_gps.jpg", gps={
        piexif.GPSIFD.GPSLatitudeRef: "N",
        piexif.GPSIFD.GPSLatitude: ((37, 1), (46, 1), (0, 1)),
    })
    assert exif_module.extract_gps(path) is None


def test_gps_missing(photo_library):
    assert exif_module.extract_gps(str(photo_library.files["jpeg_date_only"])) is None
    assert exif_module.extract_gps(str(photo_library.files["jpeg_no_exif"])) is None


def test_reverse_geocode_city_region(fake_geocoder):
    assert exif_module.reverse_geocode(GPS_LAT_DECIMAL, GPS_LON_DECIMAL) == "San Francisco, California"
    call = fake_geocoder["calls"][0]
    assert "nominatim" in call["url"]
    assert call["params"]["lat"] == GPS_LAT_DECIMAL
    assert call["params"]["lon"] == GPS_LON_DECIMAL
    assert "User-Agent" in call["headers"]
    assert call["timeout"]


@pytest.mark.parametrize("address, expected", [
    ({"town": "Hobbiton", "county": "The Shire", "country": "Middle-earth"}, "Hobbiton, The Shire"),
    ({"village": "Gimmelwald", "country": "Switzerland"}, "Gimmelwald, Switzerland"),
    ({"country": "Antarctica"}, "Antarctica"),
    ({}, None),
])
def test_reverse_geocode_address_shapes(fake_geocoder, address, expected):
    fake_geocoder["address"].clear()
    fake_geocoder["address"].update(address)
    assert exif_module.reverse_geocode(1.0, 2.0) == expected


def test_reverse_geocode_is_cached(fake_geocoder):
    first = exif_module.reverse_geocode(GPS_LAT_DECIMAL, GPS_LON_DECIMAL)
    # Same coords to 4 decimal places hit the cache.
    second = exif_module.reverse_geocode(GPS_LAT_DECIMAL + 1e-6, GPS_LON_DECIMAL)
    assert first == second
    assert len(fake_geocoder["calls"]) == 1


def test_reverse_geocode_http_error(monkeypatch):
    monkeypatch.setattr(exif_module.requests, "get", lambda *a, **k: FakeResponse({}, 503))
    assert exif_module.reverse_geocode(1.0, 2.0) is None
    assert exif_module._geocode_cache == {}


def test_reverse_geocode_network_failure():
    # autouse no_network makes requests.get raise
    assert exif_module.reverse_geocode(1.0, 2.0) is None


def test_get_metadata_location_mocked(photo_library, fake_geocoder):
    meta = exif_module.get_metadata(str(photo_library.files["jpeg_full"]))
    assert meta["location"] == "San Francisco, California"
    assert len(fake_geocoder["calls"]) == 1


def test_get_metadata_no_gps_skips_geocoding(photo_library, fake_geocoder):
    meta = exif_module.get_metadata(str(photo_library.files["jpeg_date_only"]))
    assert meta["location"] is None
    assert fake_geocoder["calls"] == []


# --- camera settings --------------------------------------------------------

def test_camera_deduplicates_make_prefix(photo_library):
    # Make "Canon" is a prefix of Model "Canon EOS R5"
    assert exif_module.extract_camera(str(photo_library.files["jpeg_full"])) == EXIF_MODEL


@pytest.mark.parametrize("make, model, expected", [
    ("NIKON CORPORATION", "D850", "NIKON CORPORATION D850"),
    ("  Apple ", " iPhone 15 ", "Apple iPhone 15"),
    ("", "X100V", "X100V"),
    ("FUJIFILM", "", "FUJIFILM"),
])
def test_camera_make_model_combinations(tmp_path, make, model, expected):
    zeroth = {}
    if make:
        zeroth[piexif.ImageIFD.Make] = make
    if model:
        zeroth[piexif.ImageIFD.Model] = model
    path = _jpeg_with_exif(tmp_path / "cam.jpg", zeroth=zeroth)
    assert exif_module.extract_camera(path) == expected


def test_fstop(photo_library):
    assert exif_module.extract_fstop(str(photo_library.files["jpeg_full"])) == "f/2.8"


def test_fstop_whole_number(tmp_path):
    path = _jpeg_with_exif(tmp_path / "f8.jpg", exif={piexif.ExifIFD.FNumber: (8, 1)})
    assert exif_module.extract_fstop(path) == "f/8"


def test_shutter_fraction(photo_library):
    assert exif_module.extract_shutter_speed(str(photo_library.files["jpeg_full"])) == "1/250s"


@pytest.mark.parametrize("exposure, expected", [
    ((2, 1), "2s"),
    ((3, 2), "1.5s"),
    ((5, 2), "2.5s"),
    ((1, 3), "1/3s"),
    ((1, 8000), "1/8000s"),
])
def test_shutter_speed_formats(tmp_path, exposure, expected):
    path = _jpeg_with_exif(tmp_path / "shutter.jpg", exif={piexif.ExifIFD.ExposureTime: exposure})
    assert exif_module.extract_shutter_speed(path) == expected


def test_iso(photo_library):
    assert exif_module.extract_iso(str(photo_library.files["jpeg_full"])) == "ISO 200"


@pytest.mark.filterwarnings("ignore:Corrupt EXIF data")
def test_iso_tuple_value(tmp_path):
    path = _jpeg_with_exif(tmp_path / "iso.jpg", exif={piexif.ExifIFD.ISOSpeedRatings: (800, 1600)})
    assert exif_module.extract_iso(path) == "ISO 800"


def test_focal_length(photo_library):
    assert exif_module.extract_focal_length(str(photo_library.files["jpeg_full"])) == "50mm"


def test_focal_length_rounds(tmp_path):
    path = _jpeg_with_exif(tmp_path / "focal.jpg", exif={piexif.ExifIFD.FocalLength: (2350, 100)})
    assert exif_module.extract_focal_length(path) == "24mm"


# --- megapixels -------------------------------------------------------------

@pytest.mark.parametrize("key", ["jpeg_full", "jpeg_no_exif", "png", "gif", "webp", "png_as_jpg"])
def test_megapixels_generated_images(photo_library, key):
    # Generated images are 300x200 or 200x300 => 0.06 MP
    assert exif_module.extract_megapixels(str(photo_library.files[key])) == "0.1 MP"


def test_megapixels_large_image(tmp_path):
    path = tmp_path / "big.png"
    Image.new("RGB", (4000, 3000)).save(path, format="PNG")
    assert exif_module.extract_megapixels(str(path)) == "12.0 MP"


def test_megapixels_corrupt(photo_library):
    assert exif_module.extract_megapixels(str(photo_library.files["corrupt"])) is None


# --- images without EXIF ----------------------------------------------------

def test_full_metadata(photo_library, fake_geocoder):
    meta = exif_module.get_metadata(str(photo_library.files["jpeg_full"]))
    assert meta == {
        "date_iso": EXIF_DATE_ISO,
        "location": "San Francisco, California",
        "camera": EXIF_MODEL,
        "megapixels": "0.1 MP",
        "fstop": "f/2.8",
        "shutter_speed": "1/250s",
        "iso": "ISO 200",
        "focal_length": "50mm",
    }


@pytest.mark.parametrize("key", ["jpeg_no_exif", "jpeg_upper", "png", "gif", "png_as_jpg"])
def test_no_exif_returns_cleanly(photo_library, key):
    meta = exif_module.get_metadata(str(photo_library.files[key]))
    assert set(meta) == METADATA_KEYS
    assert meta["megapixels"] == "0.1 MP"
    assert all(v is None for k, v in meta.items() if k != "megapixels")


def test_corrupt_file_returns_all_none(photo_library):
    meta = exif_module.get_metadata(str(photo_library.files["corrupt"]))
    assert set(meta) == METADATA_KEYS
    assert all(v is None for v in meta.values())


def test_missing_file_returns_all_none(tmp_path):
    meta = exif_module.get_metadata(str(tmp_path / "does_not_exist.jpg"))
    assert all(v is None for v in meta.values())


def test_non_ascii_path(photo_library):
    meta = exif_module.get_metadata(str(photo_library.odd_image))
    assert meta["date_iso"] == EXIF_DATE_ISO
    assert meta["location"] is None


def test_zero_denominator_rationals_return_none(tmp_path):
    """Degenerate EXIF rationals (x/0) must not crash extraction."""
    path = _jpeg_with_exif(tmp_path / "zero.jpg", exif={
        piexif.ExifIFD.FNumber: (0, 0),
        piexif.ExifIFD.FocalLength: (0, 0),
    })
    meta = exif_module.get_metadata(path)
    assert set(meta) == METADATA_KEYS
