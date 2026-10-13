"""Decorative frame assets (README → Frame assets): the generator, the committed files, serving.

The generator is imported from ``tools/frames/generate_frames.py`` and run for real; the
committed PNGs and ``frames.js`` must be exactly what it produces (compared as decoded pixels,
since PNG bytes depend on the zlib build).
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys

import pytest
from PIL import Image

from conftest import REPO_ROOT

FRAMES_DIR = REPO_ROOT / "frontend" / "assets" / "frames"
GENERATOR = REPO_ROOT / "tools" / "frames" / "generate_frames.py"
STYLES = ("wood", "gold")


@pytest.fixture(scope="module")
def gen():
    spec = importlib.util.spec_from_file_location("generate_frames", GENERATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def renders(gen):
    return {st.name: gen.render(st) for st in gen.STYLES}


def manifest_entries() -> dict[str, dict]:
    """The style entries of the committed frames.js, parsed as written."""
    text = (FRAMES_DIR / "frames.js").read_text(encoding="utf-8")
    entries = {}
    for name, body in re.findall(r"^\s*(\w+): \{(.*?)\},?$", text, re.M):
        fields = dict(re.findall(r"(\w+): ('[^']*'|\d+)", body))
        entries[name] = {k: v.strip("'") if v.startswith("'") else int(v) for k, v in fields.items()}
    return entries


def luminance(px) -> float:
    r, g, b = px
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def local_pixel(img: Image.Image, side: str, u: int, v: int):
    """Pixel at (u along the board, v in from the outer edge) on one side of a frame asset."""
    last = img.width - 1
    x, y = {"top": (u, v), "bottom": (u, last - v), "left": (v, u), "right": (last - v, u)}[side]
    return img.getpixel((x, y))


# --- Committed files are the generator's output ---

@pytest.mark.parametrize("name", STYLES)
def test_committed_assets_are_the_generators_pixels(renders, name):
    with Image.open(FRAMES_DIR / f"{name}.png") as committed:
        assert committed.mode == "RGB"
        assert committed.size == renders[name].size
        assert committed.tobytes() == renders[name].tobytes()


def test_rendering_is_deterministic(gen, renders):
    assert gen.render(gen.Gold).tobytes() == renders["gold"].tobytes()


def test_check_flags_stale_files_and_cli_exit_codes(gen, tmp_path):
    assert gen.check(FRAMES_DIR) == []
    ok = subprocess.run([sys.executable, str(GENERATOR), "--check"], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr

    stale = tmp_path / "frames"
    shutil.copytree(FRAMES_DIR, stale)
    img = Image.open(stale / "wood.png").convert("RGB")
    img.putpixel((0, 0), (255, 0, 255))
    img.save(stale / "wood.png")
    (stale / "frames.js").write_text((stale / "frames.js").read_text().replace("tile: 192", "tile: 190"))
    assert sorted(gen.check(stale)) == ["frames.js", "wood.png"]
    bad = subprocess.run([sys.executable, str(GENERATOR), "--check", "--out", str(stale)],
                         capture_output=True, text=True)
    assert bad.returncode == 1
    assert "wood.png" in bad.stderr and "frames.js" in bad.stderr


def test_cli_writes_assets_and_preview(tmp_path, renders):
    out, sheet = tmp_path / "out", tmp_path / "sheet.png"
    run = subprocess.run([sys.executable, str(GENERATOR), "--out", str(out), "--preview", str(sheet)],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    for name in STYLES:
        assert Image.open(out / f"{name}.png").tobytes() == renders[name].tobytes()
    assert (out / "frames.js").read_text() == (FRAMES_DIR / "frames.js").read_text()
    assert Image.open(sheet).size[0] == 1920


# --- Geometry ---

def test_manifest_describes_the_assets():
    entries = manifest_entries()
    assert sorted(entries) == sorted(STYLES)
    for name, e in entries.items():
        assert e["src"] == f"/assets/frames/{name}.png"
        assert e["label"] == name.capitalize()
        assert 16 <= e["default_width"] <= e["slice"]
        with Image.open(FRAMES_DIR / f"{name}.png") as img:
            # A square 9-slice: slice-px corners around one tile-px edge tile
            assert img.size == (2 * e["slice"] + e["tile"],) * 2
            assert img.mode == "RGB"  # opaque — no see-through wall behind the moulding
        assert (FRAMES_DIR / f"{name}.png").stat().st_size < 300_000


@pytest.mark.parametrize("name", STYLES)
@pytest.mark.parametrize("side", ["top", "bottom", "left", "right"])
def test_edge_tile_repeats_seamlessly(renders, name, side):
    """The column where one edge tile ends and the next begins must continue the texture."""
    e = manifest_entries()[name]
    S, T = e["slice"], e["tile"]
    img = renders[name]
    vs = range(S - 1)  # v = S - 1 at u = S + T is the mitre with the next side
    # u = S + T is the first column of the far corner; it equals the tile's first column
    assert [local_pixel(img, side, S, v) for v in vs] == [local_pixel(img, side, S + T, v) for v in vs]

    # So when tiled, tile end → tile start is an ordinary neighbouring-column step
    def step(u1, u2):
        return sum(abs(luminance(local_pixel(img, side, u1, v)) - luminance(local_pixel(img, side, u2, v)))
                   for v in vs) / len(vs)
    steps = [step(u, u + 1) for u in range(S, S + T - 1)]
    assert step(S + T - 1, S) <= max(steps)


@pytest.mark.parametrize("name", STYLES)
def test_corners_are_mitred_and_lit_from_the_top_left(renders, name):
    img = renders[name]
    S = manifest_entries()[name]["slice"]
    last = img.width - 1
    # A darker seam runs along each 45° mitre
    for flip_x, flip_y in [(False, False), (True, False), (False, True), (True, True)]:
        def at(x, y):
            return luminance(img.getpixel((last - x if flip_x else x, last - y if flip_y else y)))
        seam = sum(at(i, i) for i in range(4, S - 4))
        beside = sum((at(i + 2, i) + at(i, i + 2)) / 2 for i in range(4, S - 4))
        assert seam < 0.9 * beside, (name, flip_x, flip_y)

    # The outer slope of the moulding faces outwards: towards the light on the top and left
    # boards, away from it on the bottom and right ones
    def outer_slope(side):
        return sum(luminance(local_pixel(img, side, u, v)) for u in range(S, S + 64) for v in range(4, S // 6))
    assert outer_slope("top") > 1.1 * outer_slope("bottom")
    assert outer_slope("left") > 1.1 * outer_slope("right")


# --- Repo and serving ---

def test_frame_assets_are_not_git_ignored():
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    for name in STYLES:
        # Exit 1 = not ignored, despite the repo-wide *.png rule. --no-index: the PNGs are tracked,
        # and check-ignore skips tracked files otherwise
        r = subprocess.run(["git", "check-ignore", "-q", "--no-index", f"frontend/assets/frames/{name}.png"], cwd=REPO_ROOT)
        assert r.returncode == 1, f"{name}.png is git-ignored"
    # The *.png rule still keeps photos out
    assert subprocess.run(["git", "check-ignore", "-q", "--no-index", "photos/holiday.png"], cwd=REPO_ROOT).returncode == 0


def test_assets_are_served(client):
    r = client.get("/assets/frames/wood.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == (FRAMES_DIR / "wood.png").read_bytes()
    js = client.get("/assets/frames/frames.js")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]
    assert "window.PICME_FRAMES" in js.text


def test_assets_mount_does_not_leak_other_files(client, config_path):
    config_path.write_text('{"secret": "do-not-serve"}')
    for path in ("/assets/../backend/main.py", "/assets/%2e%2e/backend/main.py",
                 "/assets/..%2fbackend%2fmain.py", "/assets/frames/../../../README.md"):
        r = client.get(path)
        assert r.status_code == 404 or "FastAPI" not in r.text, path
        assert "do-not-serve" not in r.text
