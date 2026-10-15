"""The recursive media-folder walker (backend/media_index.py): what it lists, what it skips,
its caps and its per-directory mtime cache."""
from __future__ import annotations

import logging
import os
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

import main
import media_index

PHOTOS = main.SUPPORTED_EXTENSIONS


def walk(root, recursive=True, **kw) -> media_index.Walk:
    return media_index.walk(root, recursive, extensions=PHOTOS, **kw)


@pytest.fixture
def scans(monkeypatch) -> list[str]:
    """Paths of every directory the walker actually lists (scandir), in order."""
    calls: list[str] = []
    real = media_index._scan

    def counting(path, mtime_ns):
        calls.append(path)
        return real(path, mtime_ns)

    monkeypatch.setattr(media_index, "_scan", counting)
    return calls


def age_tree(root: Path, seconds: float = 100) -> None:
    """Back-date every folder's mtime, so the cache trusts it (a just-written folder is 'racy')."""
    old = time.time() - seconds
    for d, _, _ in os.walk(root):
        os.utime(d, (old, old))


# --- What is listed ---

def test_subfolders_are_listed(nested_library):
    assert sorted(walk(nested_library.root).files) == nested_library.included


def test_without_subfolders_only_top_level(nested_library):
    assert sorted(walk(nested_library.root, recursive=False).files) == nested_library.top


def test_skips_hidden_housekeeping_and_packages(nested_library):
    files = set(walk(nested_library.root).files)
    for p in nested_library.skipped:
        assert str(p) not in files
    assert str(nested_library.root / "Trip 2023.07" / "h.jpg") in files


def test_paths_joined_under_the_root_as_given(nested_library, monkeypatch):
    monkeypatch.chdir(nested_library.root.parent)
    assert "library/2023/trip/b.jpg" in walk("./library/").files


def test_sorted_depth_first(nested_library):
    files = [Path(f).relative_to(nested_library.root).as_posix() for f in walk(nested_library.root).files]
    expected = ["a.jpg", "file_link.jpg", "2023/trip/b.jpg", "2023/trip/deeper/c.heic", "Trip 2023.07/h.jpg"]
    if not nested_library.has_symlinks:
        expected.remove("file_link.jpg")
    assert files == expected


def test_symlinked_folders_not_followed_and_loops_end(nested_library):
    if not nested_library.has_symlinks:
        pytest.skip("symlinks unavailable")
    files = walk(nested_library.root).files
    assert len(files) == len(set(files))              # the loop added nothing twice
    assert not any("/loop/" in f or "/up/" in f for f in files)
    assert str(nested_library.root / "link_out" / "z.jpg") not in files
    assert str(nested_library.root / "file_link.jpg") in files   # symlinked files still listed


def test_bind_mount_style_repeat_is_visited_once(nested_library, monkeypatch):
    # A junction / bind mount isn't a symlink, so only the (device, inode) guard catches it:
    # make two folders report the same identity
    real_stat = os.stat
    trip = str(nested_library.root / "2023" / "trip")
    other = str(nested_library.root / "Trip 2023.07")

    def fake_stat(path, *a, **kw):
        return real_stat(trip if str(path) == other else path, *a, **kw)

    monkeypatch.setattr(media_index.os, "stat", fake_stat)
    files = walk(nested_library.root).files
    assert str(nested_library.root / "2023" / "trip" / "b.jpg") in files
    assert str(nested_library.root / "Trip 2023.07" / "h.jpg") not in files


def test_photos_library_bundle_is_never_opened(nested_library, scans):
    walk(nested_library.root)
    assert not any("photoslibrary" in p or ".app" in p or p.endswith("Pkg") for p in scans)


@pytest.mark.skipif(sys.platform != "darwin", reason="UF_HIDDEN is a macOS file flag")
def test_macos_hidden_flag_skips_folder(tmp_path):
    (tmp_path / "Hidden").mkdir()
    (tmp_path / "Hidden" / "a.jpg").write_bytes(b"x")
    os.chflags(tmp_path / "Hidden", stat.UF_HIDDEN)
    assert walk(tmp_path).files == ()


def test_picme_cache_dir_inside_the_folder_is_skipped(tmp_path, monkeypatch):
    cache = tmp_path / "Caches" / "PicMe"
    (cache / "heic").mkdir(parents=True)
    (cache / "heic" / "converted.jpg").write_bytes(b"x")
    (tmp_path / "keep.jpg").write_bytes(b"x")
    monkeypatch.setenv("PICME_CACHE_DIR", str(cache))
    assert walk(tmp_path).files == (str(tmp_path / "keep.jpg"),)


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions, not root")
def test_unreadable_subfolder_is_skipped(nested_library):
    locked = nested_library.root / "2023"
    locked.chmod(0)
    try:
        files = walk(nested_library.root).files
    finally:
        locked.chmod(0o755)
    assert str(nested_library.root / "a.jpg") in files
    assert not any("/2023/" in f for f in files)


def test_missing_root_is_empty(tmp_path):
    assert walk(tmp_path / "nope") == media_index.Walk(())


# --- Caps ---

def test_file_cap_truncates_deterministically_and_warns_once(nested_library, caplog):
    caplog.set_level(logging.WARNING, logger="media_index")
    first = walk(nested_library.root, max_files=2)
    assert first.truncated and len(first.files) == 2
    media_index._results.clear()   # walk again (the warning is still remembered)
    assert walk(nested_library.root, max_files=2).files == first.files
    assert sum("Stopped listing" in r.message for r in caplog.records) == 1


