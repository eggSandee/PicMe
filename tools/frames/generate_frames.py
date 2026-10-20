#!/usr/bin/env python3
"""Generate PicMe's decorative picture-frame assets (README → Frame assets).

Writes one PNG per style (``wood.png``, ``gold.png``, ``black.png``, ``silver.png``,
``white.png``, ``barnwood.png``) and the ``frames.js`` manifest that the display and the editor
load. The PNGs are original procedural renders made by this script: no third-party
images, MIT-licensed like the rest of the repo. Output is deterministic (seeded noise, pure
float maths), so the committed files can always be regenerated and checked.

Each asset is a square 9-slice of ``2*S + tile`` px for CSS ``border-image``: ``S`` px corners
(the slice) around a ``tile`` px edge tile (``T`` unless a style sets a longer one). Texture is a
function of ``(u, v)`` (``u`` along the board, ``v`` from the outer edge in towards the picture)
that repeats every ``tile`` px along ``u``, so ``border-image-repeat: round`` tiles the edges seamlessly and continuously with the
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
T = 192            # edge tile length (a style may set a longer `tile`, a multiple of T)
SIZE = 2 * S + T   # 384, the asset size at the default tile

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
    """Value noise on a lattice of ``cells_u`` x ``cells_v`` that wraps every ``period`` px along
    u (and every ``S`` px along v), so anything built from it tiles along the board."""

    def __init__(self, seed: int, cells_u: int, cells_v: int, period: int = T):
        rng = random.Random(seed)
        self.cu, self.cv = cells_u, cells_v
        self.su, self.sv = cells_u / period, cells_v / S
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


def _wave(u, k, phase=0.0, period=T):
    """A sine with an integer number ``k`` of periods per edge tile, so it tiles."""
    return math.sin(2 * math.pi * k * u / period + phase)


# ── Wood: a walnut cushion moulding with a satin finish ──

class Wood:
    name, label, seed, default_width = "wood", "Wood", 1729, 28
    tile = T
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
    tile = T
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


# ── Black gallery: a matte black flat-face moulding with a hairline highlight ──

class Black:
    name, label, seed, default_width = "black", "Black gallery", 5101, 24
    tile = T
    depth = 26.0
    ambient, diffuse = 0.42, 0.75
    spec, shininess, spec_color = 0.10, 6, (225, 228, 235)
    side_shift = {"top": 0, "bottom": 0, "left": 0, "right": 0}
    seam = 0.75
    base = (44, 44, 47)          # a soft black, a touch lighter than night mode's wall
    grain_amp = 0.12

    def __init__(self):
        self.grain = [(Noise(self.seed + 1, 12, 6), 1.0), (Noise(self.seed + 2, 48, 24), 0.5)]

    def height(self, u, v):
        if v < 6:                                  # rounded outer edge
            return 0.6 * math.sqrt(max(0.0, 1 - (1 - v / 6) ** 2))
        if v < 80:                                 # flat face with a whisper of crown
            return 0.6 + 0.015 * math.sin(math.pi * (v - 6) / 74)
        if v < 90:                                 # thin bevel down to the sight edge
            return 0.6 - 0.45 * (v - 80) / 10
        return 0.1

    def albedo(self, u, v):
        k = 1 - self.grain_amp / 2 + self.grain_amp * _fbm(self.grain, u, v)
        if v < 9:                                  # worn, lighter outer arris: the frame's outline
            k *= 1 + 1.3 * (1 - _smooth(5, 9, v))  # still reads on a black wall or at night
        if 80 <= v < 90:                           # the sight-edge bevel catches a little light
            k *= 1.45
        if v >= 90:
            k *= 0.4
        return tuple(c * k for c in self.base)


# ── Brushed silver: a thin aluminium profile with a stepped face ──

class Silver:
    name, label, seed, default_width = "silver", "Brushed silver", 6311, 16
    tile = T
    depth = 30.0
    ambient, diffuse = 0.45, 0.5
    spec, shininess, spec_color = 0.5, 22, (255, 255, 255)
    side_shift = {"top": 0, "bottom": 57, "left": 101, "right": 23}
    seam = 0.72

    def __init__(self):
        s = self.seed
        # Brushing: long streaks along the rail, fine across it
        self.brush = [(Noise(s + 1, 2, 96), 1.0), (Noise(s + 2, 4, 48), 0.6), (Noise(s + 3, 8, 24), 0.3)]

    def height(self, u, v):
        if v < 4:                                  # rounded outer edge
            return 0.7 * math.sqrt(max(0.0, 1 - (1 - v / 4) ** 2))
        if v < 54:                                 # face, angled a touch outwards
            return 0.7 - 0.12 * (v - 4) / 50
        if v < 64:                                 # the step down: a shadowed riser
            return 0.58 - 0.24 * _smooth(54, 64, v)
        if v < 88:                                 # inner lip
            return 0.34
        if v < 90:
            return 0.34 - 0.24 * (v - 88) / 2
        return 0.1

    def albedo(self, u, v):
        base = (156, 161, 168)                     # a mid grey, so the edge holds on a light wall
        k = 0.88 + 0.2 * _fbm(self.brush, u, v)
        # A gentle sheen baked across each face (bright outside, darker in), so the metal still
        # reads as metal at 16 px where the brushing is lost
        if v < 54:
            k *= 1.14 - 0.26 * _smooth(4, 54, v)
        elif v < 64:
            k *= 0.62                              # the step stays visible at small widths
        elif v < 90:
            k *= 1.04 - 0.14 * _smooth(64, 90, v)
        else:
            k *= 0.35
        return tuple(c * k for c in base)


# ── White modern: satin white, a tinted inner lip and a soft shadow at the sight edge ──

class White(Black):
    name, label, seed, default_width = "white", "White modern", 5203, 24
    ambient, diffuse = 0.62, 0.42
    spec, shininess, spec_color = 0.12, 10, (255, 255, 255)
    seam = 0.86
    base = (236, 234, 229)
    grain_amp = 0.03
    lip = (188, 184, 176)          # a warm grey that separates the frame from an ivory mat

    def albedo(self, u, v):
        k = 1 - self.grain_amp / 2 + self.grain_amp * _fbm(self.grain, u, v)
        c = self.base
        if v >= 78:                                # tinted inner lip
            c = _mix(c, self.lip, _smooth(78, 82, v))
        if v >= 84:                                # faint shadow falling in towards the picture
            k *= 1 - 0.6 * _smooth(84, 96, v)
        return tuple(ch * k for ch in c)


# ── Rustic barnwood: weathered reclaimed wood, cracks and a knot, on a long tile ──

class Barnwood:
    name, label, seed, default_width = "barnwood", "Rustic barnwood", 7417, 36
    # Three times the usual tile, so the cracks and the knot don't recur along a long edge
    tile = 3 * T
    depth = 24.0
    ambient, diffuse = 0.4, 0.75
    spec, shininess, spec_color = 0.04, 6, (230, 220, 200)
    # Each side is cut from a different stretch of the board
    side_shift = {"top": 0, "bottom": 211, "left": 389, "right": 97}
    seam = 0.6
    KNOT = (330.0, 46.0)          # one knot per tile

    def __init__(self):
        s, n = self.seed, self.tile
        self.streak = [(Noise(s + 1, 9, 40, n), 1.0), (Noise(s + 2, 18, 80, n), 0.5)]
        self.rough = [(Noise(s + 3, 96, 48, n), 1.0), (Noise(s + 4, 192, 96, n), 0.5)]
        self.crack_on = Noise(s + 5, 7, 1, n)
        self.crack_v = Noise(s + 6, 5, 1, n)
        self.weather = Noise(s + 7, 12, 8, n)

    def _knot(self, u, v):
        n = self.tile
        du = (u - self.KNOT[0] + n / 2) % n - n / 2
        return math.sqrt((du / 2.6) ** 2 + (v - self.KNOT[1]) ** 2)

    def _crack(self, u, v):
        vc = 30 + 34 * self.crack_v(u, 0)
        on = _smooth(0.68, 0.78, self.crack_on(u, 0))
        return on * max(0.0, 1 - abs(v - vc) / (0.6 + 1.4 * on))

    def height(self, u, v):
        if v < 7:
            base = 0.5 * math.sqrt(max(0.0, 1 - (1 - v / 7) ** 2)) * (0.9 + 0.2 * self.weather(u, v))
        elif v < 84:
            base = 0.5 + 0.02 * math.sin(math.pi * (v - 7) / 77)
        elif v < 90:
            base = 0.5 - 0.4 * (v - 84) / 6
        else:
            return 0.1
        base += 0.025 * (_fbm(self.rough, u, v) - 0.5)
        base -= 0.12 * self._crack(u, v)
        d = self._knot(u, v)
        if d < 9:
            base += 0.03 * (1 - d / 9)
        return base

    def albedo(self, u, v):
        n = self.tile
        light, dark = (172, 152, 124), (96, 80, 64)
        st = _fbm(self.streak, u, v)
        c = _mix(dark, light, _smooth(0.25, 0.8, st))
        # Weathered silver-grey patches over the warmer brown
        c = _mix(c, (140, 134, 126), 0.3 * self.weather(u, v))
        k = 0.95 + 0.08 * _fbm(self.rough, u, v)
        k *= 0.98 + 0.02 * (0.5 + 0.5 * _wave(u, 18, 0.3, n))          # faint saw marks
        gl = v / 5.5 + 1.6 * st + 0.08 * _wave(u, 3, 1.3, n)            # weathered grain lines
        k *= 1 - 0.2 * _smooth(0.72, 0.95, 0.5 + 0.5 * math.sin(2 * math.pi * gl))
        k *= 1 - 0.45 * self._crack(u, v)
        d = self._knot(u, v)
        if d < 12:
            ring = 0.5 + 0.5 * math.cos(d * 1.6)
            k *= 1 - (0.55 if d < 4 else 0.25 * ring) * (1 - d / 12)
        if v >= 90:
            k *= 0.35
        return tuple(ch * k for ch in c)


STYLES = (Wood, Gold, Black, Silver, White, Barnwood)


def render(style_cls) -> Image.Image:
    style = style_cls()
    n = style.tile
    # Height and albedo tables over one period of u (everything repeats every `tile` px)
    H = [[style.height(u, v) for v in range(S)] for u in range(n)]
    A = [[style.albedo(u, v) for v in range(S)] for u in range(n)]
    lx, ly, lz = _norm(*LIGHT)
    hx, hy, hz = _norm(lx, ly, lz + 1.0)
    rabbet = tuple(round(c * 0.3) for c in A[0][S - 1])

    size = 2 * S + n
    img = Image.new("RGB", (size, size))
    px = img.load()
    last = size - 1
    for y in range(size):
        for x in range(size):
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
            u = (u + style.side_shift[name]) % n
            col = H[u]
            dhdu = (H[(u + 1) % n][v] - H[u - 1][v]) * 0.5 * style.depth
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
            f"slice: {S}, tile: {st.tile}, default_width: {st.default_width} }},"
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
    n, end = asset.width - 2 * S, asset.width  # this asset's edge tile length, and its size
    corners = {(0, 0): (0, 0), (S + n, 0): (w - fw, 0), (0, S + n): (0, h - fw), (S + n, S + n): (w - fw, h - fw)}
    for (sx, sy), pos in corners.items():
        out.paste(asset.crop((sx, sy, sx + S, sy + S)).resize((fw, fw), Image.LANCZOS), pos)

    def run(tile, length, horizontal, place):
        count = max(1, round(length / (n * scale)))
        for i in range(count):
            a, b = round(i * length / count), round((i + 1) * length / count)
            size = (b - a, fw) if horizontal else (fw, b - a)
            place(tile.resize(size, Image.LANCZOS), a)

    run(asset.crop((S, 0, S + n, S)), w - 2 * fw, True, lambda t, a: out.paste(t, (fw + a, 0)))
    run(asset.crop((S, S + n, S + n, end)), w - 2 * fw, True, lambda t, a: out.paste(t, (fw + a, h - fw)))
    run(asset.crop((0, S, S, S + n)), h - 2 * fw, False, lambda t, a: out.paste(t, (0, fw + a)))
    run(asset.crop((S + n, S, end, S + n)), h - 2 * fw, False, lambda t, a: out.paste(t, (w - fw, fw + a)))
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
    sheet = Image.new("RGB", (1920, 20 + 640 * len(assets)), (26, 26, 26))
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
        d.text((990, y + 190), f"source asset ({asset.width}x{asset.height})", fill=(200, 200, 200))
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
