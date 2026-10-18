// Aspect ratios of photo openings (README → Editor → Slot sizes). Loaded as /display/aspect.js
// by the editor, whose preview labels each slot with its ratio. Pure arithmetic: no DOM.
//
// Sizes are display px at the 1920x1080 reference. Separate frames follow the display's
// #collage grid (12 px padding and gap) and applyFrameStyle (frame border, then the mat, then a
// caption band under the photo). Gallery openings come from gallery.js's own layout.
(function () {
  const REFERENCE = { width: 1920, height: 1080, padding: 12, gap: 12 };  // #collage in display/index.html

  // Common photo shapes, as width:height: phones (4:3, 16:9), prints (3:2 = 4x6, 5:4 = 8x10,
  // 7:5 = 5x7) and square, each both ways round
  const COMMON = [[1, 1], [4, 3], [3, 4], [16, 9], [9, 16], [3, 2], [2, 3], [5, 4], [4, 5], [7, 5], [5, 7]];
  const EXACT = 0.015;     // within 1.5%: shown as the ratio itself ("3:2")
  const TOLERANCE = 0.06;  // within 6%: "≈3:2" (a photo that shape loses at most ~6% to bars or cropping)

  // "1fr 2fr" -> [1, 2] (layouts are fr tracks only: backend/layouts.py checks)
  function tracks(template) {
    return String(template || '').trim().split(/\s+/).map(t => parseFloat(t)).filter(n => n > 0);
  }

  // Track start offsets and sizes for `total` px with `gap` px between tracks
  function trackRects(frs, total, gap) {
    const sum = frs.reduce((a, b) => a + b, 0) || 1;
    const free = total - gap * Math.max(0, frs.length - 1);
    const sizes = frs.map(f => (free * f) / sum);
    const starts = [];
    let at = 0;
    for (const s of sizes) { starts.push(at); at += s + gap; }
    return { starts, sizes };
  }

  // Each slot's cell {x, y, w, h} from its grid_area ("row-start / col-start / row-end / col-end"),
  // gaps it spans included. Defaults to the display's collage at the reference size.
  function cells(layout, { width = REFERENCE.width, height = REFERENCE.height,
                           padding = REFERENCE.padding, gap = REFERENCE.gap } = {}) {
    const cols = trackRects(tracks(layout.grid_template_columns), width - 2 * padding, gap);
    const rows = trackRects(tracks(layout.grid_template_rows), height - 2 * padding, gap);
    const span = (t, a, b) => (t.starts[b - 1] ?? 0) + (t.sizes[b - 1] ?? 0) - (t.starts[a] ?? 0);
    return (layout.slots || []).map(def => {
      const [r0, c0, r1, c1] = String(def.grid_area).split('/').map(n => parseInt(n, 10) - 1);
      return { x: padding + (cols.starts[c0] ?? 0), y: padding + (rows.starts[r0] ?? 0),
               w: span(cols, c0, c1), h: span(rows, r0, r1) };
    });
  }

  // The photo inside a separate frame: the border (a decorative moulding is capped at a sixth of
  // the cell's short side, at least 4 px), the mat on every side, and the caption band under it.
  // frame: { show_frame, frame_width, mat_width }; decorative: the style is a frames.js moulding.
  function framedOpening(cell, frame, { decorative = false, band = 0 } = {}) {
    const on = frame.show_frame ?? true;
    const width = Number(frame.frame_width ?? 8) || 0;
    const border = !on ? 0 : decorative
      ? Math.min(width, Math.max(4, Math.floor(Math.min(cell.w, cell.h) / 6))) : width;
    const edge = border + (Number(frame.mat_width ?? 16) || 0);
    return { w: Math.max(0, cell.w - 2 * edge), h: Math.max(0, cell.h - 2 * edge - (Number(band) || 0)) };
  }

  // log-ratio distance: 0.06 ≈ 6% wider or taller
  const distance = (a, b) => Math.abs(Math.log(a / b));

  // The common ratio closest to w:h: { ratio: '3:2', err, common: err <= TOLERANCE }
  function nearest(w, h) {
    if (!(w > 0 && h > 0)) return null;
    let best = null;
    for (const [a, b] of COMMON) {
      const err = distance(w / h, a / b);
      if (!best || err < best.err) best = { ratio: `${a}:${b}`, err };
    }
    best.common = best.err <= TOLERANCE;
    return best;
  }

  const trim = n => n.toFixed(1).replace(/\.0$/, '');

  // '3:2' when it's that, '≈3:2' when close, else '≈2.4:1' / '≈1:2.1' (always width:height,
  // so portrait reads 2:3 — like phone and print sizes, "4x6" being 2:3)
  function label(w, h) {
    const n = nearest(w, h);
    if (!n) return '';
    if (n.err <= EXACT) return n.ratio;
    if (n.common) return `≈${n.ratio}`;
    return w >= h ? `≈${trim(w / h)}:1` : `≈1:${trim(h / w)}`;
  }

  window.PicMeAspect = { REFERENCE, COMMON, EXACT, TOLERANCE, tracks, cells, framedOpening, nearest, label };
})();
