// Metadata label styles (README → Metadata label styles): how a slot's metadata looks.
// Shared by the display and the editor (loaded as /display/label-styles.js, with
// label-styles.css), so the editor's previews are the display's renderer. Needs pill-size.js.
//
// config.label_style = pill (the default: today's pills, pill-size.js and the display's own
// code) | caption | placard | whisper. The style decides where the labels go:
//   caption  a serif line in the caption band under the photo (always on the mat)
//   placard  a small printed card in the caption band, two lines (always on the mat)
//   whisper  bare type at the bottom of the photo over a soft scrim (always on the photo)
// label_position (auto | photo | mat) applies to pills only.
//
// Sizes follow the one-size-per-screen rule: a style's font is the screen's pill font
// (PicMePills.fontSize for the shortest area) times the style's scale, never below its floor.
(function () {
  const P = () => window.PicMePills;

  // where: 'mat' | 'photo', or null when label_position decides (pills).
  // scale: of the pill font; min: px floor at the 1920x1080 display, so the smallest text in the
  // style (placard details, whisper place) stays at least MIN_TEXT_PX; maxFraction: the caption
  // band's cap, of the smallest slot's height.
  const SPEC = {
    pill:    { where: null },
    caption: { where: 'mat',   scale: 1.1,  min: 18, maxFraction: 0.25 },
    placard: { where: 'mat',   scale: 1.0,  min: 18, maxFraction: 0.32 },
    whisper: { where: 'photo', scale: 1.05, min: 20 },
  };
  const STYLES = Object.keys(SPEC);
  const DEFAULT_STYLE = 'pill';
  const MIN_TEXT_PX = 16;   // no visible label text is smaller than this on a 1920x1080 display
  const LINE = { caption: 1.3 };
  // Placard card, in em of the label font: border (px), padding, title and details lines
  const PLACARD = { padTop: 0.32, padBottom: 0.38, title: 1.15 * 1.2, details: 1.0 * 1.25, gap: 0.08 };
  const BAND_GAP_EM = 0.35;  // clear of the photo above and the mat edge below, like pill captions

  // Inks for text straight on the mat (caption): the darker or lighter, whichever reads better.
  // Below LIFT_BELOW:1 contrast (mid-tone mats) the ink goes pure black or white and bolder.
  const INK_DARK = '#2a241c', INK_LIGHT = '#f6f0e4';
  const LIFT_BELOW = 7;

  const has = (obj, key) => typeof key === 'string' && Object.prototype.hasOwnProperty.call(obj, key);

  function resolveStyle(style) {
    return has(SPEC, style) ? style : DEFAULT_STYLE;
  }

  // 'mat' or 'photo' for this style on a screen in `mode` ('gallery' | 'slots')
  function placement(style, labelPosition, mode) {
    const where = SPEC[resolveStyle(style)].where;
    if (where) return where;
    return P() ? P().labelPlacement(labelPosition, mode) : 'photo';
  }

  // The style's font in px for the screen's pill font `pillPx`
  function fontPx(style, pillPx) {
    const s = SPEC[resolveStyle(style)];
    if (!s.scale) return pillPx;
    const max = P() ? P().FONT_MAX : 48;
    return Math.round(Math.min(max, Math.max(s.min, (Number(pillPx) || 0) * s.scale)));
  }

  // px height of the caption band for this style, from the slot's sizes and the screen's short side
  function captionBand(style, sizes, short) {
    style = resolveStyle(style);
    if (style === 'pill') return P().captionBand(sizes, short);
    const f = fontPx(style, P().fontSize(sizes, short, short));
    const gaps = 2 * Math.round(BAND_GAP_EM * f);
    if (style === 'placard') {
      const p = PLACARD;
      return Math.ceil(f * (p.padTop + p.padBottom + p.title + p.gap + p.details)) + 2 + gaps;
    }
    return Math.round(f * LINE.caption) + gaps;
  }

  // The band's cap for this style, given the smallest slot (or opening cell) height
  function maxBand(style, minHeight) {
    const s = SPEC[resolveStyle(style)];
    const frac = s.maxFraction || (P() ? P().CAPTION_MAX_FRACTION : 0.25);
    return Math.floor(frac * minHeight);
  }

  // ── Fields ──

  // The fields a slot shows, from an /api/metadata reply: [{ key, text, prio }], the date first
  // (prio 0, never hidden), then `order` ([{ flag, key }], highest priority first) up to
  // maxFields of them. meta: the slot's effective metadata settings (show_date, show_location...).
  // Used for pills and every style, so they all show the same fields.
  function fields(data, meta, { order, maxFields = Infinity, formatDate = d => d } = {}) {
    const out = [];
    if (!data || !meta) return out;
    if (meta.show_date && data.date_iso) out.push({ key: 'date', text: formatDate(data.date_iso), prio: 0 });
    let n = 0;
    (order || []).forEach(({ flag, key }, i) => {
      if (n >= maxFields || !meta[flag] || !data[key]) return;
      out.push({ key, text: String(data[key]), prio: i + 1 });
      n++;
    });
    return out;
  }

  const DATE_FORMATS = {
    long:   { year: 'numeric', month: 'long',    day: 'numeric' }, // July 4, 2019
    medium: { year: 'numeric', month: 'short',   day: 'numeric' }, // Jul 4, 2019
    short:  { year: 'numeric', month: 'numeric', day: 'numeric' }, // 7/4/2019
  };

  // An ISO date (YYYY-MM-DD) in config.date_format: long (the default) | medium | short | iso
  function formatDate(isoDate, fmt) {
    if (fmt === 'iso') return isoDate;
    const opts = DATE_FORMATS[fmt] || DATE_FORMATS.long;
    // Parse as local date (append T00:00 to avoid UTC shift)
    return new Intl.DateTimeFormat(undefined, opts).format(new Date(isoDate + 'T00:00'));
  }

  // ── Where a label may sit (shared, so the editor's preview places labels as the display does) ──

  // In a gallery opening, from PicMeGallery.layoutOpenings' result `o`: the caption band, or the
  // bottom of the shape's safe rect inside o.inset (centred in a shape that isn't a rectangle,
  // so a whisper stays inside a heart or a circle). matFrame: the opening's photo element.
  function openingArea(o, where, matFrame) {
    const b = o.bevel, i = o.inset;
    if (where === 'mat') {
      if (!o.caption) return null;
      const region = { x: o.box.x + b, y: o.box.y + b, w: o.box.w - 2 * b, h: o.box.h - 2 * b };
      return { rect: { ...o.caption }, align: 'start', region, inset: i };
    }
    return {
      rect: { x: o.safe.x + i, y: o.safe.y + i, w: o.safe.w - 2 * i, h: o.safe.h - 2 * i },
      align: o.shape === 'rect' || o.shape === 'rounded' ? 'end' : 'center',
      scrim: { host: matFrame, left: 0, right: 0, bottom: 0, photoBottom: o.box.h - b },
      region: { x: b, y: b, w: o.box.w - 2 * b, h: o.box.h - 2 * b }, inset: i,
    };
  }

  // In a separate frame, in px of the overlay's parent: g = { edge (frame + mat), mat, band, w, h }.
  // host: the element whose padding box holds the photo at `mat` from its edges (the scrim's parent).
  function frameArea(g, where, host, inset = 10) {
    const region = { x: g.edge, y: g.edge, w: g.w - 2 * g.edge, h: g.h - 2 * g.edge - (g.band || 0) };
    if (where === 'mat') {
      return g.band > 0 ? { rect: { x: g.edge, y: g.h - g.edge - g.band, w: g.w - 2 * g.edge, h: g.band }, align: 'start',
                            region, inset } : null;
    }
    const e = g.edge + inset;
    return {
      rect: { x: e, y: e, w: g.w - 2 * e, h: g.h - 2 * e },
      align: 'end',
      scrim: { host, left: g.mat, right: g.mat, bottom: g.mat, photoBottom: g.h - g.edge },
      region, inset,
    };
  }

  // The part of `region` a photo or video of natural size nw x nh fills under object-fit: contain
  // (centred, as object-position's default)
  function containRect(region, nw, nh) {
    if (!(nw > 0 && nh > 0) || !(region.w > 0 && region.h > 0)) return region;
    const k = Math.min(region.w / nw, region.h / nh);
    const w = nw * k, h = nh * k;
    return { x: region.x + (region.w - w) / 2, y: region.y + (region.h - h) / 2, w, h };
  }

  // Fit (contain): narrow an area from the opening to the photo you see. A label on the mat starts
  // at the visible photo's left edge; a whisper and its scrim stay on the photo, off the bars.
  // natural: the media's { w, h }, or null (Fill, or not loaded yet) to leave the area as it is.
  function visibleArea(area, natural) {
    if (!area || !area.region || !natural) return area;
    const reg = area.region, c = containRect(reg, natural.w, natural.h);
    if (c.w >= reg.w - 0.5 && c.h >= reg.h - 0.5) return area;
    const r = { ...area.rect };
    if (area.where === 'mat' || !area.scrim) {
      const x = Math.max(r.x, c.x);
      return { ...area, rect: { ...r, x, w: Math.max(0, r.w - (x - r.x)) } };
    }
    const i = area.inset || 0;
    const x0 = Math.max(r.x, c.x + i), y0 = Math.max(r.y, c.y + i);
    const x1 = Math.min(r.x + r.w, c.x + c.w - i), y1 = Math.min(r.y + r.h, c.y + c.h - i);
    const s = area.scrim;
    return {
      ...area,
      rect: { x: x0, y: y0, w: Math.max(0, x1 - x0), h: Math.max(0, y1 - y0) },
      scrim: { ...s, left: s.left + (c.x - reg.x), right: s.right + (reg.x + reg.w - c.x - c.w),
               bottom: s.bottom + (reg.y + reg.h - c.y - c.h), photoBottom: c.y + c.h },
    };
  }

  // ── Colour ──

  function parseColor(c) {
    const s = String(c || '').trim();
    let m = s.match(/^#([0-9a-f]{3})$/i);
    if (m) return m[1].split('').map(h => parseInt(h + h, 16));
    m = s.match(/^#([0-9a-f]{6})/i);
    if (m) return [0, 2, 4].map(i => parseInt(m[1].slice(i, i + 2), 16));
    m = s.match(/rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)/i);
    if (m) return [m[1], m[2], m[3]].map(Number);
    return null;
  }

  // WCAG relative luminance (0..1)
  function luminance(c) {
    const rgb = parseColor(c);
    if (!rgb) return 1;
    const lin = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    const [r, g, b] = rgb.map(lin);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }

  function contrast(a, b) {
    const la = luminance(a), lb = luminance(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  }

  // { ink, rule, lift } for text straight on a mat of colour `mat`: the warm inks when one reads
  // well (LIFT_BELOW:1 or more), else pure black or white, whichever contrasts more (always at
  // least 4.58:1, the most any mat allows), set heavier
  function inkFor(mat) {
    const cd = contrast(mat, INK_DARK), cl = contrast(mat, INK_LIGHT);
    let dark = cd >= cl, ink = dark ? INK_DARK : INK_LIGHT;
    const lift = Math.max(cd, cl) < LIFT_BELOW;
    if (lift) {
      dark = contrast(mat, '#000000') >= contrast(mat, '#ffffff');
      ink = dark ? '#000000' : '#ffffff';
    }
    return { ink, rule: dark ? 'rgba(0, 0, 0, 0.55)' : 'rgba(255, 255, 255, 0.6)', lift };
  }

  // ── DOM ──

  function el(doc, tag, cls) {
    const e = doc.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }

  function item(doc, f) {
    const e = el(doc, 'span', 'lbl-item');
    e.textContent = f.text;
    e.dataset.key = f.key;
    return e;
  }

  // A line of items that scrolls when it is too wide: .lbl-line > .lbl-run > items
  function line(doc, cls, items) {
    const l = el(doc, 'div', `lbl-line ${cls}`);
    const run = el(doc, 'span', 'lbl-run');
    for (const f of items) run.appendChild(item(doc, f));
    l.appendChild(run);
    return l;
  }

  // The style's element for `shown` fields (date first)
  function build(doc, style, shown) {
    const root = el(doc, 'div', `lbl lbl-${style}`);
    const date = shown.find(f => f.key === 'date');
    const loc = shown.find(f => f.key === 'location');
    const rest = shown.filter(f => f !== date && f !== loc);
    if (style === 'caption') {
      root.appendChild(line(doc, 'lbl-main', shown));
    } else if (style === 'placard') {
      // The place is the title and the date leads the details; without a place the date is the title
      const title = loc || date || rest[0];
      root.appendChild(line(doc, 'lbl-title', title ? [title] : []));
      const details = shown.filter(f => f !== title);
      if (details.length) root.appendChild(line(doc, 'lbl-details', details));
    } else if (style === 'whisper') {
      if (loc) root.appendChild(line(doc, 'lbl-place', [loc]));
      if (date) root.appendChild(line(doc, 'lbl-date', [date]));
      if (rest.length) root.appendChild(line(doc, 'lbl-details', rest));
    }
    return root;
  }

  // Put `fields` in `overlay` in `style` (not pill). Positioned and fitted by place().
  function render(overlay, style, fieldList) {
    style = resolveStyle(style);
    clear(overlay);
    if (style === 'pill' || !fieldList || !fieldList.length) return;
    overlay.classList.add('label-styled');
    overlay.dataset.labelStyle = style;
    overlay._label = { style, fields: fieldList.slice() };
    overlay.appendChild(build(overlay.ownerDocument, style, fieldList));
  }

  // Back to a plain overlay (pills), dropping a whisper scrim
  function clear(overlay) {
    if (!overlay) return;
    overlay.innerHTML = '';
    overlay.classList.remove('label-styled', 'label-visible', 'label-lift');
    delete overlay.dataset.labelStyle;
    delete overlay.dataset.hidden;
    delete overlay.dataset.labelWhere;
    delete overlay.dataset.labelAlign;
    overlay._label = null;
    if (overlay._scrim) { overlay._scrim.remove(); overlay._scrim = null; }
  }

  // Where the label may sit, then fit it there.
  // area = { where: 'mat' | 'photo', rect: {x, y, w, h} (px in the overlay's parent),
  //          align: 'start' | 'end' | 'center', fontPx, matColor (mat styles),
  //          scrim: { host, left, right, bottom, photoBottom } (whisper: the photo's element,
  //          the scrim's px inside it, and the photo's bottom edge in the overlay's parent) }
  function place(overlay, area, opts = {}) {
    const st = overlay && overlay._label;
    if (!st || !area || !area.rect) return;
    st.area = area;
    const r = area.rect;
    overlay.dataset.labelWhere = area.where;
    overlay.dataset.labelAlign = area.align || 'start';
    Object.assign(overlay.style, {
      left: `${Math.round(r.x)}px`, top: `${Math.round(r.y)}px`, right: 'auto', bottom: 'auto',
      width: `${Math.max(0, Math.floor(r.w))}px`, height: `${Math.max(0, Math.floor(r.h))}px`,
      maxWidth: 'none', maxHeight: 'none', margin: '0',
    });
    overlay.style.setProperty('--label-font-size', `${area.fontPx}px`);
    if (st.style === 'caption') {
      const ink = inkFor(area.matColor);
      overlay.style.setProperty('--lbl-ink', ink.ink);
      overlay.style.setProperty('--lbl-rule', ink.rule);
      overlay.classList.toggle('label-lift', ink.lift);
    }
    if (st.style === 'whisper' && area.scrim && area.scrim.host) {
      if (!overlay._scrim || overlay._scrim.parentElement !== area.scrim.host) {
        if (overlay._scrim) overlay._scrim.remove();
        overlay._scrim = el(overlay.ownerDocument, 'div', 'lbl-scrim');
        area.scrim.host.appendChild(overlay._scrim);
      }
      overlay._scrim.classList.toggle('visible', overlay.classList.contains('label-visible'));
    } else if (overlay._scrim) {
      overlay._scrim.remove();
      overlay._scrim = null;
    }
    fit(overlay, opts);
  }

  // Hide optional fields from the lowest priority up until the label fits its area; the date is
  // never hidden. A line still too wide (a long date or place on its own) scrolls slowly if
  // opts.marquee(distance) gives an animation, else it is clipped at the area's edge.
  function fit(overlay, opts = {}) {
    const st = overlay && overlay._label;
    if (!st || !st.area) return;
    const { style, fields: all } = st;
    const r = st.area.rect;
    const doc = overlay.ownerDocument;
    const date = all.find(f => f.key === 'date');
    const optional = all.filter(f => f !== date).sort((a, b) => a.prio - b.prio);
    const minKeep = date ? 0 : Math.min(1, optional.length);
    // A whisper whose date alone is too big (a heart or a circle) tries again with the date at
    // the size of the rest (the screen's size stays, only the hierarchy flattens), then, like a
    // pill date that doesn't fit, at the smallest legible size
    const font = st.area.fontPx || MIN_TEXT_PX;
    // (the date alone then: smaller details would be under MIN_TEXT_PX)
    const most = optional.length;
    const passes = style === 'whisper'
      ? [[false, '', most], [true, '', most], [true, `${Math.min(1, MIN_TEXT_PX / font)}em`, minKeep]]
      : [[false, '', most]];
    let root = null;
    search:
    for (const [compact, size, maxKeep] of passes) {
      for (let keep = maxKeep; keep >= minKeep; keep--) {
        const kept = new Set(optional.slice(0, keep));
        const shown = all.filter(f => f === date || kept.has(f));
        const next = build(doc, style, shown);
        next.classList.toggle('lbl-compact', compact);
        next.style.fontSize = size;
        if (root) root.replaceWith(next); else { overlay.innerHTML = ''; overlay.appendChild(next); }
        root = next;
        if (root.offsetWidth <= r.w + 0.5 && root.offsetHeight <= r.h + 0.5) break search;
      }
    }
    overlay.dataset.hidden = String(all.length - root.querySelectorAll('.lbl-item').length);
    // Too wide even so: the lines clip at the area's edge and scroll
    root.style.maxWidth = `${Math.max(0, Math.floor(r.w))}px`;
    const lines = [...root.querySelectorAll('.lbl-line')];
    const distances = lines.map(l => l.firstChild.offsetWidth - l.clientWidth);
    lines.forEach((l, i) => {
      const scrolls = distances[i] >= 1;
      l.classList.toggle('scrolling', scrolls);
      l.firstChild.style.animation = scrolls && opts.marquee ? opts.marquee(distances[i]) : '';
    });
    sizeScrim(overlay, root);
  }

  // The whisper's scrim: from the photo's bottom edge to a little above the text
  function sizeScrim(overlay, root) {
    const st = overlay._label, s = st && st.area && st.area.scrim;
    if (!overlay._scrim || !s) return;
    const font = st.area.fontPx || 16;
    const top = st.area.rect.y + root.offsetTop;
    const h = Math.max(0, Math.round(s.photoBottom - top + 1.4 * font));
    Object.assign(overlay._scrim.style, { left: `${s.left}px`, right: `${s.right}px`, bottom: `${s.bottom}px`, height: `${h}px` });
  }

  // Fade the label in (and its scrim); call after place()
  function show(overlay, visible = true) {
    if (!overlay) return;
    overlay.classList.toggle('label-visible', visible);
    if (overlay._scrim) overlay._scrim.classList.toggle('visible', visible);
  }

  // Sample fields for the editor's previews
  const SAMPLE = { date_iso: '2019-07-04', location: 'Lake Tahoe, California', camera: 'Canon EOS R5',
                   megapixels: '45 MP', fstop: 'f/2.8', shutter_speed: '1/250s', iso: 'ISO 200', focal_length: '50mm' };

  window.PicMeLabels = {
    SPEC, STYLES, DEFAULT_STYLE, MIN_TEXT_PX, INK_DARK, INK_LIGHT, LIFT_BELOW, SAMPLE,
    resolveStyle, placement, fontPx, captionBand, maxBand, fields, formatDate, openingArea, frameArea, containRect, visibleArea,
    parseColor, luminance, contrast, inkFor,
    render, clear, place, fit, show,
  };
})();
