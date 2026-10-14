// Metadata pill sizing — how big the pills and their text are for a given box.
// Shared by the display and the editor (loaded as /display/pill-size.js), so the editor's
// preview matches the TV. Pure functions: no DOM access, so any box can be sized — a slot,
// an editor preview cell, or an opening inside a gallery mat.
//
// metadata_defaults / slot.metadata = { text_size: small|medium|large|x-large, pill_size: small|medium|large }
// tokens() returns CSS custom properties; the display's pill CSS reads only these:
//   --pill-font-size, --pill-pad-y, --pill-pad-x, --pill-gap, --pill-radius
// (--pill-inset, the overlay's distance from the box edge, is set by whoever positions the overlay).
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

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
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
    const base = clamp(BASE_RATIO * short, BASE_MIN, BASE_MAX);
    return Math.round(clamp(base * TEXT_SIZES[text_size], FONT_MIN, FONT_MAX));
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
    resolve, fontSize, tokens, marquee,
  };
})();
