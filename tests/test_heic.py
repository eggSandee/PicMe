"""HEIC/HEIF (iPhone photo) support: listing, on-the-fly JPEG conversion, caching and metadata.

Fixtures are real HEVC-encoded HEICs generated with pillow-heif (see conftest), not real
iPhone files. Every test gets its own PICME_CACHE_DIR (``heic_cache_dir``).
"""
from __future__ import annotations

import io
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from conftest import (
    EXIF_DATE_ISO,
    EXIF_MODEL,
    LANDSCAPE,
    P3_ORANGE,
    PORTRAIT,
    _exif_bytes,
    _make_heic,
    exif_module,
)
import heic


def _get_jpeg(client, path) -> Image.Image:
    r = client.get("/api/image", params={"path": str(path)})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"
    img = Image.open(io.BytesIO(r.content))
    assert img.format == "JPEG"
    img.load()
    return img


@pytest.fixture
def convert_calls(monkeypatch) -> list:
    """Spy on the real decoder so tests can tell cache hits from conversions."""
    calls = []
    real = heic._convert

    def _spy(src, dst):
        calls.append(src)
        return real(src, dst)

    monkeypatch.setattr(heic, "_convert", _spy)
    return calls


@pytest.fixture(scope="session")
def heic_48mp(tmp_path_factory):
    """An iPhone-48MP-sized (8064x6048) HEIC with EXIF."""
    path = tmp_path_factory.mktemp("big") / "IMG_4800.HEIC"
    Image.new("RGB", (8064, 6048), (90, 120, 150)).save(
        path, format="HEIF", quality=50, exif=_exif_bytes(date=True, camera=True))
    return path


# --- listing ---------------------------------------------------------------

def test_list_includes_heic_and_heif_any_case(client, photo_library):
    images = set(client.get("/api/images/list", params={"folder": str(photo_library.dir)}).json()["images"])
    for key in ("heic_full", "heic_portrait", "heic_p3", "heif"):
        assert str(photo_library.files[key]) in images
    assert str(photo_library.files["heic_full"]).endswith("IMG_0001.HEIC")


def test_heic_is_a_photo_not_a_video(client, photo_library):
    videos = client.get("/api/images/list", params={"folder": str(photo_library.dir), "kind": "video"}).json()
    assert not any(p.lower().endswith((".heic", ".heif")) for p in videos["images"])


# --- serving / conversion ----------------------------------------------------

@pytest.mark.parametrize("key", ["heic_full", "heif", "heic_p3"])
def test_heic_served_as_jpeg(client, photo_library, key):
    assert _get_jpeg(client, photo_library.files[key]).size == LANDSCAPE


def test_portrait_orientation_applied_once(client, photo_library):
    # Sensor data is landscape + Orientation 6; it must come out portrait (not sideways, not double-rotated)
    assert _get_jpeg(client, photo_library.files["heic_portrait"]).size == PORTRAIT


def test_large_heic_downscaled_to_max_edge(client, heic_48mp):
    img = _get_jpeg(client, heic_48mp)
    assert max(img.size) == heic.MAX_EDGE == 3840
    assert img.size == (3840, 2880)  # aspect ratio kept


def test_max_edge_is_configurable(client, photo_library, monkeypatch):
    monkeypatch.setattr(heic, "MAX_EDGE", 100)
    img = _get_jpeg(client, photo_library.files["heic_portrait"])
    assert img.size == (67, 100)


def test_p3_converted_to_srgb(client, photo_library):
    tagged = _get_jpeg(client, photo_library.files["heic_p3"])
    untagged = _get_jpeg(client, photo_library.files["heif"])  # same pixel values, no ICC profile
    assert "icc_profile" not in tagged.info  # pixels are plain sRGB, no P3 tag left for browsers
    r, g, b = tagged.getpixel((150, 100))
    ur, ug, ub = untagged.getpixel((150, 100))
    # Untagged stays put (treated as sRGB) ...
    assert abs(ur - P3_ORANGE[0]) <= 4 and abs(ub - P3_ORANGE[2]) <= 4
    # ... while P3 orange becomes a more saturated sRGB orange (~216, 93, 24) instead of looking washed out
    assert r >= ur + 10
    assert b <= ub - 15


