#!/usr/bin/env python3
"""Generate PicMe's decorative picture-frame assets (README → Frame assets).

Writes ``wood.png``, ``gold.png`` and the ``frames.js`` manifest that the display and the
editor load. The PNGs are original procedural renders made by this script: no third-party
images, MIT-licensed like the rest of the repo. Output is deterministic (seeded noise, pure
float maths), so the committed files can always be regenerated and checked.

Each asset is a square 9-slice of ``2*S + T`` px for CSS ``border-image``: ``S`` px corners
(the slice) around a ``T`` px edge tile. Texture is a function of ``(u, v)`` (``u`` along the
board, ``v`` from the outer edge in towards the picture) that repeats every ``T`` px along
``u``, so ``border-image-repeat: round`` tiles the edges seamlessly and continuously with the
corners. Each pixel belongs to its nearest side, which gives 45° mitre joints. Lighting comes
from a height field per moulding, lit from the top left, so top and left boards catch the
light and bottom and right ones fall into shade.

Pillow only (no numpy). Run it with the project venv, from the repo root:

    .venv/bin/python tools/frames/generate_frames.py              # regenerate
    .venv/bin/python tools/frames/generate_frames.py --check      # verify the committed files
    .venv/bin/python tools/frames/generate_frames.py --preview /tmp/picme-frames-preview.png
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw

S = 96             # corner size = border-image-slice, and the moulding's depth in px
T = 192            # edge tile length
SIZE = 2 * S + T   # 384

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "frontend" / "assets" / "frames"
URL_PREFIX = "/assets/frames/"

SIDES = ("top", "bottom", "left", "right")
LIGHT = (-0.5, -0.62, 0.6)  # image coords: x right, y down, z towards the viewer


def _norm(x, y, z):
    n = math.sqrt(x * x + y * y + z * z)
    return x / n, y / n, z / n


def _smooth(e0, e1, x):
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def _mix(a, b, t):
    return tuple(p + (q - p) * t for p, q in zip(a, b))


class Noise:
    """Value noise on a lattice of ``cells_u`` x ``cells_v`` that wraps every ``T`` px along u
    (and every ``S`` px along v), so anything built from it tiles along the board."""

    def __init__(self, seed: int, cells_u: int, cells_v: int):
        rng = random.Random(seed)
        self.cu, self.cv = cells_u, cells_v
        self.su, self.sv = cells_u / T, cells_v / S
        self.g = [[rng.random() for _ in range(cells_v)] for _ in range(cells_u)]

    def __call__(self, u: float, v: float) -> float:
        x, y = u * self.su, v * self.sv
        i, j = math.floor(x), math.floor(y)
        fx, fy = x - i, y - j
        fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        i0, i1 = i % self.cu, (i + 1) % self.cu
        j0, j1 = j % self.cv, (j + 1) % self.cv
        g = self.g
        top = g[i0][j0] + (g[i1][j0] - g[i0][j0]) * fx
        bot = g[i0][j1] + (g[i1][j1] - g[i0][j1]) * fx
        return top + (bot - top) * fy


def _fbm(octaves, u, v):
    total = weight = 0.0
    for noise, amp in octaves:
        total += noise(u, v) * amp
        weight += amp
    return total / weight


def _wave(u, k, phase=0.0):
    """A sine with an integer number ``k`` of periods per edge tile, so it tiles."""
    return math.sin(2 * math.pi * k * u / T + phase)


# ── Wood: a walnut cushion moulding with a satin finish ──

class Wood:
    name, label, seed, default_width = "wood", "Wood", 1729, 28
    depth = 30.0                 # height-field scale: steeper slopes = stronger shading
    ambient, diffuse = 0.38, 0.85
    spec, shininess, spec_color = 0.16, 18, (255, 236, 205)
    # The four boards are cut from different parts of the plank: same grain, shifted along u
    side_shift = {"top": 0, "bottom": 71, "left": 133, "right": 29}
    seam = 0.62

    def __init__(self):
        s = self.seed
        self.warp = Noise(s + 1, 2, 2)
        self.wobble = [(Noise(s + 2, 2, 8), 1.0), (Noise(s + 3, 3, 16), 0.5)]
        self.pores = Noise(s + 4, 24, 64)
        self.fleck = Noise(s + 5, 96, 48)
        self.streak = Noise(s + 6, 4, 16)
        self.spacing = Noise(s + 7, 1, 8)    # varies across v only

    def height(self, u, v):
        if v < 5:                                  # outer round-over
            t = v / 5
            return 0.5 * math.sqrt(max(0.0, 1 - (1 - t) ** 2))
        if v < 72:                                 # broad cushion, crest towards the outside
            t = (v - 5) / 67
            return 0.5 + 0.5 * math.sin(math.pi * t ** 0.8)
        if v < 77:                                 # cut v-groove
            return 0.5 - 0.16 * (1 - abs(v - 74.5) / 2.5)
        if v < 87:                                 # inner flat band, falling gently
            return 0.5 - 0.06 * (v - 77) / 10
        if v < 91:                                 # round down into the rabbet
            t = (v - 87) / 4
            return 0.44 - 0.34 * t * t
        return 0.1

    def albedo(self, u, v):
        early, late = (158, 104, 62), (78, 46, 26)
        # Straight grain along the board. On screen the tile repeats every ~60 px, so the rings
        # only drift slightly along u; the character comes from their uneven spacing across v
        warp = 0.14 * _wave(u, 1, 0.7) + 0.06 * _wave(u, 2, 2.1) + 0.2 * (self.warp(u, v) - 0.5)
        wob = 0.12 * (_fbm(self.wobble, u, v) - 0.5)
        rc = v / 10.0 + 1.4 * self.spacing(0, v) + warp + wob
        ring = rc - math.floor(rc)
        latewood = _smooth(0.55, 0.8, ring) * (1 - _smooth(0.88, 1.0, ring))
        fine = v / 3.3 + 2.0 * (warp + wob) + 2.0 * self.spacing(0, v)
        fine_line = _smooth(0.7, 0.95, 0.5 + 0.5 * math.sin(2 * math.pi * fine))
        c = _mix(early, late, min(1.0, 0.8 * latewood + 0.22 * fine_line))
        # Open pores: short dark dashes stretched along the grain
        pore = _smooth(0.62, 0.85, self.pores(u, v))
        fleck = self.fleck(u, v)
        k = (1 - 0.16 * pore) * (0.95 + 0.1 * fleck)
        # A few dark mineral streaks
        k *= 1 - 0.18 * _smooth(0.66, 0.82, self.streak(u, v))
        if v >= 89:                                # shadowed rabbet lip
            k *= 0.35
        elif v < 2:                                # worn outer arris
            k *= 0.8
        return tuple(ch * k for ch in c)


# ── Gold: a gilded moulding with a torus, cove, bead row and sight-edge bead ──

class Gold:
    name, label, seed, default_width = "gold", "Gold", 4217, 36
    depth = 22.0
    ambient, diffuse = 0.28, 0.62
    spec, shininess, spec_color = 0.95, 14, (255, 238, 185)
    side_shift = {"top": 0, "bottom": 0, "left": 0, "right": 0}
    seam = 0.7
    BEAD_PERIOD = T // 8        # 24 px
    BEAD_V, BEAD_R = 53.0, 8.5

    def __init__(self):
        s = self.seed
        self.wear = [(Noise(s + 1, 8, 4), 1.0), (Noise(s + 2, 24, 12), 0.5), (Noise(s + 3, 64, 32), 0.3)]
        self.speck = Noise(s + 4, 96, 48)

    def height(self, u, v):
        if v < 4:                                  # outer edge
            return 0.35 * math.sqrt(max(0.0, 1 - (1 - v / 4) ** 2))
        if v < 30:                                 # big torus
            x = (v - 17) / 13
            return 0.35 + 0.65 * math.sqrt(max(0.0, 1 - x * x))
        if v < 44:                                 # cove
            x = (v - 37) / 7
            return 0.35 - 0.18 * math.sqrt(max(0.0, 1 - x * x))
        if v < 62:                                 # bead row on a low fillet
            p = self.BEAD_PERIOD
            du = (u % p) - p / 2
            d2 = du * du + (v - self.BEAD_V) ** 2
            r2 = self.BEAD_R ** 2
            bead = 0.45 * math.sqrt(1 - d2 / r2) if d2 < r2 else 0.0
            return 0.3 + bead
        if v < 80:                                 # flat panel sloping inwards
            return 0.42 - 0.12 * (v - 62) / 18
        if v < 90:                                 # sight-edge bead
            x = (v - 85) / 5
            return 0.3 + 0.22 * math.sqrt(max(0.0, 1 - x * x))
        return 0.08

    def albedo(self, u, v):
        gold = (214, 168, 72)
        bole = (150, 70, 40)       # red clay under the leaf, showing where the gilding wears
        wear = _fbm(self.wear, u, v)
        h = self.height(u, v)
        # Rubbed-through high spots show a hint of bole; recesses keep their darker patina
        c = _mix(gold, bole, 0.35 * _smooth(0.66, 0.85, wear) * _smooth(0.6, 0.95, h))
        k = (0.55 + 0.5 * min(1.0, h / 0.6)) * (0.92 + 0.12 * wear) * (0.96 + 0.06 * self.speck(u, v))
        if v >= 90:                                # dark rabbet lip
            k *= 0.3
        return tuple(ch * k for ch in c)


STYLES = (Wood, Gold)


def render(style_cls) -> Image.Image:
    style = style_cls()
    # Height and albedo tables over one period of u (everything repeats every T px)
    H = [[style.height(u, v) for v in range(S)] for u in range(T)]
    A = [[style.albedo(u, v) for v in range(S)] for u in range(T)]
    lx, ly, lz = _norm(*LIGHT)
    hx, hy, hz = _norm(lx, ly, lz + 1.0)
    rabbet = tuple(round(c * 0.3) for c in A[0][S - 1])

    img = Image.new("RGB", (SIZE, SIZE))
    px = img.load()
    last = SIZE - 1
    for y in range(SIZE):
        for x in range(SIZE):
            d = (y, last - y, x, last - x)
            m = min(d)
            if m >= S:                             # centre: not drawn by border-image
                px[x, y] = rabbet
                continue
            side = d.index(m)
            on_mitre = d.count(m) > 1
            name = SIDES[side]
            if side == 0:
                u, v = x, y
            elif side == 1:
                u, v = x, last - y
            elif side == 2:
                u, v = y, x
            else:
                u, v = y, last - x
            u = (u + style.side_shift[name]) % T
            col = H[u]
            dhdu = (H[(u + 1) % T][v] - H[u - 1][v]) * 0.5 * style.depth
            dhdv = (col[min(v + 1, S - 1)] - col[max(v - 1, 0)]) / (2 if 0 < v < S - 1 else 1) * style.depth
            nu, nv = -dhdu, -dhdv
            # Local (u, v) normal to image (x, y), the inverse of the mapping above
            if side == 0:
                nx, ny = nu, nv
            elif side == 1:
                nx, ny = nu, -nv
            elif side == 2:
                nx, ny = nv, nu
            else:
                nx, ny = -nv, nu
            nx, ny, nz = _norm(nx, ny, 1.0)
            diff = max(0.0, nx * lx + ny * ly + nz * lz)
            spec = style.spec * max(0.0, nx * hx + ny * hy + nz * hz) ** style.shininess
            shade = style.ambient + style.diffuse * diff
            if on_mitre:
                shade *= style.seam
                spec *= style.seam
            a = A[u][v]
            px[x, y] = tuple(
                max(0, min(255, round(a[i] * shade + style.spec_color[i] * spec)))
                for i in range(3)
            )
    return img


def manifest() -> str:
    lines = [
        "// Generated by tools/frames/generate_frames.py — do not edit; re-run the script instead.",
        "// Decorative frame styles for border-image: `slice` px corners around a `tile` px edge tile.",
        "window.PICME_FRAMES = {",
    ]
    for st in STYLES:
        lines.append(
            f"  {st.name}: {{ label: '{st.label}', src: '{URL_PREFIX}{st.name}.png', "
            f"slice: {S}, tile: {T}, default_width: {st.default_width} }},"
        )
    lines.append("};")
    return "\n".join(lines) + "\n"


def write(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for st in STYLES:
        render(st).save(out / f"{st.name}.png", optimize=True)
    (out / "frames.js").write_text(manifest(), encoding="utf-8", newline="\n")


def check(out: Path) -> list[str]:
    """Names of committed files that differ from a fresh render (pixels, not PNG bytes)."""
    stale = []
    for st in STYLES:
        path = out / f"{st.name}.png"
        try:
            with Image.open(path) as committed:
                same = committed.mode == "RGB" and committed.tobytes() == render(st).tobytes()
        except OSError:
            same = False
        if not same:
            stale.append(path.name)
    js = out / "frames.js"
    if not js.exists() or js.read_text(encoding="utf-8") != manifest():
        stale.append(js.name)
    return stale


# ── Preview contact sheet: a 9-slice with border-image-repeat: round, like the browser ──

def nine_slice(asset: Image.Image, w: int, h: int, fw: int) -> Image.Image:
    out = Image.new("RGB", (w, h))
    scale = fw / S
    corners = {(0, 0): (0, 0), (S + T, 0): (w - fw, 0), (0, S + T): (0, h - fw), (S + T, S + T): (w - fw, h - fw)}
    for (sx, sy), pos in corners.items():
        out.paste(asset.crop((sx, sy, sx + S, sy + S)).resize((fw, fw), Image.LANCZOS), pos)

    def run(tile, length, horizontal, place):
        n = max(1, round(length / (T * scale)))
        for i in range(n):
            a, b = round(i * length / n), round((i + 1) * length / n)
            size = (b - a, fw) if horizontal else (fw, b - a)
            place(tile.resize(size, Image.LANCZOS), a)

    run(asset.crop((S, 0, S + T, S)), w - 2 * fw, True, lambda t, a: out.paste(t, (fw + a, 0)))
    run(asset.crop((S, S + T, S + T, SIZE)), w - 2 * fw, True, lambda t, a: out.paste(t, (fw + a, h - fw)))
    run(asset.crop((0, S, S, S + T)), h - 2 * fw, False, lambda t, a: out.paste(t, (0, fw + a)))
    run(asset.crop((S + T, S, SIZE, S + T)), h - 2 * fw, False, lambda t, a: out.paste(t, (w - fw, fw + a)))
    return out


def _photo(w: int, h: int) -> Image.Image:
    """A stand-in landscape photo: sky gradient, hills, a sun."""
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(round(c) for c in _mix((90, 150, 215), (235, 200, 160), t)))
    d.ellipse([w * 0.7, h * 0.15, w * 0.7 + h * 0.18, h * 0.33], fill=(255, 236, 180))
    d.polygon([(0, h), (0, h * 0.62), (w * 0.3, h * 0.48), (w * 0.62, h * 0.66), (w, h * 0.55), (w, h)], fill=(70, 110, 70))
    d.polygon([(0, h), (0, h * 0.8), (w * 0.5, h * 0.72), (w, h * 0.86), (w, h)], fill=(45, 80, 50))
    return img


def framed_slot(asset, w, h, fw, mat, mat_color=(0, 0, 0)):
    slot = nine_slice(asset, w, h, fw)
    inner = Image.new("RGB", (w - 2 * fw, h - 2 * fw), mat_color)
    inner.paste(_photo(w - 2 * (fw + mat), h - 2 * (fw + mat)), (mat, mat))
    slot.paste(inner, (fw, fw))
    return slot


def preview(path: Path) -> None:
    """Contact sheet: each style on a classic-4 slot at 1920x1080 size (942x522, the default
    width), on a small 240x160 slot (width clamped like the display), and a 4x corner zoom."""
    assets = [(st, render(st)) for st in STYLES]
    sheet = Image.new("RGB", (1920, 1300), (26, 26, 26))
    d = ImageDraw.Draw(sheet)
    for row, (st, asset) in enumerate(assets):
        y = 20 + row * 640
        d.text((20, y - 16), f"{st.label}: classic-4 slot 942x522, width {st.default_width}", fill=(200, 200, 200))
        sheet.paste(framed_slot(asset, 942, 522, st.default_width, 16), (20, y))
        small_w = min(st.default_width, 160 // 6)
        d.text((990, y - 16), f"240x160 slot, width {small_w}", fill=(200, 200, 200))
        sheet.paste(framed_slot(asset, 240, 160, small_w, 6), (990, y))
        d.text((1260, y - 16), "corner, 4x zoom of the width-36 render", fill=(200, 200, 200))
        corner = framed_slot(asset, 160, 160, 36, 16).crop((0, 0, 120, 120)).resize((480, 480), Image.NEAREST)
        sheet.paste(corner, (1260, y))
        d.text((990, y + 190), "source asset (384x384)", fill=(200, 200, 200))
        sheet.paste(asset.resize((240, 240), Image.LANCZOS), (990, y + 210))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory (default: %(default)s)")
    ap.add_argument("--check", action="store_true", help="verify the files in --out match a fresh render")
    ap.add_argument("--preview", type=Path, metavar="PATH", help="also write a contact sheet PNG here")
    args = ap.parse_args(argv)
    if args.check:
        stale = check(args.out)
        if stale:
            print(f"Out of date in {args.out}: {', '.join(stale)} — re-run {Path(__file__).name}", file=sys.stderr)
            return 1
        print(f"Frame assets in {args.out} are up to date.")
    else:
        write(args.out)
        print(f"Wrote {', '.join(st.name + '.png' for st in STYLES)} and frames.js to {args.out}")
    if args.preview:
        preview(args.preview)
        print(f"Preview: {args.preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
