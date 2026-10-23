"""Static checks for every ``layouts/*.json`` template."""
from __future__ import annotations

import json
import re

import pytest

from conftest import LAYOUTS_DIR, REPO_ROOT
from layouts import KNOWN_SHAPES, validate_layout

LAYOUT_FILES = sorted(LAYOUTS_DIR.glob("*.json"))
LAYOUT_IDS = [p.stem for p in LAYOUT_FILES]


def _readme_slot_counts() -> dict[str, str]:
    """Parse the README "Layout Template Reference" table into {name: slots}."""
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = text.split("## Layout Template Reference", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|", section, re.MULTILINE)
    return dict(rows)


README_SLOTS = _readme_slot_counts()


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _track_count(template: str) -> int:
    return len(template.split())


def _parse_area(area: str) -> tuple[int, int, int, int]:
    """``"row-start / col-start / row-end / col-end"`` -> ints."""
    parts = [p.strip() for p in area.split("/")]
    assert len(parts) == 4, f"grid_area {area!r} must have 4 parts"
    return tuple(int(p) for p in parts)  # type: ignore[return-value]


def _cells(area: str) -> set[tuple[int, int]]:
    r0, c0, r1, c1 = _parse_area(area)
    return {(r, c) for r in range(r0, r1) for c in range(c0, c1)}


def test_layouts_exist():
    assert LAYOUT_FILES, "no layouts/*.json found"


def test_readme_table_parsed():
    assert "custom" in README_SLOTS
    assert len(README_SLOTS) >= len(LAYOUT_FILES)


@pytest.mark.parametrize("path", LAYOUT_FILES, ids=LAYOUT_IDS)
def test_layout_parses_with_required_fields(path):
    layout = _load(path)
    assert layout["id"] == path.stem
    for key in ("name", "description", "grid_template_columns", "grid_template_rows"):
        assert isinstance(layout[key], str) and layout[key].strip(), key
    assert isinstance(layout["slots"], list) and layout["slots"]


@pytest.mark.parametrize("path", LAYOUT_FILES, ids=LAYOUT_IDS)
def test_slot_ids_are_sequential(path):
    ids = [s["id"] for s in _load(path)["slots"]]
    assert ids == list(range(1, len(ids) + 1))


@pytest.mark.parametrize("path", LAYOUT_FILES, ids=LAYOUT_IDS)
def test_slot_areas_within_grid(path):
    layout = _load(path)
    rows = _track_count(layout["grid_template_rows"])
    cols = _track_count(layout["grid_template_columns"])
    for slot in layout["slots"]:
        r0, c0, r1, c1 = _parse_area(slot["grid_area"])
        assert 1 <= r0 < r1 <= rows + 1, f"slot {slot['id']} rows out of grid"
        assert 1 <= c0 < c1 <= cols + 1, f"slot {slot['id']} cols out of grid"


@pytest.mark.parametrize("path", LAYOUT_FILES, ids=LAYOUT_IDS)
def test_slots_tile_grid_without_overlaps_or_gaps(path):
    layout = _load(path)
    rows = _track_count(layout["grid_template_rows"])
    cols = _track_count(layout["grid_template_columns"])
    covered: dict[tuple[int, int], int] = {}
    for slot in layout["slots"]:
        for cell in _cells(slot["grid_area"]):
            assert cell not in covered, (
                f"slots {covered[cell]} and {slot['id']} overlap at {cell}"
            )
            covered[cell] = slot["id"]
    grid = {(r, c) for r in range(1, rows + 1) for c in range(1, cols + 1)}
    # Gallery layouts may leave cells empty: they show as plain mat
    if layout.get("gallery") is not True:
        assert set(covered) == grid, f"gaps at {sorted(grid - set(covered))}"


@pytest.mark.parametrize("path", LAYOUT_FILES, ids=LAYOUT_IDS)
def test_gallery_layout_has_no_slotless_row_or_column(path):
    """An empty mat row or column would just be a wide strip of mat."""
    layout = _load(path)
    rows = _track_count(layout["grid_template_rows"])
    cols = _track_count(layout["grid_template_columns"])
    cells = set().union(*(_cells(s["grid_area"]) for s in layout["slots"]))
    assert {r for r, _ in cells} == set(range(1, rows + 1))
    assert {c for _, c in cells} == set(range(1, cols + 1))


def _shapes_js_names() -> set[str]:
    """Shape names in frontend/display/shapes.js (its ORDER list)."""
    text = (REPO_ROOT / "frontend" / "display" / "shapes.js").read_text(encoding="utf-8")
    order = re.search(r"const ORDER = \[([^\]]*)\]", text).group(1)
    return set(re.findall(r"'(\w+)'", order))


def test_shape_names_match_between_display_and_backend():
    assert _shapes_js_names() == set(KNOWN_SHAPES)


@pytest.mark.parametrize("path", LAYOUT_FILES, ids=LAYOUT_IDS)
def test_slot_shapes_known(path):
    for slot in _load(path)["slots"]:
        if "shape" in slot:
            assert slot["shape"] in _shapes_js_names(), f"slot {slot['id']}: {slot['shape']}"


@pytest.mark.parametrize("path", LAYOUT_FILES, ids=LAYOUT_IDS)
def test_gallery_flag_is_a_bool(path):
    layout = _load(path)
    assert isinstance(layout.get("gallery", False), bool)


@pytest.mark.parametrize("path", LAYOUT_FILES, ids=LAYOUT_IDS)
def test_backend_validator_accepts_every_shipped_layout(path):
    assert validate_layout(_load(path)) == []


def test_gallery_presets_ship():
    gallery = [p.stem for p in LAYOUT_FILES if _load(p).get("gallery") is True]
    assert len(gallery) >= 6
    # Asymmetric: some gallery layout has openings of different sizes
    assert any(len({len(_cells(s["grid_area"])) for s in _load(LAYOUTS_DIR / f"{g}.json")["slots"]}) > 1
               for g in gallery)


@pytest.mark.parametrize("path", LAYOUT_FILES, ids=LAYOUT_IDS)
def test_slot_count_matches_readme(path):
    layout = _load(path)
    assert layout["id"] in README_SLOTS, f"{layout['id']} missing from README table"
    assert len(layout["slots"]) == int(README_SLOTS[layout["id"]])


@pytest.mark.parametrize(
    "name", [n for n in README_SLOTS if n != "custom"]
)
def test_every_readme_template_has_a_file(name):
    assert (LAYOUTS_DIR / f"{name}.json").is_file()


def test_api_layouts_matches_files(client):
    r = client.get("/api/layouts")
    assert r.status_code == 200
    assert [l["id"] for l in r.json()] == LAYOUT_IDS