def test_hdr_10bit_heic_converted_to_8bit_jpeg(client, tmp_path):
    import pillow_heif

    path = tmp_path / "hdr.heic"
    # 10-bit (RGB;16) frame, mid-grey
    pillow_heif.from_bytes(mode="RGB;16", size=(64, 48), data=b"\x00\x80" * 64 * 48 * 3).save(path, quality=-1)
    with Image.open(path) as src:
        assert src.info["bit_depth"] == 10
    img = _get_jpeg(client, path)
    assert img.mode == "RGB" and img.size == (64, 48)
    assert all(100 < c < 156 for c in img.getpixel((32, 24)))


def test_cached_after_first_request(client, photo_library, convert_calls, heic_cache_dir):
    path = photo_library.files["heic_full"]
    first = client.get("/api/image", params={"path": str(path)}).content
    second = client.get("/api/image", params={"path": str(path)}).content
    assert first == second
    assert len(convert_calls) == 1
    cached = list(heic_cache_dir.glob("*.jpg"))
    assert len(cached) == 1 and cached[0].read_bytes() == first


def test_cache_invalidated_when_source_changes(client, tmp_path, convert_calls):
    path = _make_heic(tmp_path / "edit.heic", size=LANDSCAPE)
    assert _get_jpeg(client, path).size == LANDSCAPE
    _make_heic(path, size=PORTRAIT)  # re-saved: new mtime/size
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert _get_jpeg(client, path).size == PORTRAIT
    assert len(convert_calls) == 2
    # Same bytes, newer mtime (e.g. edited and saved back at the same size) also reconverts
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    _get_jpeg(client, path)
    assert len(convert_calls) == 3


def test_cache_outside_photo_folder_and_original_untouched(client, tmp_path, heic_cache_dir):
    folder = tmp_path / "album"
    folder.mkdir()
    path = _make_heic(folder / "IMG_9999.HEIC", exif=_exif_bytes(date=True))
    before_bytes, before_mtime = path.read_bytes(), path.stat().st_mtime_ns
    _get_jpeg(client, path)
    assert [p.name for p in folder.iterdir()] == ["IMG_9999.HEIC"]
    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime
    assert any(heic_cache_dir.glob("*.jpg"))


def test_default_cache_dir_is_per_user_os_cache(monkeypatch, tmp_path):
    monkeypatch.delenv("PICME_CACHE_DIR")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / ".cache"))
    path = heic.cache_dir()
    assert path.is_dir() and path.name == "heic"
    assert tmp_path in path.parents
    assert "picme" in str(path).lower()


def test_corrupt_heic_415_and_not_retried(client, photo_library, convert_calls):
    path = photo_library.files["heic_corrupt"]
    for _ in range(3):
        r = client.get("/api/image", params={"path": str(path)})
        assert r.status_code == 415
    assert len(convert_calls) == 1  # later requests hit the negative cache instead of re-decoding


def test_corrupt_heic_retried_after_replacement(client, tmp_path, convert_calls):
    path = tmp_path / "later_fixed.heic"
    path.write_bytes(b"garbage")
    assert client.get("/api/image", params={"path": str(path)}).status_code == 415
    _make_heic(path)
    assert _get_jpeg(client, path).size == LANDSCAPE
    assert len(convert_calls) == 2


