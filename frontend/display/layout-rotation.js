// Daily layout rotation — which layout is on screen at a given moment.
// Shared by the display and the editor (loaded as /display/layout-rotation.js), so both
// always agree. Pure functions: no DOM, no fetch, and the clock is always passed in.
//
// config.layout_rotation = { enabled, layouts: [ids] (empty = all), change_at: "HH:MM" }
// The legacy flag daily_layout_rotation: true means all layouts, switching at midnight.
(function () {
  const DEFAULT_CHANGE_AT = 4 * 60;  // 04:00
  const DAY_MS = 86400000;

  // "HH:MM" -> minutes after midnight. Out-of-range parts are clamped; anything else is 04:00.
  function parseChangeAt(s) {
    const m = /^\s*(\d{1,2}):(\d{1,2})\s*$/.exec(typeof s === 'string' ? s : '');
    if (!m) return DEFAULT_CHANGE_AT;
    return Math.min(23, Number(m[1])) * 60 + Math.min(59, Number(m[2]));
  }

  function isObject(v) {
    return !!v && typeof v === 'object' && !Array.isArray(v);
  }

  // Normalised settings: { enabled, layouts, changeAt (minutes) }
  function rotationSettings(config) {
    const r = isObject(config) ? config.layout_rotation : null;
    if (isObject(r)) {
      return {
        enabled: r.enabled === true,
        layouts: Array.isArray(r.layouts) ? r.layouts : [],
        changeAt: parseChangeAt(r.change_at),
      };
    }
    if (isObject(config) && config.daily_layout_rotation === true) {
      return { enabled: true, layouts: [], changeAt: 0 };
    }
    return { enabled: false, layouts: [], changeAt: DEFAULT_CHANGE_AT };
  }

  // Eligible layouts in canonical /api/layouts order. Empty list = all; unknown ids are dropped.
  function eligibleLayouts(config, layouts) {
    if (!Array.isArray(layouts)) return [];
    const ids = rotationSettings(config).layouts;
    if (ids.length === 0) return layouts.slice();
    const wanted = new Set(ids);
    return layouts.filter(l => wanted.has(l.id));
  }

  // Days since the epoch of the rotation day `now` falls in. Before change_at it is still
  // the previous day. Uses the local calendar date and wall-clock minutes, so a 23 h or 25 h
  // DST day can't shift it, and a change_at inside a DST gap takes effect when the gap ends.
  function rotationDay(now, changeAtMins) {
    const day = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) / DAY_MS;
    return now.getHours() * 60 + now.getMinutes() < changeAtMins ? day - 1 : day;
  }

  // A config without a known layout gets the first-run default, not whichever layout sorts
  // first in the picker (Single)
  const DEFAULT_LAYOUT = 'classic-4';
  function fallbackLayout(layouts) {
    return layouts.find(l => l.id === DEFAULT_LAYOUT) || layouts[0];
  }

  function baseLayout(config, layouts) {
    const id = isObject(config) ? config.layout : undefined;
    return layouts.find(l => l.id === id) || fallbackLayout(layouts);
  }

  // The layout object to show at `now`, or null if there are no layouts at all
  function resolveLayout(config, layouts, now = new Date()) {
    if (!Array.isArray(layouts) || layouts.length === 0) return null;
    const settings = rotationSettings(config);
    if (!settings.enabled) return baseLayout(config, layouts);
    const eligible = eligibleLayouts(config, layouts);
    if (eligible.length === 1) return eligible[0];
    if (eligible.length === 0) return baseLayout(config, layouts);
    const n = eligible.length;
    return eligible[((rotationDay(now, settings.changeAt) % n) + n) % n];
  }

  // { at: Date, layout } for the next switch after `now`, or null if rotation isn't in effect
  function nextChange(config, layouts, now = new Date()) {
    const settings = rotationSettings(config);
    if (!settings.enabled || eligibleLayouts(config, layouts).length < 2) return null;
    const h = Math.floor(settings.changeAt / 60), m = settings.changeAt % 60;
    let at = new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, m);
    if (rotationDay(at, settings.changeAt) === rotationDay(now, settings.changeAt)) {
      at = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, h, m);
    }
    return { at, layout: resolveLayout(config, layouts, at) };
  }

  window.PicMeRotation = {
    DEFAULT_CHANGE_AT, DEFAULT_LAYOUT, parseChangeAt, rotationSettings, eligibleLayouts, rotationDay,
    fallbackLayout, resolveLayout, nextChange,
  };
})();
