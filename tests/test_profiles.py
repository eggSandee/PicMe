"""Reset to defaults, the saved preset and one-step undo (README → Presets and reset), through the API."""
from __future__ import annotations

import json

import pytest

import main
from conftest import make_config


def customised(folder="/media/family", other="/media/videos") -> dict:
    """An owner's config: looks changed everywhere, slot 2 on its own video folder, 6 slots
    (slots 5–6 dormant on classic-4), no fill_mode (from before the setting)."""
    cfg = make_config(folder, layout="gallery-salon", slots=6)
    cfg |= {
        "source_path": folder, "include_subfolders": False, "video_max_edge": 3840, "include_live_photos": True,
        "wall_color": "#123456", "date_format": "iso", "max_metadata_pills": 5, "label_position": "photo",
        "label_style": "placard",
        "frame_defaults": {"mat_color": "#ff0000", "mat_width": 30, "show_frame": True, "frame_color": "#00ff00",
                           "frame_width": 12, "frame_style": "solid"},
        "metadata_defaults": {"show_date": True, "show_location": True, "text_size": "large"},
        "layout_rotation": {"enabled": True, "layouts": ["classic-4", "gallery-salon"], "change_at": "06:00"},
        "gallery": {"mat_color": "#000000", "gutter": 40},
        "layout_modes": {"classic-4": "gallery"},
        "time_of_day": {"enabled": True, "day_start": "07:00", "night_start": "20:00", "day": {}, "night": {}},
    }
    cfg["slots"][1] |= {"mode": "video", "source_path": other, "use_global": {"source": False, "frame": False,
                        "metadata": True}, "include_subfolders": True, "frame": {"mat_color": "#abcdef"},
                        "video_loops": 4, "video_audio": True, "fill_mode": "contain"}
    cfg["slots"][2] |= {"group": "A", "cycle_seconds": 90, "shape": "heart"}
    cfg["slots"][5] |= {"source_path": "/media/dormant", "use_global": {"source": False}}
    return cfg


MEDIA = ("source_path", "include_subfolders", "video_max_edge", "include_live_photos")


def assert_media_kept(before: dict, after: dict):
    for key in MEDIA:
        assert after[key] == before[key], key
    assert len(after["slots"]) == len(before["slots"])
    for b, a in zip(before["slots"], after["slots"]):
        for key in ("id", "mode", "source", "source_path", "include_subfolders"):
            assert a.get(key) == b.get(key), (b["id"], key)
        assert a["use_global"].get("source") == (b.get("use_global") or {}).get("source"), b["id"]


@pytest.fixture
def saved(client, config_path):
    cfg = customised()
    config_path.write_text(json.dumps(cfg))
    return cfg


def test_reset_puts_looks_back_and_keeps_media(client, config_path, saved):
    r = client.post("/api/config/reset").json()
    after = json.loads(config_path.read_text())
    assert r["config"] == after
    assert_media_kept(saved, after)
    d = main.DEFAULT_CONFIG
    for key in ("layout", "wall_color", "date_format", "max_metadata_pills", "label_position", "label_style",
                "fill_mode", "frame_defaults", "metadata_defaults", "layout_rotation", "gallery", "layout_modes"):
        assert after[key] == d[key], key
    assert after["layout"] == "classic-4" and after["layout_rotation"]["enabled"] is False
    assert "time_of_day" not in after
    for slot in after["slots"]:
        assert slot["use_global"]["frame"] is True and slot["use_global"]["metadata"] is True
        assert slot["frame"] == {} and slot["metadata"] == {} and slot["cycle_seconds"] == 30
        for key in ("group", "fill_mode", "shape", "video_loops", "video_audio"):
            assert key not in slot, (slot["id"], key)
    assert r["count"] > 20


