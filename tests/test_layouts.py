"""Static checks for every ``layouts/*.json`` template."""
from __future__ import annotations

import json
import re

import pytest

from conftest import LAYOUTS_DIR, REPO_ROOT

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
    assert set(covered) == grid, f"gaps at {sorted(grid - set(covered))}"


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
