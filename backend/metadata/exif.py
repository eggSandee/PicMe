from __future__ import annotations

import datetime
from pathlib import Path

import requests
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

# In-memory cache: GPS coords -> location string
_geocode_cache: dict[tuple[float, float], str] = {}


def _get_exif(image_path: str) -> dict:
    try:
        img = Image.open(image_path)
        raw = img._getexif()
        if not raw:
            return {}
        return {TAGS.get(k, k): v for k, v in raw.items()}
    except Exception:
        return {}


def extract_date(image_path: str) -> str | None:
    exif = _get_exif(image_path)
    for field in ("DateTimeOriginal", "DateTime", "DateTimeDigitized"):
        val = exif.get(field)
        if val:
            try:
                dt = datetime.datetime.strptime(val, "%Y:%m:%d %H:%M:%S")
                return dt.strftime("%B %-d, %Y")
            except (ValueError, AttributeError):
                # %-d is Unix-only; fall back to cross-platform form
                try:
                    dt = datetime.datetime.strptime(val, "%Y:%m:%d %H:%M:%S")
                    return dt.strftime("%B {d}, %Y").format(d=dt.day)
                except Exception:
                    pass
    return None


def _dms_to_decimal(dms, ref: str) -> float:
    degrees, minutes, seconds = dms
    # PIL returns these as IFDRational or tuples
    def to_float(v):
        try:
            return float(v)
        except TypeError:
            return v[0] / v[1]

    decimal = to_float(degrees) + to_float(minutes) / 60 + to_float(seconds) / 3600
    if ref in ("S", "W"):
        decimal = -decimal
    return decimal


def extract_gps(image_path: str) -> tuple[float, float] | None:
    exif = _get_exif(image_path)
    gps_info_raw = exif.get("GPSInfo")
    if not gps_info_raw:
        return None
    gps = {GPSTAGS.get(k, k): v for k, v in gps_info_raw.items()}
    try:
        lat = _dms_to_decimal(gps["GPSLatitude"], gps["GPSLatitudeRef"])
        lon = _dms_to_decimal(gps["GPSLongitude"], gps["GPSLongitudeRef"])
        return (lat, lon)
    except (KeyError, TypeError, ZeroDivisionError):
        return None


def reverse_geocode(lat: float, lon: float) -> str | None:
    key = (round(lat, 4), round(lon, 4))
    if key in _geocode_cache:
        return _geocode_cache[key]
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json"},
            headers={"User-Agent": "PicMe/1.0 (digital picture frame app)"},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        addr = data.get("address", {})
        # Build a readable "City, Region" or "City, Country" string
        parts = [
            addr.get("city") or addr.get("town") or addr.get("village") or addr.get("hamlet"),
            addr.get("state") or addr.get("county"),
            addr.get("country"),
        ]
        parts = [p for p in parts if p]
        location = ", ".join(parts[:2]) if len(parts) >= 2 else parts[0] if parts else None
        _geocode_cache[key] = location
        return location
    except Exception:
        return None


def get_metadata(image_path: str) -> dict:
    date = extract_date(image_path)
    coords = extract_gps(image_path)
    location = reverse_geocode(*coords) if coords else None
    return {"date": date, "location": location}
