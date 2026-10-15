"""Recursive, cached listing of media folders (photos and videos share it).

A media folder includes its subfolders (trips, years, albums) unless the slot opts out. The
walk is safe to point at a messy tree such as ~/Pictures:

- hidden files and folders (dotfiles, ._ AppleDouble, macOS UF_HIDDEN) are skipped;
- so are macOS packages (Photos/Lightroom libraries, apps, anything with Contents/Info.plist)
  and NAS/OS housekeeping folders (@eaDir, #recycle, $RECYCLE.BIN, …) — checked by name
  *before* descending, so the Photos library bundle is never even opened;
- symlinked folders are never followed, and a visited (device, inode) set stops loops through
  junctions or bind mounts; symlinked files are listed, as before (serving re-checks them);
- depth and file-count caps stop a runaway walk, with a warning in the log.

Listings are cached per directory and revalidated by directory mtime, which changes whenever
a direct child is added, removed or renamed: re-listing a tree of 50k photos in 500 folders
costs 500 stats, and a result checked in the last FRESH_S seconds costs nothing. A full rewalk
every MAX_AGE_S bounds staleness on network shares that update directory mtimes lazily.
"""
from __future__ import annotations

import logging
import os
import stat
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from cache import cache_root

log = logging.getLogger(__name__)

DEFAULT_MAX_DEPTH = 32
DEFAULT_MAX_FILES = 100_000
FRESH_S = 10.0         # a result checked this recently is returned without any stat
MAX_AGE_S = 300.0      # older than this → full rewalk (lazy directory mtimes on SMB/NFS)
RACY_S = 2.0           # a directory modified this close to its scan is rescanned next time
                       # (coarse mtimes: a file added in the same tick wouldn't bump it)
UNUSED_S = 3600.0      # forget results nobody has asked for in this long

SKIP_FILE_NAMES = {"thumbs.db", "desktop.ini", "icon\r"}
SKIP_DIR_NAMES = {
    "@eadir", "#recycle", "#snapshot", "@recycle", "@recently-snapshot",
    "$recycle.bin", "system volume information", "lost+found",
}
# Folders macOS shows as a single file. Never descended into: reading inside a Photos library
# can trigger a privacy prompt, and its originals/derivatives would all show up as duplicates.
PACKAGE_EXTENSIONS = {
    ".photoslibrary", ".photolibrary", ".aplibrary", ".migratedphotolibrary",
    ".app", ".bundle", ".framework", ".plugin", ".kext", ".pkg", ".mpkg",
    ".lrdata", ".lrlibrary", ".imovielibrary", ".fcpbundle", ".tvlibrary", ".musiclibrary",
    ".theater", ".sparsebundle", ".xcarchive", ".rtfd", ".pages", ".numbers", ".key", ".band",
}
_UF_HIDDEN = getattr(stat, "UF_HIDDEN", 0)


@dataclass(frozen=True)
class Walk:
    files: tuple[str, ...]       # joined under the root as given (not resolved), sorted per folder
    truncated: bool = False      # stopped at max_files
    depth_capped: bool = False   # folders deeper than max_depth were left out


@dataclass
class _Dir:
    mtime_ns: int
    scanned_at: float            # time.time() of the scandir
    files: tuple[str, ...]       # names of regular files (symlinked files included)
    subdirs: tuple[str, ...]     # names of folders to descend into (skip rules applied)

    def valid_for(self, mtime_ns: int) -> bool:
        return mtime_ns == self.mtime_ns and self.scanned_at - mtime_ns / 1e9 > RACY_S


@dataclass
class _Result:
    walk: Walk
    dirs: list                   # the _Dir objects it was built from, in walk order
    checked_at: float
    built_at: float
    used_at: float


_dirs: dict[str, _Dir] = {}      # directory path (as walked) -> its last listing
_results: dict[tuple, _Result] = {}
_locks: dict[tuple, threading.Lock] = {}
_locks_guard = threading.Lock()
_warned: set[tuple] = set()
_limits = {"max_depth": DEFAULT_MAX_DEPTH, "max_files": DEFAULT_MAX_FILES}


def configure(config: dict) -> None:
    """Caps from config.json (``media_max_depth`` / ``media_max_files``; not in the editor)."""
    for key, default in (("max_depth", DEFAULT_MAX_DEPTH), ("max_files", DEFAULT_MAX_FILES)):
        try:
            value = int(config.get(f"media_{key}", default))
        except (TypeError, ValueError):
            value = default
        _limits[key] = max(0 if key == "max_depth" else 1, value)


def invalidate() -> None:
    """Forget every cached listing (config saved, tests)."""
    with _locks_guard:
        _dirs.clear()
        _results.clear()
        _warned.clear()


def skip_dir(name: str) -> bool:
    """Skipped by name alone (hidden, housekeeping or a package)."""
    lower = name.lower()
    return (name.startswith(".") or lower in SKIP_DIR_NAMES
            or os.path.splitext(lower)[1] in PACKAGE_EXTENSIONS)


