"""``backend/layouts.py``: the layout validator, and ``/api/layouts`` skipping invalid files."""
from __future__ import annotations

import copy
import json
import logging

import pytest

import main
from conftest import LAYOUTS_DIR
from layouts import KNOWN_SHAPES, load_layouts, validate_layout

GOOD = {
    "id": "t", "name": "T", "description": "test",
    "grid_template_columns": "1fr 2fr", "grid_template_rows": "1fr",
    "slots": [{"id": 1, "grid_area": "1 / 1 / 2 / 2"}, {"id": 2, "grid_area": "1 / 2 / 2 / 3"}],
}


def variant(**changes) -> dict:
    layout = copy.deepcopy(GOOD)
    for key, value in changes.items():
        if value is None:
            layout.pop(key)
        else:
            layout[key] = value
    return layout


def test_good_layout_is_valid():
    assert validate_layout(GOOD) == []


@pytest.mark.parametrize("layout, fragment", [
    ([], "JSON object"),
    (variant(id=""), "id must be"),
    (variant(name=None), "name must be"),
    (variant(description=3), "description must be"),
    (variant(grid_template_columns="100px 1fr"), "grid_template_columns"),
    (variant(grid_template_rows="repeat(2, 1fr)"), "grid_template_rows"),
    (variant(grid_template_rows="0fr"), "grid_template_rows"),
    (variant(gallery="yes"), "gallery must be"),
    (variant(slots=[]), "1-12 slots"),
    (variant(slots=[{"id": i, "grid_area": "1 / 1 / 2 / 2"} for i in range(1, 14)]), "1-12 slots"),
    (variant(slots=[{"id": 2, "grid_area": "1 / 1 / 2 / 2"}, {"id": 1, "grid_area": "1 / 2 / 2 / 3"}]), "must have id 1"),
    (variant(slots=[{"id": True, "grid_area": "1 / 1 / 2 / 3"}]), "must have id 1"),
    (variant(slots=[{"id": 1, "grid_area": "1 / 1 / 2"}, {"id": 2, "grid_area": "1 / 2 / 2 / 3"}]), "grid_area must be"),
    (variant(slots=[{"id": 1, "grid_area": "1 / 1 / 2 / 4"}]), "outside the 1x2 grid"),
    (variant(slots=[{"id": 1, "grid_area": "1 / 1 / 2 / 3"}, {"id": 2, "grid_area": "1 / 2 / 2 / 3"}]), "overlap"),
    (variant(slots=[{"id": 1, "grid_area": "1 / 1 / 2 / 2"}]), "not covered"),
    (variant(slots=[{"id": 1, "grid_area": "1 / 1 / 2 / 2", "shape": "star"},
                    {"id": 2, "grid_area": "1 / 2 / 2 / 3"}]), "unknown shape 'star'"),
])
def test_invalid_layouts_are_reported(layout, fragment):
    errors = validate_layout(layout)
    assert any(fragment in e for e in errors), errors


def test_gallery_layout_may_leave_cells_empty_but_not_overlap():
    one = [{"id": 1, "grid_area": "1 / 1 / 2 / 2", "shape": "heart"}]
    assert validate_layout(variant(gallery=True, slots=one)) == []
    overlap = one + [{"id": 2, "grid_area": "1 / 1 / 2 / 3"}]
    assert any("overlap" in e for e in validate_layout(variant(gallery=True, slots=overlap)))


def test_every_known_shape_is_accepted():
    for shape in KNOWN_SHAPES:
        slots = [{"id": 1, "grid_area": "1 / 1 / 2 / 2", "shape": shape}, {"id": 2, "grid_area": "1 / 2 / 2 / 3"}]
        assert validate_layout(variant(slots=slots)) == [], shape


def test_load_layouts_keeps_file_order_and_skips_bad_files(tmp_path, caplog):
    (tmp_path / "b-good.json").write_text(json.dumps(variant(id="b-good")))
    (tmp_path / "a-good.json").write_text(json.dumps(variant(id="a-good")))
    (tmp_path / "c-broken.json").write_text("{ not json")
    (tmp_path / "d-overlap.json").write_text(json.dumps(variant(id="d", slots=[
        {"id": 1, "grid_area": "1 / 1 / 2 / 3"}, {"id": 2, "grid_area": "1 / 2 / 2 / 3"}])))
    with caplog.at_level(logging.WARNING):
        layouts = load_layouts(tmp_path)
    assert [l["id"] for l in layouts] == ["a-good", "b-good"]
    assert "c-broken.json" in caplog.text and "d-overlap.json" in caplog.text


def test_api_layouts_skips_an_invalid_file(client, tmp_path, monkeypatch, caplog):
    """A typo in one layout file must not take /api/layouts (and so the display) down."""
    for f in LAYOUTS_DIR.glob("*.json"):
        (tmp_path / f.name).write_text(f.read_text())
    (tmp_path / "zz-typo.json").write_text('{"id": "zz-typo", "slots": [')
    monkeypatch.setattr(main, "LAYOUTS_PATH", tmp_path)
    with caplog.at_level(logging.WARNING):
        r = client.get("/api/layouts")
    assert r.status_code == 200
    ids = [l["id"] for l in r.json()]
    assert ids == sorted(f.stem for f in LAYOUTS_DIR.glob("*.json"))
    assert "zz-typo.json" in caplog.text
