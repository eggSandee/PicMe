// Screen sizes (README → Screen sizes): one scale factor for everything the display draws in
// fixed px. Every configured width (frame, mat, gallery margins...) and every size tuned by eye
// (collage padding, pill inset, label floors) is px at the 1920x1080 reference; the display
// multiplies them by factor(), so a tablet or a 1366x768 TV shows a smaller copy of the 1080p
// design and a 3840x2160 (DPR 1) monitor a larger one. A 4K TV reports 1920x1080 CSS px, so 1.
//
// Loaded by the display and the editor (as /display/scale.js). Only the display calls update():
// the editor's factor stays 1, and its preview of another screen shape draws inside at().
(function () {
  const REFERENCE = { width: 1920, height: 1080 };
  const MIN = 0.3, MAX = 2;
  // No text drawn by the display is smaller than this, whatever the scale
  const LEGIBLE_PX = 10;

  let current = 1;

  // The factor for a viewport of w x h CSS px: the smaller ratio to the reference (so a portrait
  // screen goes by its width), between MIN and MAX. 1 for a size it can't read.
  function compute(w, h) {
    w = Number(w); h = Number(h);
    if (!(w > 0 && h > 0)) return 1;
    return Math.min(MAX, Math.max(MIN, Math.min(w / REFERENCE.width, h / REFERENCE.height)));
  }

  // Recompute from `win`'s viewport (the display, on load and resize). Returns the factor.
  function update(win = window) {
    current = compute(win.innerWidth, win.innerHeight);
    return current;
  }

  function factor() { return current; }

  // Run fn() with the factor of a w x h viewport, then put the current one back: the editor's
  // preview of another screen shape sizes pills and labels as the display would there.
  function at(w, h, fn) {
    const saved = current;
    current = compute(w, h);
    try { return fn(current); } finally { current = saved; }
  }

  // A reference px size at the current scale (unrounded)
  function px(v) { return (Number(v) || 0) * current; }

  // A floor tuned at the reference, at the current scale, never below LEGIBLE_PX
  function floor(v) { return Math.max(LEGIBLE_PX, px(v)); }

  window.PicMeScale = { REFERENCE, MIN, MAX, LEGIBLE_PX, compute, update, factor, at, px, floor };
})();