def _scan(path: str, mtime_ns: int) -> _Dir:
    files, subdirs = [], []
    scanned_at = time.time()
    try:
        with os.scandir(path) as entries:
            for e in entries:
                name = e.name
                try:
                    if e.is_dir(follow_symlinks=False):
                        if skip_dir(name) or _is_junction(e):
                            continue
                        if _UF_HIDDEN and e.stat(follow_symlinks=False).st_flags & _UF_HIDDEN:
                            continue
                        # A package with an extension we don't know (or none) still has this
                        if os.path.isfile(os.path.join(e.path, "Contents", "Info.plist")):
                            continue
                        subdirs.append(name)
                    elif not name.startswith(".") and name.lower() not in SKIP_FILE_NAMES and e.is_file():
                        files.append(name)
                except OSError:
                    continue
    except OSError as err:
        log.debug("Can't list %s: %s", path, err)
    files.sort()
    subdirs.sort()
    return _Dir(mtime_ns, scanned_at, tuple(files), tuple(subdirs))


def _is_junction(entry: os.DirEntry) -> bool:
    is_junction = getattr(entry, "is_junction", None)   # Python 3.12+, Windows only
    return bool(is_junction and is_junction())


def _cache_ident() -> tuple[int, int] | None:
    try:
        st = os.stat(cache_root())
        return st.st_dev, st.st_ino
    except OSError:
        return None


def _build(root: str, recursive: bool, extensions: frozenset, max_depth: int, max_files: int,
           force: bool) -> tuple[Walk, list[_Dir]] | None:
    """Depth-first over sorted entries, so a capped listing is the same subset every time."""
    files: list[str] = []
    used: list[_Dir] = []
    visited: set[tuple[int, int]] = set()
    skip = _cache_ident()
    truncated = depth_capped = False
    stack = [(root, 0)]
    while stack:
        path, depth = stack.pop()
        try:
            st = os.stat(path)
        except OSError:
            if path == root:
                return None
            continue
        ident = (st.st_dev, st.st_ino)
        if ident in visited or (ident == skip and path != root):
            continue
        visited.add(ident)
        entry = _dirs.get(path)
        if force or entry is None or not entry.valid_for(st.st_mtime_ns):
            entry = _dirs[path] = _scan(path, st.st_mtime_ns)
        used.append(entry)
        for name in entry.files:
            if os.path.splitext(name)[1].lower() in extensions:
                if len(files) >= max_files:
                    truncated = True
                    break
                files.append(os.path.join(path, name))
        if truncated:
            break
        if recursive and entry.subdirs:
            if depth >= max_depth:
                depth_capped = True
                continue
            stack.extend((os.path.join(path, name), depth + 1) for name in reversed(entry.subdirs))
    if truncated:
        _warn_once(root, "files", "Stopped listing %s after %d files — narrow the folder or raise media_max_files",
                   root, max_files)
    if depth_capped:
        _warn_once(root, "depth", "Not listing folders more than %d levels below %s — raise media_max_depth",
                   max_depth, root)
    return Walk(tuple(files), truncated, depth_capped), used


def _warn_once(root: str, cap: str, msg: str, *args) -> None:
    if (root, cap) not in _warned:
        _warned.add((root, cap))
        log.warning(msg, *args)


def walk(root: str | Path, recursive: bool = True, *, extensions, max_depth: int | None = None,
         max_files: int | None = None) -> Walk:
    """Media files (by extension) in ``root`` and, if ``recursive``, its subfolders."""
    root = str(Path(root))   # "photos/" and "./photos" list as photos/a.jpg, as before
    max_depth = _limits["max_depth"] if max_depth is None else max_depth
    max_files = _limits["max_files"] if max_files is None else max_files
    extensions = frozenset(e.lower() for e in extensions)
    key = (root, bool(recursive), extensions, max_depth, max_files)
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:   # concurrent callers for one folder wait for a single walk
        now = time.monotonic()
        prev = _results.get(key)
        if prev and now - prev.checked_at < FRESH_S:
            prev.used_at = now
            return prev.walk
        force = prev is None or now - prev.built_at >= MAX_AGE_S
        built = _build(root, bool(recursive), extensions, max_depth, max_files, force)
        if built is None:
            _results.pop(key, None)
            return Walk(())
        result, used = built
        if prev and not force and len(used) == len(prev.dirs) and all(a is b for a, b in zip(used, prev.dirs)):
            result = prev.walk   # nothing changed: keep the same tuple
        _results[key] = _Result(result, used, now, prev.built_at if prev and not force else now, now)
        _prune(now)
        return result


def _prune(now: float) -> None:
    stale = [k for k, r in _results.items() if now - r.used_at > UNUSED_S]
    if not stale:
        return
    for k in stale:
        del _results[k]
    live = {id(d) for r in _results.values() for d in r.dirs}
    for path in [p for p, d in _dirs.items() if id(d) not in live]:
        del _dirs[path]
