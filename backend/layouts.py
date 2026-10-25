"""Layout templates (README → Layout Template Reference): validation and loading.

A layout is a CSS grid: ``fr`` tracks and one ``grid_area`` per slot. Separate-frame layouts
must cover every cell; gallery layouts (``"gallery": true``) may leave cells empty, which show
as plain mat. ``/api/layouts`` serves only layouts that pass, so a typo in one file can't
break the display.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

# Opening shapes (gallery mode). Must match frontend/display/shapes.js — tests check both agree.
KNOWN_SHAPES = frozenset({"rect", "rounded", "ellipse", "circle", "arch", "hexagon", "heart"})

MAX_SLOTS = 12
_TRACK = re.compile(r"^\d+(\.\d+)?fr$")


def _tracks(template) -> int | None:
    """Number of tracks in ``"1fr 2fr ..."``, or None if it isn't only ``fr`` tracks."""
    if not isinstance(template, str) or not template.split():
        return None
    parts = template.split()
    return len(parts) if all(_TRACK.match(p) and float(p[:-2]) > 0 for p in parts) else None


def _area(value) -> tuple[int, int, int, int] | None:
    """``"row-start / col-start / row-end / col-end"`` -> ints, or None if malformed."""
    if not isinstance(value, str):
        return None
    parts = [p.strip() for p in value.split("/")]
    if len(parts) != 4 or not all(re.fullmatch(r"\d+", p) for p in parts):
        return None
    return tuple(int(p) for p in parts)  # type: ignore[return-value]


# Picker position: layouts without an `order` come after the ones that set a smaller one (Single is 0)
DEFAULT_ORDER = 100


def validate_layout(obj, shapes=KNOWN_SHAPES) -> list[str]:
    """Everything wrong with a layout object; an empty list means it is valid."""
    if not isinstance(obj, dict):
        return ["layout must be a JSON object"]
    errors = []
    for key in ("id", "name", "description"):
        if not isinstance(obj.get(key), str) or not obj[key].strip():
            errors.append(f"{key} must be a non-empty string")
    rows = _tracks(obj.get("grid_template_rows"))
    cols = _tracks(obj.get("grid_template_columns"))
    if rows is None:
        errors.append("grid_template_rows must be fr tracks, e.g. '1fr 2fr'")
    if cols is None:
        errors.append("grid_template_columns must be fr tracks, e.g. '1fr 2fr'")
    gallery = obj.get("gallery", False)
    if not isinstance(gallery, bool):
        errors.append("gallery must be true or false")
    if not isinstance(obj.get("frame", True), bool):
        errors.append("frame must be true or false")
    order = obj.get("order", DEFAULT_ORDER)
    if not isinstance(order, int) or isinstance(order, bool):
        errors.append("order must be a whole number")

    slots = obj.get("slots")
    if not isinstance(slots, list) or not 1 <= len(slots) <= MAX_SLOTS:
        errors.append(f"slots must be a list of 1-{MAX_SLOTS} slots")
        return errors
    covered: dict[tuple[int, int], int] = {}
    for i, slot in enumerate(slots, start=1):
        if not isinstance(slot, dict):
            errors.append(f"slot {i} must be an object")
            continue
        if slot.get("id") != i or isinstance(slot.get("id"), bool):
            errors.append(f"slot {i} must have id {i} (ids run 1..N in order)")
        if "shape" in slot and slot["shape"] not in shapes:
            errors.append(f"slot {i}: unknown shape {slot['shape']!r}")
        area = _area(slot.get("grid_area"))
        if area is None:
            errors.append(f"slot {i}: grid_area must be 'row / col / row-end / col-end'")
            continue
        if rows is None or cols is None:
            continue
        r0, c0, r1, c1 = area
        if not (1 <= r0 < r1 <= rows + 1 and 1 <= c0 < c1 <= cols + 1):
            errors.append(f"slot {i}: grid_area {slot['grid_area']!r} is outside the {rows}x{cols} grid")
            continue
        for cell in ((r, c) for r in range(r0, r1) for c in range(c0, c1)):
            if cell in covered:
                errors.append(f"slots {covered[cell]} and {i} overlap at row {cell[0]}, column {cell[1]}")
                break
            covered[cell] = i
    if rows and cols and gallery is not True and not errors:
        missing = [(r, c) for r in range(1, rows + 1) for c in range(1, cols + 1) if (r, c) not in covered]
        if missing:
            errors.append(f"cells not covered by any slot: {missing} (only gallery layouts may leave cells empty)")
    return errors


def load_layouts(path: Path) -> list[dict]:
    """Every valid ``*.json`` layout in ``path``: by ``order`` (smallest first, default
    DEFAULT_ORDER), then file name. Invalid or unreadable files are logged and skipped."""
    layouts = []
    for f in sorted(Path(path).glob("*.json")):
        try:
            obj = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("Skipping layout %s: %s", f.name, e)
            continue
        errors = validate_layout(obj)
        if errors:
            log.warning("Skipping layout %s: %s", f.name, "; ".join(errors))
            continue
        layouts.append(obj)
    layouts.sort(key=lambda l: l.get("order", DEFAULT_ORDER))  # stable: file-name order within an order
    # Custom layouts (README → `custom`) would be validated the same way and appended here,
    # e.g. from config.get("custom_layouts"); the renderer accepts any layout that passes.
    return layouts
