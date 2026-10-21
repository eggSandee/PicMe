// Time-of-day periods — which template (day or night) is in effect at a given moment.
// Shared by the display and the editor (loaded as /display/time-of-day.js), so the editor's
// override notes always name the period the display is showing. Pure: the clock is passed in.
//
// config.time_of_day = { enabled, day_start: "HH:MM", night_start: "HH:MM", day: {...}, night: {...} }
// A template overrides only the keys it defines; an absent key ("Same as default") leaves the
// global, frame or gallery value showing.
(function () {
  function minutes(s, fallback) {
    const [h, m] = (typeof s === 'string' && s ? s : fallback).split(':').map(Number);
    return h * 60 + m;
  }

  // { name: 'day' | 'night', template, until: "HH:MM" } for `now`, or null when time of day is off
  function currentPeriod(config, now) {
    const tod = config && config.time_of_day;
    if (!tod || !tod.enabled) return null;
    const cur = now.getHours() * 60 + now.getMinutes();
    const dayMins = minutes(tod.day_start, '07:00'), nightMins = minutes(tod.night_start, '20:00');
    const isDay = dayMins < nightMins
      ? (cur >= dayMins && cur < nightMins)
      : (cur >= dayMins || cur < nightMins);
    return isDay
      ? { name: 'day', template: tod.day || null, until: tod.night_start || '20:00' }
      : { name: 'night', template: tod.night || null, until: tod.day_start || '07:00' };
  }

  window.PicMeTimeOfDay = { currentPeriod };
})();
