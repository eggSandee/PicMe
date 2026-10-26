// Metadata pill sizing — how big the pills and their text are for a given box.
// Shared by the display and the editor (loaded as /display/pill-size.js), so the editor's
// preview matches the TV. Pure functions: no DOM access, so any box can be sized — a slot,
// an editor preview cell, or an opening inside a gallery mat.
//
// metadata_defaults / slot.metadata = { text_size: small|medium|large|x-large, pill_size: small|medium|large }
// tokens() returns CSS custom properties; the display's pill CSS reads only these:
//   --pill-font-size, --pill-pad-y, --pill-pad-x, --pill-gap, --pill-radius
// (--pill-inset, the overlay's distance from the box edge, is set by whoever positions the overlay).
//
// One size per screen: callers measure the pill area of every slot on screen, take the shortest
// side with sharedShort(), and size every slot's pills from that (a slot's own text_size still
// scales it). config.label_position puts the pills on the photo or in a caption on the mat.
(function () {
  // ── Tuning: every number that decides pill size is here ──
  // Base font = BASE_RATIO of the box's short side, between BASE_MIN and BASE_MAX; the text
  // size preset scales it, and the result stays within FONT_MIN..FONT_MAX.
  // At 1920x1080 a classic-4 slot gives ~20 px for medium: about 13 mm on a 55" TV.
  const BASE_RATIO = 0.04;
  const BASE_MIN = 14, BASE_MAX = 32;
  const FONT_MIN = 12, FONT_MAX = 48;
  const DEFAULT_SIZE = 'medium';

  const TEXT_SIZES = { small: 0.8, medium: 1, large: 1.25, 'x-large': 1.5 };
  // In em, so a pill always fits its text. Medium is the original 3px/9px padding at 12px.
  const PILL_SIZES = {
    small:  { py: 0.15, px: 0.5,  gap: 0.2 },
    medium: { py: 0.25, px: 0.75, gap: 0.3 },
    large:  { py: 0.4,  px: 0.95, gap: 0.45 },
  };

  // Long pills scroll instead of being cut off: MARQUEE_SPEED px/s each way, with a
  // MARQUEE_PAUSE s hold at the start and at the end.
  const MARQUEE_SPEED = 25;
  const MARQUEE_PAUSE = 2;
  // When the photo changes sooner than a slow pass would finish, scroll faster (up to this),
  // so the whole text is shown at least once while the photo is on screen.
  const MARQUEE_MAX_SPEED = 60;

  // Captions on the mat: one row of pills in a band under each opening, CAPTION_GAP_EM of the
  // font clear of the photo above and below; the band is at most CAPTION_MAX_FRACTION of the
  // smallest slot's height.
  const CAPTION_GAP_EM = 0.35;
  const CAPTION_MAX_FRACTION = 0.25;
  // The last non-date pill left in a caption shrinks and scrolls down to this many em (or its
  // own width) before it is hidden too
  const CAPTION_PILL_MIN_EM = 6;
  const LABEL_POSITIONS = ['auto', 'photo', 'mat'];

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  // The limits above are px at the 1920x1080 reference: on another screen they follow
  // PicMeScale (scale.js), the floors never below its legible size. 1 without it (and in the editor).
  const S = () => window.PicMeScale;
  const scaledMin = v => (S() ? S().floor(v) : v);
  const scaledMax = (v, min) => Math.max(min, S() ? S().px(v) : v);
  // FONT_MIN on this screen: the smallest pill text, e.g. a date that alone doesn't fit
  function fontMin() { return scaledMin(FONT_MIN); }
  const has = (obj, key) => typeof key === 'string' && Object.prototype.hasOwnProperty.call(obj, key);

  // { text_size, pill_size } from a metadata object; missing or unknown values are medium
  function resolve(meta) {
    const m = meta && typeof meta === 'object' ? meta : {};
    return {
      text_size: has(TEXT_SIZES, m.text_size) ? m.text_size : DEFAULT_SIZE,
      pill_size: has(PILL_SIZES, m.pill_size) ? m.pill_size : DEFAULT_SIZE,
    };
  }

  // Font size in px for a box of boxW x boxH (the space the pills sit in)
  function fontSize(sizes, boxW, boxH) {
    const { text_size } = resolve(sizes);
    const short = Math.max(0, Math.min(Number(boxW) || 0, Number(boxH) || 0));
    const baseMin = scaledMin(BASE_MIN), fontLo = fontMin();
    const base = clamp(BASE_RATIO * short, baseMin, scaledMax(BASE_MAX, baseMin));
    return Math.round(clamp(base * TEXT_SIZES[text_size], fontLo, scaledMax(FONT_MAX, fontLo)));
  }

  function tokens(sizes, boxW, boxH) {
    const p = PILL_SIZES[resolve(sizes).pill_size];
    return {
      '--pill-font-size': `${fontSize(sizes, boxW, boxH)}px`,
      '--pill-pad-y': `${p.py}em`,
      '--pill-pad-x': `${p.px}em`,
      '--pill-gap': `${p.gap}em`,
      '--pill-radius': '999px',
    };
  }

  // The shortest side of the given {w, h} boxes, ignoring empty ones (hidden slots); 0 if none
  function sharedShort(boxes) {
    let short = Infinity;
    for (const b of boxes || []) {
      const w = Number(b && b.w) || 0, h = Number(b && b.h) || 0;
      if (w > 0 && h > 0) short = Math.min(short, w, h);
    }
    return Number.isFinite(short) ? short : 0;
  }

  // px height of a caption band holding one row of pills sized for `short`
  function captionBand(sizes, short) {
    const font = fontSize(sizes, short, short);
    const p = PILL_SIZES[resolve(sizes).pill_size];
    return Math.round(font * (1.25 + 2 * p.py)) + 2 * Math.round(CAPTION_GAP_EM * font);
  }

  // Where the pills go on a screen in `mode` ('gallery' | 'slots'): 'mat' or 'photo'.
  // label_position auto (the default, and any unknown value) is the mat on a gallery mat only.
  function labelPlacement(labelPosition, mode) {
    const pos = LABEL_POSITIONS.includes(labelPosition) ? labelPosition : 'auto';
    if (pos === 'auto') return mode === 'gallery' ? 'mat' : 'photo';
    return pos;
  }

  // px/s for a `d` px scroll on a photo shown for `windowSec` s (unknown, e.g. video: base speed)
  function marqueeSpeed(d, windowSec) {
    if (!(windowSec > 0)) return MARQUEE_SPEED;
    const avail = windowSec - 2 * MARQUEE_PAUSE;  // a hold, one pass, and slack for the fade
    return clamp(avail > 0 ? d / avail : MARQUEE_MAX_SPEED, MARQUEE_SPEED, MARQUEE_MAX_SPEED);
  }

  // Ping-pong scroll over `distance` px: hold, scroll left, hold, scroll back.
  // Returns { name, duration (s), speed, css } — css is an @keyframes rule to add once per name.
  function marquee(distance, windowSec) {
    const d = Math.max(1, Math.ceil(distance));
    const speed = marqueeSpeed(d, windowSec);
    const travel = d / speed;
    const duration = 2 * (MARQUEE_PAUSE + travel);
    const pct = t => `${(100 * t / duration).toFixed(3)}%`;
    const name = `pill-marquee-${d}-${Math.round(speed)}`;
    const css = `@keyframes ${name} {`
      + ` 0%, ${pct(MARQUEE_PAUSE)} { transform: translateX(0); }`
      + ` ${pct(MARQUEE_PAUSE + travel)}, ${pct(2 * MARQUEE_PAUSE + travel)} { transform: translateX(-${d}px); }`
      + ` 100% { transform: translateX(0); } }`;
    return { name, duration, speed, css };
  }

  window.PicMePills = {
    TEXT_SIZES, PILL_SIZES, FONT_MIN, FONT_MAX, MARQUEE_SPEED, MARQUEE_PAUSE, MARQUEE_MAX_SPEED,
    CAPTION_GAP_EM, CAPTION_MAX_FRACTION, CAPTION_PILL_MIN_EM, LABEL_POSITIONS,
    resolve, fontSize, fontMin, tokens, marquee, sharedShort, captionBand, labelPlacement,
  };
})();