def test_unwritable_cache_is_415_not_500(client, photo_library, monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setenv("PICME_CACHE_DIR", str(blocker))
    r = client.get("/api/image", params={"path": str(photo_library.files["heic_full"])})
    assert r.status_code == 415
    # Other formats keep working
    assert client.get("/api/image", params={"path": str(photo_library.files["png"])}).status_code == 200


def test_cold_conversions_limited_to_two_at_a_time(client, tmp_path, monkeypatch):
    """A 6-slot layout on first start requests 6 uncached HEICs at once; only 2 may decode
    concurrently (each 48MP decode is several hundred MB)."""
    paths = [_make_heic(tmp_path / f"IMG_{i:04d}.HEIC", size=(64, 48)) for i in range(6)]
    real = heic._convert
    cond = threading.Condition()
    state = {"active": 0, "max": 0}

    def _spy(src, dst):
        with cond:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
            cond.notify_all()
            # Linger so overlapping calls are observed; stop early once the limit is clearly broken.
            cond.wait_for(lambda: state["active"] > 2, timeout=0.3)
        try:
            return real(src, dst)
        finally:
            with cond:
                state["active"] -= 1

    monkeypatch.setattr(heic, "_convert", _spy)
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda p: client.get("/api/image", params={"path": str(p)}), paths))
    assert [r.status_code for r in responses] == [200] * 6
    assert state["max"] == 2  # bounded, but still converting in parallel


def test_cache_hits_and_known_bad_dont_wait_for_busy_converters(client, photo_library, convert_calls):
    good = photo_library.files["heic_full"]
    bad = photo_library.files["heic_corrupt"]
    assert client.get("/api/image", params={"path": str(good)}).status_code == 200
    assert client.get("/api/image", params={"path": str(bad)}).status_code == 415
    # Occupy every conversion slot, as two long cold decodes would.
    for _ in range(heic.MAX_CONCURRENT_CONVERSIONS):
        assert heic._convert_slots.acquire(timeout=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            hit = pool.submit(client.get, "/api/image", params={"path": str(good)})
            known_bad = pool.submit(client.get, "/api/image", params={"path": str(bad)})
            assert hit.result(timeout=5).status_code == 200
            assert known_bad.result(timeout=5).status_code == 415
        finally:  # release before the pool joins, so a failing run doesn't hang
            for _ in range(heic.MAX_CONCURRENT_CONVERSIONS):
                heic._convert_slots.release()
    assert len(convert_calls) == 2


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_symlink_to_heic_converted(client, photo_library, tmp_path):
    link = tmp_path / "link.heic"
    try:
        link.symlink_to(photo_library.files["heic_portrait"])
    except OSError:
        pytest.skip("cannot create symlinks here")
    assert _get_jpeg(client, link).size == PORTRAIT


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_symlink_named_heic_to_other_file_rejected(client, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("nope")
    link = tmp_path / "sneaky.heic"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("cannot create symlinks here")
    assert client.get("/api/image", params={"path": str(link)}).status_code == 400


# --- metadata ----------------------------------------------------------------

def test_heic_metadata_overlays(client, photo_library, monkeypatch):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"address": {"city": "San Francisco", "state": "California"}}

    monkeypatch.setattr(exif_module.requests, "get", lambda *a, **k: Resp())
    r = client.get("/api/metadata", params={"path": str(photo_library.files["heic_full"])})
    assert r.status_code == 200
    assert r.json() == {
        "date_iso": EXIF_DATE_ISO,
        "location": "San Francisco, California",
        "camera": EXIF_MODEL,
        "megapixels": "0.1 MP",
        "fstop": "f/2.8",
        "shutter_speed": "1/250s",
        "iso": "ISO 200",
        "focal_length": "50mm",
    }


def test_heic_megapixels_reflect_original_not_converted_copy(client, heic_48mp):
    _get_jpeg(client, heic_48mp)  # converted copy is 3840x2880 (11.1 MP)
    meta = client.get("/api/metadata", params={"path": str(heic_48mp)}).json()
    assert meta["megapixels"] == "48.8 MP"
    assert meta["date_iso"] == EXIF_DATE_ISO
    assert meta["camera"] == EXIF_MODEL


def test_corrupt_heic_metadata_is_empty_not_500(client, photo_library):
    r = client.get("/api/metadata", params={"path": str(photo_library.files["heic_corrupt"])})
    assert r.status_code == 200
    assert all(v is None for v in r.json().values())


def test_metadata_reread_after_file_edit(tmp_path):
    path = _make_heic(tmp_path / "IMG_0100.HEIC")
    assert exif_module.extract_date(str(path)) is None
    _make_heic(path, exif=_exif_bytes(date=True))
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert exif_module.extract_date(str(path)) == EXIF_DATE_ISO
