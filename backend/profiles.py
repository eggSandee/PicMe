"""Reset to defaults and saved presets (README → Presets and reset).

Settings fall into two kinds, defined once here:

- **look**: how the screen looks and behaves (layout, rotation, frames, gallery mat, metadata,
  Fill, time of day, and each slot's frame, metadata, timer, group, Fit/Fill, shape and video
  playback). Reset puts these back to the defaults; a preset saves and restores them.
- **media**: what is shown (the default folder and each slot's folder and photo/video mode) and
  how media is read (subfolders, video conversion, listing caps). Reset keeps these and a preset
  never holds them, so neither can blank the display or start re-converting the video library.

Both lists are allowlists for presets: a key a preset doesn't know is never written. Keys that
are neither (added by a later version, say) are left as they are by reset and apply.
"""
from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Top-level look keys. daily_layout_rotation is the legacy rotation flag (the editor migrates it).
LOOK_KEYS = (
    "layout", "wall_color", "show_metadata", "date_format", "max_metadata_pills", "label_position",
    "label_style", "fill_mode", "frame_defaults", "metadata_defaults", "layout_rotation", "gallery", "layout_modes",
    "time_of_day", "daily_layout_rotation",
)
# Kept by reset, never in a preset (for the record; anything not in LOOK_KEYS is kept)
MEDIA_KEYS = ("source_path", "include_subfolders", "video_max_edge", "include_live_photos",
              "media_max_files", "media_max_depth")
SLOT_LOOK_KEYS = ("frame", "metadata", "cycle_seconds", "group", "fill_mode", "shape",
                  "video_loops", "video_audio")
SLOT_MEDIA_KEYS = ("id", "mode", "source", "source_path", "include_subfolders")
USE_GLOBAL_LOOK = ("frame", "metadata")  # use_global.source goes with the folder (media)

DEFAULT_CYCLE_SECONDS = 30
PRESETS_VERSION = 1

# Names for the confirm dialog's list of what changes
GROUPS = {
    "layout": "Layout", "layout_rotation": "Daily rotation", "daily_layout_rotation": "Daily rotation",
    "layout_modes": "Frames per layout", "wall_color": "Wall color", "fill_mode": "Fill frames",
    "frame_defaults": "Frame", "gallery": "Gallery mat", "metadata_defaults": "Metadata",
    "show_metadata": "Metadata", "date_format": "Metadata", "max_metadata_pills": "Metadata",
    "label_position": "Metadata", "label_style": "Metadata", "time_of_day": "Time-of-day",
}


def _slot_look_defaults(defaults: dict) -> dict:
    first = (defaults.get("slots") or [{}])[0]
    return {"frame": {}, "metadata": {}, "cycle_seconds": first.get("cycle_seconds", DEFAULT_CYCLE_SECONDS)}


def _reset_slot(slot: dict, defaults: dict) -> dict:
    """The slot's media and any key this version doesn't know, with a default look."""
    out = {k: copy.deepcopy(v) for k, v in slot.items() if k not in SLOT_LOOK_KEYS and k != "use_global"}
    use_global = {"frame": True, "metadata": True}
    old = slot.get("use_global")
    if isinstance(old, dict) and "source" in old:
        use_global["source"] = old["source"]  # missing stays missing: the display reads it as adopted
    out["use_global"] = use_global
    out.update(copy.deepcopy(_slot_look_defaults(defaults)))
    return out


def reset_config(current: dict, defaults: dict) -> dict:
    """``current`` with every look setting back to ``defaults``. Folders, photo/video modes, video
    settings and the slot count (dormant slots too) are kept."""
    out = copy.deepcopy(current)
    for key in LOOK_KEYS:
        if key in defaults:
            out[key] = copy.deepcopy(defaults[key])
        else:
            out.pop(key, None)  # time_of_day: the editor puts back its disabled default
    slots = current.get("slots")
    out["slots"] = [_reset_slot(s, defaults) for s in slots if isinstance(s, dict)] if isinstance(slots, list) \
        else copy.deepcopy(defaults.get("slots", []))
    return out


