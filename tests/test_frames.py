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
STYLES = ("wood", "gold", "black", "silver", "white", "barnwood")
# label, default width (separate frames), edge tile length
EXPECTED = {
    "wood": ("Wood", 28, 192),
    "gold": ("Gold", 36, 192),
    "black": ("Black gallery", 24, 192),
    "silver": ("Brushed silver", 16, 192),
    "white": ("White modern", 24, 192),
    "barnwood": ("Rustic barnwood", 36, 576),
}
SIDES = ("top", "bottom", "left", "right")


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
    assert list(entries) == list(STYLES)  # the editor's pickers list them in this order
    for name, e in entries.items():
        assert e["src"] == f"/assets/frames/{name}.png"
        assert (e["label"], e["default_width"], e["tile"]) == EXPECTED[name]
        assert 16 <= e["default_width"] <= e["slice"]
        with Image.open(FRAMES_DIR / f"{name}.png") as img:
            # A square 9-slice: slice-px corners around one tile-px edge tile
            assert img.size == (2 * e["slice"] + e["tile"],) * 2
            assert img.mode == "RGB"  # opaque — no see-through wall behind the moulding
        assert (FRAMES_DIR / f"{name}.png").stat().st_size < 300_000


@pytest.mark.parametrize("name", STYLES)
@pytest.mark.parametrize("side", SIDES)
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

    # The outer round-over of the moulding faces outwards: towards the light on the top and left
    # boards, away from it on the bottom and right ones. (Further in, a flat or stepped face may
    # tilt either way: silver's face leans in towards the picture.)
    def outer_slope(side):
        return sum(luminance(local_pixel(img, side, u, v)) for u in range(S, S + 64) for v in range(1, 4))
    assert outer_slope("top") > 1.1 * outer_slope("bottom")
    assert outer_slope("left") > 1.1 * outer_slope("right")


# --- The new styles' looks (design vote 2026-10-20, reviewer notes) ---

def band(img, side, v0, v1, step=3) -> float:
    """Mean luminance of one side's edge tile between v0 and v1 (v in from the outer edge)."""
    S = manifest_entries()["wood"]["slice"]
    us = range(S, img.width - S, step)
    return sum(luminance(local_pixel(img, side, u, v)) for u in us for v in range(v0, v1)) / (len(us) * (v1 - v0))


@pytest.mark.parametrize("side", SIDES)
def test_black_has_a_light_hairline_and_a_soft_black_face(renders, side):
    img = renders["black"]
    face = band(img, side, 20, 70)
    # Not pure black: the face and its outline still show on a black wall (night mode)
    assert face >= 45, face
    # A hairline just inside the rounded outer edge, lighter than the face on every side —
    # including bottom and right, which face away from the light
    assert band(img, side, 6, 9) >= 1.4 * face


@pytest.mark.parametrize("side", SIDES)
def test_silver_has_a_visible_step_and_a_brushed_sheen(renders, side):
    img = renders["silver"]
    face, step, lip = band(img, side, 4, 54), band(img, side, 56, 62), band(img, side, 64, 88)
    # The step between the face and the inner lip reads as a darker line
    assert step < 0.85 * face and step < 0.85 * lip, (face, step, lip)
    # A gentle gradient across the face: brighter outside, darker towards the step
    assert band(img, side, 6, 16) > 1.1 * band(img, side, 44, 54)
    # A mid grey, not near-white: the edge holds up on a light wall
    assert face < 170


@pytest.mark.parametrize("side", SIDES)
def test_white_has_a_tinted_lip_and_an_inner_shadow(renders, side):
    img = renders["white"]
    face = band(img, side, 20, 70)
    assert face > 190  # white
    lip, sight = band(img, side, 82, 90), band(img, side, 92, 96)
    # The lip is tinted darker and the sight edge darker still, so the frame doesn't run into
    # an ivory mat (the gallery default #f4f1ea)
    assert lip < 0.9 * face and sight < lip and sight < 0.75 * luminance((0xf4, 0xf1, 0xea))
    # Tinted, not just shaded: the lip is warmer (less blue) than the face
    r, g, b = local_pixel(img, side, 200, 86)
    fr, fg, fb = local_pixel(img, side, 200, 40)
    assert b / r < fb / fr


def test_barnwood_does_not_repeat_every_192_px(renders):
    """QA in the design vote: barnwood's cracks and knot recurred every ~120 px on screen.
    Its edge tile is three times as long, and holds no 192 px repeat inside it."""
    img = renders["barnwood"]
    S, T = manifest_entries()["barnwood"]["slice"], manifest_entries()["barnwood"]["tile"]
    assert T == 576 and img.size == (2 * S + T,) * 2

    def diff(side_a, side_b, shift):
        us, vs = range(S, S + T, 4), range(8, 88, 2)
        return sum(abs(luminance(local_pixel(img, side_a, u, v)) -
                       luminance(local_pixel(img, side_b, S + (u - S + shift) % T, v)))
                   for u in us for v in vs) / (len(us) * len(vs))

    neighbour = diff("top", "top", 1)
    for side in SIDES:
        for shift in (T // 3, 2 * T // 3):
            assert diff(side, side, shift) > 3 * neighbour, (side, shift)
    # Each side is cut from a different stretch of the board
    assert diff("top", "bottom", 0) > 3 * neighbour
    assert diff("left", "right", 0) > 3 * neighbour


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
