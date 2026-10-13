"""Per-user cache directory for converted media (HEIC → JPEG, HEVC → H.264).

Always outside the repo and outside the photo folders, which PicMe only ever reads.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def cache_root() -> Path:
    """PICME_CACHE_DIR if set, else the OS's per-user cache location."""
    override = os.environ.get("PICME_CACHE_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "PicMe"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "PicMe" / "Cache"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "picme"


def cache_subdir(name: str) -> Path:
    """``cache_root() / name``, created if missing."""
    path = cache_root() / name
    path.mkdir(parents=True, exist_ok=True)
    return path
