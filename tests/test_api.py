"""HTTP-level tests for every route in ``backend/main.py``."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from conftest import EXIF_DATE_ISO, LAYOUTS_DIR, make_config


# --- /api/config ---

def test_config_get_without_file_creates_defaults(client, config_path):
    assert not config_path.exists()
    r = client.get("/api/config")
    assert r.status_code == 200
    cfg = r.json()
    assert cfg["layout"] == "classic-4"
    assert cfg["source_path"] == "photos"
    assert [s["id"] for s in cfg["slots"]] == [1, 2, 3, 4]
    assert all(s["mode"] == "photos" and s["source_path"] == "photos" for s in cfg["slots"])
    assert cfg["show_metadata"] is True
    assert cfg["metadata_defaults"]["show_date"] is True
    assert cfg["frame_defaults"]["show_frame"] is True
    # Persisted on first run, so the editor and a restart see the same thing
    assert json.loads(config_path.read_text()) == cfg
    assert client.get("/api/config").json() == cfg


def test_default_config_layout_exists_with_matching_slot_count():
    layout = json.loads((LAYOUTS_DIR / f"{main.DEFAULT_CONFIG['layout']}.json").read_text())
    assert len(layout["slots"]) == len(main.DEFAULT_CONFIG["slots"])


def test_config_get_does_not_overwrite_existing_empty_config(client, config_path):
    config_path.write_text("{}")
    assert client.get("/api/config").json() == {}
    assert config_path.read_text() == "{}"


def test_config_get_does_not_overwrite_config_without_slots(client, config_path):
    config_path.write_text(json.dumps({"layout": "classic-6"}))
    assert client.get("/api/config").json() == {"layout": "classic-6"}
    assert json.loads(config_path.read_text()) == {"layout": "classic-6"}


def test_lifespan_does_not_create_config(config_path):
    with TestClient(main.app):
        pass
    assert not config_path.exists()


def test_load_config_without_file_returns_defaults_without_writing(config_path):
    assert main.load_config() == main.default_config()
    assert not config_path.exists()


def test_default_config_is_not_shared():
    a = main.default_config()
    a["slots"][0]["source_path"] = "/elsewhere"
    a["metadata_defaults"]["show_date"] = False
    b = main.default_config()
    assert b["slots"][0]["source_path"] == "photos"
    assert b["metadata_defaults"]["show_date"] is True


def test_config_get_serves_defaults_when_config_cannot_be_written(client, config_path, monkeypatch):
    def read_only(config):
        raise PermissionError("read-only install")
    monkeypatch.setattr(main, "save_config", read_only)
    r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json() == main.default_config()
    assert not config_path.exists()


def test_config_get_returns_saved_file(client, written_config):
    r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json() == written_config


def test_config_post_round_trip(client, config_path, sample_config):
    r = client.post("/api/config", json=sample_config)
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert json.loads(config_path.read_text()) == sample_config
    assert client.get("/api/config").json() == sample_config


def test_config_post_overwrites_previous(client, written_config, photo_library):
    new = make_config(photo_library.odd_dir, layout="classic-6", slots=6, cycle_seconds=10)
    assert client.post("/api/config", json=new).status_code == 200
    assert client.get("/api/config").json() == new


def test_config_post_preserves_unicode_paths(client, photo_library):
    cfg = make_config(photo_library.odd_dir)
    client.post("/api/config", json=cfg)
    assert client.get("/api/config").json()["slots"][0]["source_path"] == str(photo_library.odd_dir)


@pytest.mark.parametrize("body", [[1, 2, 3], "a string", 42])
def test_config_post_rejects_non_object(client, config_path, body):
    r = client.post("/api/config", json=body)
    assert r.status_code == 422
    assert not config_path.exists()


def test_config_post_rejects_invalid_json(client, config_path):
    r = client.post("/api/config", content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert not config_path.exists()


def test_config_does_not_touch_real_config(config_path):
    assert main.CONFIG_PATH == config_path
    assert main.CONFIG_PATH != main.ROOT / "config.json"


# --- /api/layouts ---

def test_layouts_lists_every_layout_file(client):
    r = client.get("/api/layouts")
    assert r.status_code == 200
    layouts = r.json()
    files = sorted(LAYOUTS_DIR.glob("*.json"))
    assert len(layouts) == len(files) > 0
    assert [l["id"] for l in layouts] == [json.loads(f.read_text())["id"] for f in files]


def test_layouts_have_expected_shape(client):
    for layout in client.get("/api/layouts").json():
        assert {"id", "name", "slots", "grid_template_columns", "grid_template_rows"} <= layout.keys()
        assert layout["slots"], layout["id"]
        for slot in layout["slots"]:
            assert {"id", "grid_area"} <= slot.keys()


# --- /api/images/list ---

def test_images_list_returns_supported_files_only(client, photo_library):
    r = client.get("/api/images/list", params={"folder": str(photo_library.dir)})
    assert r.status_code == 200
    body = r.json()
    assert sorted(body["images"]) == photo_library.listed
    assert body["count"] == len(photo_library.listed)
    names = {Path(p).name for p in body["images"]}
    assert "UPPER.JPG" in names                      # case-insensitive extension
    assert "notes.txt" not in names
    assert "jpeg_named.txt" not in names             # extension, not content, decides
    assert "subdir" not in names
    assert "nested.jpg" not in names                 # not recursive


def test_images_list_empty_folder(client, photo_library):
    r = client.get("/api/images/list", params={"folder": str(photo_library.empty)})
    assert r.status_code == 200
    assert r.json() == {"images": [], "count": 0}


def test_images_list_missing_folder(client, tmp_path):
    r = client.get("/api/images/list", params={"folder": str(tmp_path / "nope")})
    assert r.status_code == 200
    assert r.json() == {"images": [], "count": 0}


def test_images_list_file_instead_of_folder(client, photo_library):
    r = client.get("/api/images/list", params={"folder": str(photo_library.files["png"])})
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_images_list_unicode_and_spaces(client, photo_library):
    r = client.get("/api/images/list", params={"folder": str(photo_library.odd_dir)})
    assert r.status_code == 200
    assert r.json()["images"] == [str(photo_library.odd_image)]


def test_images_list_dotdot_path(client, photo_library):
    dotted = photo_library.empty / ".." / photo_library.dir.name
    r = client.get("/api/images/list", params={"folder": str(dotted)})
    assert r.status_code == 200
    assert r.json()["count"] == len(photo_library.listed)


def test_images_list_missing_param(client):
    assert client.get("/api/images/list").status_code == 422


# --- /api/images/random ---

def test_images_random_returns_listed_image(client, photo_library):
    seen = set()
    for _ in range(20):
        r = client.get("/api/images/random", params={"folder": str(photo_library.dir)})
        assert r.status_code == 200
        seen.add(r.json()["path"])
    assert seen <= set(photo_library.listed)
    assert len(seen) > 1


def test_images_random_unicode_folder(client, photo_library):
    r = client.get("/api/images/random", params={"folder": str(photo_library.odd_dir)})
    assert r.status_code == 200
    assert r.json() == {"path": str(photo_library.odd_image)}


def test_images_random_empty_folder_404(client, photo_library):
    r = client.get("/api/images/random", params={"folder": str(photo_library.empty)})
    assert r.status_code == 404


def test_images_random_missing_folder_404(client, tmp_path):
    r = client.get("/api/images/random", params={"folder": str(tmp_path / "nope")})
    assert r.status_code == 404


def test_images_random_missing_param(client):
    assert client.get("/api/images/random").status_code == 422


# --- /api/image ---

@pytest.mark.parametrize("key,ctype", [
    ("jpeg_full", "image/jpeg"),
    ("jpeg_upper", "image/jpeg"),
    ("jpeg_gps_only", "image/jpeg"),
    ("png", "image/png"),
    ("gif", "image/gif"),
    ("webp", "image/webp"),
])
def test_image_serves_supported_formats(client, photo_library, key, ctype):
    path = photo_library.files[key]
    r = client.get("/api/image", params={"path": str(path)})
    assert r.status_code == 200
    assert r.headers["content-type"] == ctype
    assert r.content == path.read_bytes()


def test_image_unicode_path(client, photo_library):
    r = client.get("/api/image", params={"path": str(photo_library.odd_image)})
    assert r.status_code == 200
    assert r.content == photo_library.odd_image.read_bytes()


def test_image_dotdot_path(client, photo_library):
    target = photo_library.files["png"]
    dotted = photo_library.empty / ".." / target.parent.name / target.name
    r = client.get("/api/image", params={"path": str(dotted)})
    assert r.status_code == 200
    assert r.content == target.read_bytes()


def test_image_corrupt_file_still_served(client, photo_library):
    # The server does not validate image contents — only the extension.
    path = photo_library.files["corrupt"]
    r = client.get("/api/image", params={"path": str(path)})
    assert r.status_code == 200
    assert r.content == path.read_bytes()


def test_image_wrong_extension_served_by_extension(client, photo_library):
    path = photo_library.files["png_as_jpg"]
    r = client.get("/api/image", params={"path": str(path)})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"


@pytest.mark.parametrize("key", ["txt", "jpeg_as_txt"])
def test_image_unsupported_extension_400(client, photo_library, key):
    r = client.get("/api/image", params={"path": str(photo_library.files[key])})
    assert r.status_code == 400


def test_image_missing_file_404(client, photo_library):
    r = client.get("/api/image", params={"path": str(photo_library.dir / "nope.jpg")})
    assert r.status_code == 404


def test_image_directory_404(client, photo_library):
    r = client.get("/api/image", params={"path": str(photo_library.dir)})
    assert r.status_code == 404


def test_image_missing_param(client):
    assert client.get("/api/image").status_code == 422


# --- /api/metadata ---

METADATA_KEYS = {"date_iso", "location", "camera", "megapixels", "fstop",
                 "shutter_speed", "iso", "focal_length"}


def test_metadata_full_exif(client, photo_library):
    r = client.get("/api/metadata", params={"path": str(photo_library.files["jpeg_full"])})
    assert r.status_code == 200
    body = r.json()
    assert body.keys() == METADATA_KEYS
    assert body["date_iso"] == EXIF_DATE_ISO
    assert body["camera"] == "Canon EOS R5"
    assert body["fstop"] == "f/2.8"
    assert body["shutter_speed"] == "1/250s"
    assert body["iso"] == "ISO 200"
    assert body["focal_length"] == "50mm"
    assert body["megapixels"] == "0.1 MP"
    assert body["location"] is None          # network blocked in tests


def test_metadata_location_with_mocked_geocoder(client, photo_library, monkeypatch):
    from metadata import exif as exif_module

    monkeypatch.setattr(exif_module, "reverse_geocode", lambda lat, lon: "San Francisco, California")
    r = client.get("/api/metadata", params={"path": str(photo_library.files["jpeg_gps_only"])})
    assert r.status_code == 200
    assert r.json()["location"] == "San Francisco, California"
    assert r.json()["date_iso"] is None


@pytest.mark.parametrize("key", ["jpeg_no_exif", "png", "gif", "corrupt", "png_as_jpg"])
def test_metadata_without_exif_returns_cleanly(client, photo_library, key):
    r = client.get("/api/metadata", params={"path": str(photo_library.files[key])})
    assert r.status_code == 200
    body = r.json()
    assert body.keys() == METADATA_KEYS
    for k in METADATA_KEYS - {"megapixels"}:
        assert body[k] is None, k


def test_metadata_corrupt_file_has_no_megapixels(client, photo_library):
    r = client.get("/api/metadata", params={"path": str(photo_library.files["corrupt"])})
    assert r.json()["megapixels"] is None


def test_metadata_unicode_path(client, photo_library):
    r = client.get("/api/metadata", params={"path": str(photo_library.odd_image)})
    assert r.status_code == 200
    assert r.json()["date_iso"] == EXIF_DATE_ISO


def test_metadata_webp_date(client, photo_library):
    r = client.get("/api/metadata", params={"path": str(photo_library.files["webp"])})
    assert r.status_code == 200
    assert r.json()["date_iso"] == EXIF_DATE_ISO


def test_metadata_missing_file_404(client, photo_library):
    r = client.get("/api/metadata", params={"path": str(photo_library.dir / "nope.jpg")})
    assert r.status_code == 404


def test_metadata_directory_404(client, photo_library):
    r = client.get("/api/metadata", params={"path": str(photo_library.dir)})
    assert r.status_code == 404


def test_metadata_missing_param(client):
    assert client.get("/api/metadata").status_code == 422


# --- /api/browse ---

def test_browse_default_is_home(client):
    r = client.get("/api/browse")
    assert r.status_code == 200
    assert r.json()["path"] == str(Path.home())


def test_browse_lists_subdirectories_only(client, photo_library):
    r = client.get("/api/browse", params={"path": str(photo_library.root)})
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == str(photo_library.root.resolve())
    assert body["parent"] == str(photo_library.root.resolve().parent)
    names = [e["name"] for e in body["entries"]]
    assert names == sorted(["photos", "empty", "odd dir ü"])
    for e in body["entries"]:
        assert Path(e["path"]).is_dir()


def test_browse_hides_dotdirs_and_files(client, tmp_path):
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "visible").mkdir()
    (tmp_path / "file.jpg").write_bytes(b"x")
    body = client.get("/api/browse", params={"path": str(tmp_path)}).json()
    assert [e["name"] for e in body["entries"]] == ["visible"]


def test_browse_file_path_falls_back_to_parent(client, photo_library):
    r = client.get("/api/browse", params={"path": str(photo_library.files["png"])})
    assert r.status_code == 200
    assert r.json()["path"] == str(photo_library.dir.resolve())
    assert [e["name"] for e in r.json()["entries"]] == ["subdir"]


def test_browse_missing_leaf_falls_back_to_parent(client, photo_library):
    r = client.get("/api/browse", params={"path": str(photo_library.dir / "does-not-exist")})
    assert r.status_code == 200
    assert r.json()["path"] == str(photo_library.dir.resolve())


def test_browse_unicode_path(client, photo_library):
    r = client.get("/api/browse", params={"path": str(photo_library.odd_dir)})
    assert r.status_code == 200
    assert r.json()["path"] == str(photo_library.odd_dir.resolve())
    assert r.json()["entries"] == []


def test_browse_dotdot_is_resolved(client, photo_library):
    r = client.get("/api/browse", params={"path": str(photo_library.dir / "..")})
    assert r.status_code == 200
    assert r.json()["path"] == str(photo_library.root.resolve())


def test_browse_filesystem_root_has_no_parent(client):
    r = client.get("/api/browse", params={"path": "/"})
    assert r.status_code == 200
    assert r.json()["path"] == "/"
    assert r.json()["parent"] is None


def test_browse_deep_missing_path(config_path, tmp_path):
    with TestClient(main.app, raise_server_exceptions=False) as c:
        r = c.get("/api/browse", params={"path": str(tmp_path / "a" / "b" / "c")})
    assert r.status_code == 200
    assert r.json()["path"] == str(tmp_path.resolve())


# --- / redirect and static frontends ---

def test_root_redirects_to_display(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/display"


def test_root_redirect_followed_lands_on_display(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert r.content == (main.FRONTEND_DISPLAY / "index.html").read_bytes()


@pytest.mark.parametrize("mount,directory", [
    ("/display/", main.FRONTEND_DISPLAY),
    ("/editor/", main.FRONTEND_EDITOR),
])
def test_static_frontends_serve_index(client, mount, directory):
    r = client.get(mount)
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert r.content == (directory / "index.html").read_bytes()


@pytest.mark.parametrize("mount", ["/display", "/editor"])
def test_static_mount_without_slash_redirects(client, mount):
    r = client.get(mount, follow_redirects=False)
    assert r.status_code in (301, 307, 308)
    assert r.headers["location"].endswith(mount + "/")


@pytest.mark.parametrize("mount", ["/display/", "/editor/"])
def test_static_missing_file_404(client, mount):
    assert client.get(mount + "nope.js").status_code == 404


@pytest.mark.parametrize("mount", ["/display/", "/editor/"])
def test_static_rejects_traversal(client, mount):
    r = client.get(mount + "..%2F..%2Fbackend%2Fmain.py")
    assert r.status_code == 404
    assert "FastAPI" not in r.text


def test_unknown_api_route_404(client):
    assert client.get("/api/nope").status_code == 404