def test_reset_dry_run_counts_and_writes_nothing(client, config_path, saved):
    before = config_path.read_text()
    r = client.post("/api/config/reset?dry_run=true").json()
    assert config_path.read_text() == before
    assert not main.backup_path().exists()
    groups = {c["group"] for c in r["changes"]}
    assert {"Layout", "Daily rotation", "Frame", "Gallery mat", "Metadata", "Wall color", "Fill frames",
            "Time-of-day", "Frames per layout", "Slot 2", "Slot 3"} <= groups
    assert r["count"] == len(r["changes"])
    # The count matches what the reset then changes
    assert client.post("/api/config/reset").json()["count"] == r["count"]


def test_dry_run_counts_only_what_shows_differently(client, config_path):
    # The first-run config: nothing differs from the defaults
    client.get("/api/config")
    assert client.post("/api/config/reset?dry_run=true").json() == {"changes": [], "count": 0}
    # A missing fill_mode is Fit and a missing frame width 8: both differ from the defaults (Fill, wood 28)
    cfg = main.default_config()
    del cfg["fill_mode"], cfg["frame_defaults"]["frame_width"]
    cfg["slots"][0].pop("cycle_seconds")  # missing means 30, the default: no change
    config_path.write_text(json.dumps(cfg))
    changes = client.post("/api/config/reset?dry_run=true").json()["changes"]
    assert changes == [{"group": "Fill frames", "setting": "fill_mode"},
                       {"group": "Frame", "setting": "frame_defaults.frame_width"}]


def test_undo_restores_the_exact_previous_config_once(client, config_path, saved):
    assert client.get("/api/config/backup").json() == {"exists": False}
    assert client.post("/api/config/undo").status_code == 404
    client.post("/api/config/reset")
    b = client.get("/api/config/backup").json()
    assert b["exists"] is True and b["reason"] == "reset" and b["saved_at"]
    r = client.post("/api/config/undo")
    assert r.status_code == 200 and r.json()["config"] == saved
    assert json.loads(config_path.read_text()) == saved
    assert client.get("/api/config/backup").json() == {"exists": False}
    assert client.post("/api/config/undo").status_code == 404


def test_preset_round_trip_restores_looks_and_keeps_media(client, config_path, saved):
    assert client.get("/api/presets").json() == {"presets": []}
    p = client.put("/api/presets/my-preset", json={"name": "My preset"}).json()["preset"]
    assert p["id"] == "my-preset" and p["name"] == "My preset" and p["created"] == p["updated"]
    assert "source_path" not in p["settings"] and "video_max_edge" not in p["settings"]
    assert all("source_path" not in s and "mode" not in s for s in p["settings"]["slots"])
    doc = json.loads(main.presets_path().read_text())
    assert doc["version"] == 1 and [x["id"] for x in doc["presets"]] == ["my-preset"]

    # Reset, then move the folders: applying the preset brings the looks back, not the old folders
    client.post("/api/config/reset")
    moved = json.loads(config_path.read_text())
    moved["source_path"] = "/media/new"
    moved["slots"][1]["source_path"] = "/media/new-videos"
    moved["slots"][1]["mode"] = "photos"
    config_path.write_text(json.dumps(moved))

    dry = client.post("/api/presets/my-preset/apply?dry_run=true").json()
    assert dry["count"] > 20 and dry["warnings"] == []
    assert json.loads(config_path.read_text()) == moved
    r = client.post("/api/presets/my-preset/apply").json()
    after = json.loads(config_path.read_text())
    assert r["config"] == after and r["count"] == dry["count"]
    assert_media_kept(moved, after)
    look = lambda c: {k: c.get(k) for k in ("layout", "wall_color", "frame_defaults", "layout_rotation", "gallery",
                                             "layout_modes", "time_of_day", "label_position", "label_style", "date_format",
                                             "max_metadata_pills")}
    assert look(after) == look(saved)
    # Saved as the display showed it: no fill_mode was Fit, a field left out was off
    assert after["fill_mode"] == "contain"
    meta = after["metadata_defaults"]
    assert {k: meta[k] for k in saved["metadata_defaults"]} == saved["metadata_defaults"]
    assert meta["show_camera"] is False and meta["pill_size"] == "medium"
    s2 = after["slots"][1]
    assert s2["frame"] == {"mat_color": "#abcdef"} and s2["use_global"]["frame"] is False
    assert s2["video_loops"] == 4 and s2["fill_mode"] == "contain"
    assert after["slots"][2]["shape"] == "heart" and after["slots"][2]["group"] == "A"
    # Undo goes back to before the apply
    assert client.post("/api/config/undo").json()["reason"] == "preset"
    assert json.loads(config_path.read_text()) == moved


