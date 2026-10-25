#!/usr/bin/env python3
"""Draw PicMe's favicon: a small framed picture (a wood frame, a cream mat, hills under a sun).

An original drawing made by this script (Pillow only), MIT-licensed like the rest of the repo.
Writes ``frontend/assets/favicon.ico`` (16, 32 and 48 px) and ``favicon.png`` (180 px, for
home-screen icons). Run from the repo root with the project venv:

    .venv/bin/python tools/favicon/make_favicon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[2] / "frontend" / "assets"
BIG = 512  # drawn large, then scaled down so small sizes stay smooth


def draw() -> Image.Image:
    img = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((16, 16, BIG - 16, BIG - 16), radius=48, fill=(122, 78, 44))        # frame
    d.rounded_rectangle((40, 40, BIG - 40, BIG - 40), radius=32, fill=(92, 56, 30))         # frame shade
    d.rectangle((72, 72, BIG - 72, BIG - 72), fill=(241, 233, 216))                           # mat
    sky = (112, 118, BIG - 112, BIG - 118)
    d.rectangle(sky, fill=(110, 170, 225))                                                     # photo
    d.ellipse((300, 150, 370, 220), fill=(255, 205, 80))                                       # sun
    d.polygon([(112, 394), (210, 250), (300, 394)], fill=(70, 140, 85))                        # hills
    d.polygon([(220, 394), (320, 280), (400, 394)], fill=(50, 115, 70))
    return img


def main() -> None:
    img = draw()
    img.save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    img.resize((180, 180), Image.LANCZOS).save(OUT / "favicon.png")
    print(f"Wrote favicon.ico and favicon.png to {OUT}")


if __name__ == "__main__":
    main()