def extract_look(config: dict) -> dict:
    """The look settings of ``config``: what a preset saves. Where a missing key doesn't mean the
    default (no fill_mode is Fit, not Fill), what the display does is saved, so applying the
    preset to a newer config looks the same."""
    look = {k: copy.deepcopy(config[k]) for k in LOOK_KEYS if k in config and k != "daily_layout_rotation"}
    for key, means in MISSING_MEANS.items():
        if key not in look:
            look[key] = copy.deepcopy(means)
        elif isinstance(means, dict) and isinstance(look[key], dict):
            look[key] = {**means, **look[key]}
    slots = []
    for slot in config.get("slots") or []:
        if not isinstance(slot, dict):
            continue
        s = {k: copy.deepcopy(slot[k]) for k in SLOT_LOOK_KEYS if k in slot}
        ug = slot.get("use_global")
        if isinstance(ug, dict):
            s["use_global"] = {k: ug[k] for k in USE_GLOBAL_LOOK if k in ug}
        slots.append(s)
    look["slots"] = slots
    return look


def apply_look(current: dict, settings: dict, defaults: dict, layout_ids: list[str],
               shapes: frozenset[str] | set[str]) -> tuple[dict, list[str]]:
    """``current`` with the preset's look. A look key the preset lacks (saved before that setting
    existed) gets its default. Returns the config and warnings for what couldn't be used."""
    warnings: list[str] = []
    if not isinstance(settings, dict):
        return reset_config(current, defaults), ["The preset has no settings; defaults were used."]
    out = reset_config(current, defaults)
    for key in LOOK_KEYS:
        if key in settings:
            out[key] = copy.deepcopy(settings[key])
    ignored = sorted(k for k in settings if k not in LOOK_KEYS and k != "slots")
    if ignored:
        warnings.append(f"Ignored settings this version doesn't know: {', '.join(ignored)}.")

    ids = set(layout_ids)
    if out.get("layout") not in ids:
        keep = current.get("layout") if current.get("layout") in ids else defaults.get("layout")
        warnings.append(f"Layout \"{out.get('layout')}\" no longer exists; kept {keep}.")
        out["layout"] = keep
    rotation = out.get("layout_rotation")
    if isinstance(rotation, dict) and isinstance(rotation.get("layouts"), list):
        kept = [i for i in rotation["layouts"] if i in ids]
        if len(kept) != len(rotation["layouts"]):
            gone = [str(i) for i in rotation["layouts"] if i not in ids]
            warnings.append(f"Removed layouts from daily rotation: {', '.join(gone)}.")
            if not kept and rotation.get("enabled"):
                # An empty list means "every layout": don't turn a short list into all of them
                rotation["enabled"] = False
                warnings.append("Daily rotation was turned off: none of its layouts exist.")
            rotation["layouts"] = kept
    modes = out.get("layout_modes")
    if isinstance(modes, dict):
        out["layout_modes"] = {k: v for k, v in modes.items() if k in ids and v in ("gallery", "slots")}
    elif "layout_modes" in out:
        out["layout_modes"] = {}

    preset_slots = settings.get("slots")
    if not isinstance(preset_slots, list):
        preset_slots = []
    unknown_slot_keys: set[str] = set()
    for i, ps in enumerate(preset_slots):
        if not isinstance(ps, dict):
            continue
        if i >= len(out["slots"]):
            # A slot the current config doesn't have yet: a photo slot on the default folder
            out["slots"].append({"id": i + 1, "mode": "photos", "source": "local",
                                 "source_path": current.get("source_path"),
                                 "use_global": {"source": True, "frame": True, "metadata": True},
                                 **copy.deepcopy(_slot_look_defaults(defaults))})
        slot = out["slots"][i]
        for key in SLOT_LOOK_KEYS:
            if key in ps:
                slot[key] = copy.deepcopy(ps[key])
        if isinstance(ps.get("use_global"), dict):
            for key in USE_GLOBAL_LOOK:
                if key in ps["use_global"]:
                    slot["use_global"][key] = ps["use_global"][key]
        unknown_slot_keys |= {k for k in ps if k not in SLOT_LOOK_KEYS and k != "use_global"}
        if "shape" in slot and slot["shape"] not in shapes:
            warnings.append(f"Slot {slot.get('id', i + 1)}: unknown opening shape \"{slot['shape']}\" ignored.")
            del slot["shape"]
    if unknown_slot_keys:
        warnings.append(f"Ignored slot settings this version doesn't know: {', '.join(sorted(unknown_slot_keys))}.")
    return out, warnings