def test_saving_again_keeps_created_and_name(client, saved):
    first = client.put("/api/presets/my-preset", json={"name": "Living room"}).json()["preset"]
    again = client.put("/api/presets/my-preset", json={}).json()["preset"]
    assert again["name"] == "Living room" and again["created"] == first["created"]
    assert len(client.get("/api/presets").json()["presets"]) == 1


def test_apply_drops_what_this_version_cant_use(client, config_path, saved):
    settings = {
        "layout": "removed-layout", "wall_color": "#222222", "future_setting": 1,
        "layout_rotation": {"enabled": True, "layouts": ["gone-1", "gone-2"], "change_at": "04:00"},
        "layout_modes": {"gone-1": "gallery", "classic-6": "gallery", "hero-left": "sideways"},
        "slots": [{"shape": "star", "future_slot_key": True}, {"fill_mode": "cover"}] + [{}] * 6,
    }
    main.presets_path().write_text(json.dumps({"version": 1, "presets": [
        {"id": "my-preset", "name": "Old", "created": "x", "updated": "x", "settings": settings}]}))
    r = client.post("/api/presets/my-preset/apply").json()
    after = json.loads(config_path.read_text())
    assert after["layout"] == saved["layout"] and after["wall_color"] == "#222222"
    assert "future_setting" not in after
    assert after["layout_rotation"] == {"enabled": False, "layouts": [], "change_at": "04:00"}
    assert after["layout_modes"] == {"classic-6": "gallery"}
    assert "shape" not in after["slots"][0] and "future_slot_key" not in after["slots"][0]
    assert after["slots"][1]["fill_mode"] == "cover"
    # 8 preset slots on a 6-slot config: two new photo slots on the default folder
    assert len(after["slots"]) == 8
    new = after["slots"][7]
    assert (new["id"], new["mode"], new["source_path"], new["use_global"]["source"]) == (8, "photos", saved["source_path"], True)
    text = " ".join(r["warnings"])
    for word in ("removed-layout", "future_setting", "gone-1", "turned off", "star", "future_slot_key"):
        assert word in text, word
    assert_media_kept(saved, {**after, "slots": after["slots"][:6]})


def test_corrupt_or_missing_presets_read_as_none(client, saved):
    assert client.post("/api/presets/my-preset/apply").status_code == 404
    main.presets_path().write_text("{not json")
    assert client.get("/api/presets").json() == {"presets": []}
    main.presets_path().write_text(json.dumps({"presets": [{"no": "id"}, "junk"]}))
    assert client.get("/api/presets").json() == {"presets": []}
    # Saving replaces the broken file
    client.put("/api/presets/my-preset", json={})
    assert [p["id"] for p in client.get("/api/presets").json()["presets"]] == ["my-preset"]


def test_reset_with_a_legacy_slot_keeps_it_on_the_default_folder(client, config_path):
    """A slot from before use_global (no use_global at all) follows the default folder on the
    display; reset mustn't change that by inventing use_global.source."""
    cfg = make_config("/media/family")
    cfg["source_path"] = "/media/family"
    config_path.write_text(json.dumps(cfg))
    after = client.post("/api/config/reset").json()["config"]
    assert all("source" not in s["use_global"] for s in after["slots"])
    assert [s["source_path"] for s in after["slots"]] == ["/media/family"] * 4
