// Opening shapes for gallery mat mode (README → Gallery mat).
// Shared by the display and the editor (loaded as /display/shapes.js). Pure functions: no DOM.
//
// Each shape gives, for an opening box of w x h px:
//   clip(w, h)          CSS clip-path for the photo/video (basic shapes only: inset, ellipse,
//                       polygon — they work on older TV browsers, unlike path() or url(#id))
//   svgPath(x, y, w, h) the same outline as an SVG path, for the bevel and its shadow
//   safe(w, h)          a px rect inside the shape where the metadata pills may sit
// Optional: aspect (the box keeps this width/height ratio), maxAspect (never wider than this).
// Curved outlines (arch, heart) are polygons, so the clip and the SVG are the same points.
// Shape names must match KNOWN_SHAPES in backend/layouts.py (tests check both agree).
(function () {
  const r2 = n => Math.round(n * 100) / 100;

  function polygonClip(points) {
    return `polygon(${points.map(([x, y]) => `${r2(x)}px ${r2(y)}px`).join(', ')})`;
  }

  function polygonPath(points, x, y) {
    return points.map(([px, py], i) => `${i ? 'L' : 'M'}${r2(x + px)} ${r2(y + py)}`).join(' ') + ' Z';
  }

  function rectPath(x, y, w, h) {
    return `M${r2(x)} ${r2(y)} H${r2(x + w)} V${r2(y + h)} H${r2(x)} Z`;
  }

  function fraction(fx, fy, fw, fh) {
    return (w, h) => ({ x: fx * w, y: fy * h, w: fw * w, h: fh * h });
  }

  // Corner radius of a rounded opening: a tenth of the short side, at most 48 px
  const roundRadius = (w, h) => Math.min(0.1 * Math.min(w, h), 48);

  // Semicircular top of radius w/2 over a rectangle
  const ARCH_STEPS = 48;
  function archPoints(w, h) {
    const r = Math.min(w / 2, h);
    const pts = [[0, h], [0, r]];
    for (let i = 1; i < ARCH_STEPS; i++) {
      const a = Math.PI - Math.PI * i / ARCH_STEPS;
      pts.push([w / 2 + (w / 2) * Math.cos(a), r - r * Math.sin(a)]);
    }
    pts.push([w, r], [w, h]);
    return pts;
  }

  // Flat-top hexagon
  const hexPoints = (w, h) => [[0.25 * w, 0], [0.75 * w, 0], [w, h / 2], [0.75 * w, h], [0.25 * w, h], [0, h / 2]];

  // The classic heart curve x = 16 sin³t, y = 13 cos t − 5 cos 2t − 2 cos 3t − cos 4t,
  // sampled once and normalised to a unit box
  const HEART_STEPS = 96;
  const HEART = (() => {
    const raw = [];
    for (let i = 0; i < HEART_STEPS; i++) {
      const t = 2 * Math.PI * i / HEART_STEPS;
      raw.push([16 * Math.sin(t) ** 3,
                -(13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t))]);
    }
    const xs = raw.map(p => p[0]), ys = raw.map(p => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    return { unit: raw.map(([x, y]) => [(x - x0) / (x1 - x0), (y - y0) / (y1 - y0)]), aspect: (x1 - x0) / (y1 - y0) };
  })();
  const heartPoints = (w, h) => HEART.unit.map(([x, y]) => [x * w, y * h]);

  const SHAPES = {
    rect: {
      label: 'Rectangle',
      clip: () => 'none',
      svgPath: rectPath,
      safe: fraction(0, 0, 1, 1),
    },
    rounded: {
      label: 'Rounded',
      clip: (w, h) => `inset(0 round ${r2(roundRadius(w, h))}px)`,
      svgPath: (x, y, w, h) => {
        const r = r2(roundRadius(w, h));
        return `M${r2(x + r)} ${r2(y)} H${r2(x + w - r)} A${r} ${r} 0 0 1 ${r2(x + w)} ${r2(y + r)}`
          + ` V${r2(y + h - r)} A${r} ${r} 0 0 1 ${r2(x + w - r)} ${r2(y + h)}`
          + ` H${r2(x + r)} A${r} ${r} 0 0 1 ${r2(x)} ${r2(y + h - r)}`
          + ` V${r2(y + r)} A${r} ${r} 0 0 1 ${r2(x + r)} ${r2(y)} Z`;
      },
      // In from each edge by the part of the corner the arc cuts off at 45°
      safe: (w, h) => {
        const c = 0.3 * roundRadius(w, h);
        return { x: c, y: c, w: w - 2 * c, h: h - 2 * c };
      },
    },
    ellipse: {
      label: 'Oval',
      clip: () => 'ellipse(50% 50% at 50% 50%)',
      svgPath: (x, y, w, h) => {
        const rx = r2(w / 2), ry = r2(h / 2);
        return `M${r2(x)} ${r2(y + h / 2)} A${rx} ${ry} 0 1 1 ${r2(x + w)} ${r2(y + h / 2)}`
          + ` A${rx} ${ry} 0 1 1 ${r2(x)} ${r2(y + h / 2)} Z`;
      },
      // The rect inscribed at 45°: 0.5 ± 0.5/√2
      safe: fraction(0.1464, 0.1464, 0.7072, 0.7072),
    },
    arch: {
      label: 'Arch',
      maxAspect: 1.25,  // wider than this, the arch would be mostly a flat lintel
      clip: (w, h) => polygonClip(archPoints(w, h)),
      svgPath: (x, y, w, h) => polygonPath(archPoints(w, h), x, y),
      // Below where the arch meets the corners of a rect 5% in from each side
      safe: (w, h) => {
        const r = Math.min(w / 2, h), top = r - r * Math.sqrt(1 - 0.81);
        return { x: 0.05 * w, y: top, w: 0.9 * w, h: h - top };
      },
    },
    hexagon: {
      label: 'Hexagon',
      aspect: 2 / Math.sqrt(3),
      clip: (w, h) => polygonClip(hexPoints(w, h)),
      svgPath: (x, y, w, h) => polygonPath(hexPoints(w, h), x, y),
      safe: fraction(0.15, 0.2, 0.7, 0.6),
    },
    heart: {
      label: 'Heart',
      aspect: HEART.aspect,
      clip: (w, h) => polygonClip(heartPoints(w, h)),
      svgPath: (x, y, w, h) => polygonPath(heartPoints(w, h), x, y),
      // Largest rect (at least half the width) inside the curve, found numerically, less a margin
      safe: fraction(0.18, 0.25, 0.64, 0.32),
    },
  };
  SHAPES.circle = { ...SHAPES.ellipse, label: 'Circle', aspect: 1 };

  const ORDER = ['rect', 'rounded', 'ellipse', 'circle', 'arch', 'hexagon', 'heart'];

  function has(name) {
    return typeof name === 'string' && Object.prototype.hasOwnProperty.call(SHAPES, name);
  }

  // Unknown names (a hand-edited config) are rectangles
  function get(name) {
    return SHAPES[has(name) ? name : 'rect'];
  }

  function names() {
    return ORDER.slice();
  }

  // The opening's box inside a cell of w x h: the whole cell, or the largest centred box with
  // the shape's aspect (circle, hexagon, heart), or no wider than its maxAspect (arch)
  function box(name, w, h) {
    const s = get(name);
    let bw = w, bh = h;
    if (s.aspect) {
      if (w / h > s.aspect) bw = h * s.aspect; else bh = w / s.aspect;
    } else if (s.maxAspect && w / h > s.maxAspect) {
      bw = h * s.maxAspect;
    }
    bw = Math.max(0, Math.floor(bw));
    bh = Math.max(0, Math.floor(bh));
    return { x: Math.floor((w - bw) / 2), y: Math.floor((h - bh) / 2), w: bw, h: bh };
  }

  // The outline of the same shape drawn `b` px inside a w x h box (the photo inside the bevel):
  // the shape of the inner box, so it matches the clip-path of media inset by `b` exactly
  function insetPath(name, x, y, w, h, b) {
    return get(name).svgPath(x + b, y + b, Math.max(0, w - 2 * b), Math.max(0, h - 2 * b));
  }

  window.PicMeShapes = { SHAPES, has, get, names, box, insetPath };
})();