def test_depth_cap(nested_library, caplog):
    caplog.set_level(logging.WARNING, logger="media_index")
    w = walk(nested_library.root, max_depth=1)
    assert w.depth_capped and not w.truncated
    assert str(nested_library.root / "Trip 2023.07" / "h.jpg") in w.files        # depth 1
    assert str(nested_library.root / "2023" / "trip" / "b.jpg") not in w.files   # depth 2
    assert any("levels below" in r.message for r in caplog.records)


def test_caps_from_config(nested_library):
    media_index.configure({"media_max_files": 1, "media_max_depth": "bad"})
    w = walk(nested_library.root)
    assert w.truncated and len(w.files) == 1
    assert media_index._limits["max_depth"] == media_index.DEFAULT_MAX_DEPTH


def test_only_media_files_count_towards_the_cap(tmp_path):
    for i in range(5):
        (tmp_path / f"{i}.xmp").write_bytes(b"x")
    (tmp_path / "z.jpg").write_bytes(b"x")
    w = walk(tmp_path, max_files=1)
    assert w.files == (str(tmp_path / "z.jpg"),) and not w.truncated


# --- Cache ---

def test_repeat_within_fresh_window_does_no_io(nested_library, scans, monkeypatch):
    first = walk(nested_library.root)
    assert scans
    scans.clear()
    monkeypatch.setattr(media_index.os, "stat", lambda *a, **k: pytest.fail("stat during fresh window"))
    assert walk(nested_library.root) is first


def test_revalidation_rescans_only_changed_folders(nested_library, scans, monkeypatch):
    age_tree(nested_library.root)
    first = walk(nested_library.root)
    monkeypatch.setattr(media_index, "FRESH_S", 0)
    scans.clear()
    assert walk(nested_library.root) is first   # nothing changed: no scandir, same result
    assert scans == []

    deeper = nested_library.root / "2023" / "trip" / "deeper"
    (deeper / "new.jpg").write_bytes(b"x")       # bumps only deeper's mtime
    files = walk(nested_library.root).files
    assert str(deeper / "new.jpg") in files
    assert scans == [str(deeper)]


def test_deleted_subfolder_disappears(nested_library, monkeypatch):
    age_tree(nested_library.root)
    walk(nested_library.root)
    monkeypatch.setattr(media_index, "FRESH_S", 0)
    trip = nested_library.root / "2023" / "trip"
    for f in sorted(trip.rglob("*"), reverse=True):
        f.unlink() if f.is_file() or f.is_symlink() else f.rmdir()
    trip.rmdir()
    assert not any("/2023/" in f for f in walk(nested_library.root).files)


def test_just_modified_folder_is_rescanned(tmp_path, scans, monkeypatch):
    # Coarse mtimes (1–2 s on some filesystems) can hide a file added in the same tick, so a
    # folder modified right before its scan isn't trusted next time
    (tmp_path / "a.jpg").write_bytes(b"x")
    walk(tmp_path)
    monkeypatch.setattr(media_index, "FRESH_S", 0)
    scans.clear()
    walk(tmp_path)
    assert scans == [str(tmp_path)]


def test_max_age_forces_full_rewalk(nested_library, scans, monkeypatch):
    age_tree(nested_library.root)
    walk(nested_library.root)
    n = len(scans)
    monkeypatch.setattr(media_index, "FRESH_S", 0)
    monkeypatch.setattr(media_index, "MAX_AGE_S", 0)
    scans.clear()
    walk(nested_library.root)
    assert len(scans) == n


def test_invalidate_forgets_everything(nested_library, scans):
    walk(nested_library.root)
    media_index.invalidate()
    scans.clear()
    walk(nested_library.root)
    assert scans


def test_unused_results_are_dropped(nested_library, tmp_path, monkeypatch):
    walk(nested_library.root)
    other = tmp_path / "other"
    other.mkdir()
    for r in media_index._results.values():
        r.used_at -= media_index.UNUSED_S + 1
    walk(other)
    assert [k[0] for k in media_index._results] == [str(other)]
    assert set(media_index._dirs) == {str(other)}


def test_concurrent_callers_share_one_walk(nested_library, scans, monkeypatch):
    real = media_index._build
    monkeypatch.setattr(media_index, "_build", lambda *a: (time.sleep(0.2), real(*a))[1])
    results = []
    threads = [threading.Thread(target=lambda: results.append(walk(nested_library.root))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({id(r) for r in results}) == 1
    assert len(scans) == len(set(scans))


@pytest.mark.skipif(os.environ.get("PICME_PERF") != "1", reason="set PICME_PERF=1 to time a large tree")
def test_large_tree_walk_time(tmp_path, capsys):
    # ~50k photos in 1,000 folders (10 years × 100 albums × 50 files)
    root = tmp_path / "big"
    for y in range(10):
        for a in range(100):
            d = root / f"{2010 + y}" / f"album {a:03}"
            d.mkdir(parents=True)
            for i in range(50):
                (d / f"IMG_{i:04}.jpg").touch()
    age_tree(root)
    t0 = time.perf_counter()
    cold = walk(root)
    t1 = time.perf_counter()
    media_index.FRESH_S, fresh = 0, media_index.FRESH_S
    try:
        warm = walk(root)
    finally:
        media_index.FRESH_S = fresh
    t2 = time.perf_counter()
    with capsys.disabled():
        print(f"\n50k files / 1,010 folders: cold walk {t1 - t0:.2f}s, warm revalidation {t2 - t1:.3f}s")
    assert len(cold.files) == 50_000 and warm is cold
    assert t1 - t0 < 3 and t2 - t1 < 0.2