def _leaves(value, path: tuple = ()) -> dict:
    """Leaf values by path; lists are leaves (a rotation list is one setting), empty dicts none."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out.update(_leaves(v, path + (str(k),)))
        return out
    return {path: value}


_MISSING = object()

# What the display does when a look key is missing, where that isn't DEFAULT_CONFIG's value: a
# config from before a setting existed keeps the old behaviour (README → Configuration)
MISSING_MEANS = {
    "fill_mode": "contain",
    "frame_defaults": {"mat_color": "#000000", "mat_width": 16, "show_frame": True, "frame_color": "#2c2318",
                       "frame_width": 8, "frame_style": "solid"},
    "metadata_defaults": {"show_date": False, "show_location": False, "show_camera": False,
                          "show_megapixels": False, "show_fstop": False, "show_shutter_speed": False,
                          "show_iso": False, "show_focal_length": False, "show_people": False,
                          "text_size": "medium", "pill_size": "medium"},
}


def effective_look(config: dict, defaults: dict) -> dict:
    """The look as the display sees it: missing keys filled in, so comparing two configs counts
    only what shows on screen differently."""
    out = {}
    for key in LOOK_KEYS:
        fallback = MISSING_MEANS.get(key, defaults.get(key))
        value = config.get(key, _MISSING)
        if isinstance(fallback, dict) and isinstance(value, dict):
            value = {**fallback, **value}
        out[key] = copy.deepcopy(fallback) if value is _MISSING else value
    tod = out.get("time_of_day")
    # Time-of-day templates do nothing while it's off
    out["time_of_day"] = tod if isinstance(tod, dict) and tod.get("enabled") is True else None
    out.pop("daily_layout_rotation", None)
    if config.get("daily_layout_rotation") is True and "layout_rotation" not in config:
        out["layout_rotation"] = {"enabled": True, "layouts": [], "change_at": "00:00"}
    slots = []
    for slot in config.get("slots") or []:
        look = extract_look({"slots": [slot]})["slots"][0] if isinstance(slot, dict) else {}
        ug = look.pop("use_global", {})
        look.setdefault("frame", {})
        look.setdefault("metadata", {})
        look["use_global"] = {k: ug.get(k) is not False for k in USE_GLOBAL_LOOK}
        look.setdefault("cycle_seconds", DEFAULT_CYCLE_SECONDS)
        look.setdefault("fill_mode", None)
        slots.append(look)
    out["slots"] = slots
    return out


def changed_settings(before: dict, after: dict, defaults: dict) -> list[dict]:
    """Look settings that differ between two configs, as ``{group, setting}``. Time-of-day counts
    once however many of its colours change."""
    b_eff, a_eff = effective_look(before, defaults), effective_look(after, defaults)
    changes = []
    for key in LOOK_KEYS:
        if key not in b_eff or b_eff[key] == a_eff[key]:
            continue
        group = GROUPS.get(key, key)
        b, a = b_eff[key], a_eff[key]
        if key == "time_of_day" or not (isinstance(b, dict) and isinstance(a, dict)):
            changes.append({"group": group, "setting": key})
            continue
        lb, la = _leaves(b), _leaves(a)
        for path in sorted(set(lb) | set(la)):
            if lb.get(path, _MISSING) != la.get(path, _MISSING):
                changes.append({"group": group, "setting": ".".join((key,) + path)})
    bs, as_ = b_eff["slots"], a_eff["slots"]
    for i in range(max(len(bs), len(as_))):
        lb = _leaves(bs[i]) if i < len(bs) else {}
        la = _leaves(as_[i]) if i < len(as_) else {}
        for path in sorted(set(lb) | set(la)):
            if lb.get(path, _MISSING) != la.get(path, _MISSING):
                changes.append({"group": f"Slot {i + 1}", "setting": ".".join(path)})
    return changes


# --- Files next to config.json ---

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path):
    """The file's JSON, or None if it's missing or unreadable (logged)."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as e:
        log.warning("Ignoring unreadable %s: %s", path, e)
        return None


def read_presets(path: Path) -> list[dict]:
    """``presets.json`` is ``{"version": 1, "presets": [{id, name, created, updated, settings}]}``;
    a missing, corrupt or hand-broken file reads as no presets."""
    data = read_json(path)
    presets = data.get("presets") if isinstance(data, dict) else None
    if not isinstance(presets, list):
        return []
    return [p for p in presets if isinstance(p, dict) and isinstance(p.get("id"), str)]


def upsert_preset(presets: list[dict], preset_id: str, name: str, settings: dict) -> tuple[list[dict], dict]:
    stamp = now_iso()
    old = next((p for p in presets if p["id"] == preset_id), None)
    preset = {"id": preset_id, "name": name, "created": (old or {}).get("created", stamp),
              "updated": stamp, "settings": settings}
    return [preset if p is old else p for p in presets] + ([] if old else [preset]), preset


def presets_document(presets: list[dict]) -> dict:
    return {"version": PRESETS_VERSION, "presets": presets}
