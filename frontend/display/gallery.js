// Gallery mat mode (README → Gallery mat): one outer frame around the whole display, one mat
// filling it, and each slot an opening cut into the mat — optionally shaped and bevelled.
// Shared by the display and the editor (loaded as /display/gallery.js), so the editor's preview
// is the display's renderer. No fetch and no media logic: the caller makes each opening's media
// (makeSlot) and the openings keep the slot DOM (#slot-N.slot > .mat-frame, .slot-overlay), so
// photo, video and pill code works on them unchanged. Needs shapes.js; uses frames.js if loaded.
//
// config.gallery = { mat_color, outer_margin, gutter, wall_margin, bevel, bevel_width,
//                    bevel_color, frame_width, frame_override, show_frame, frame_style, frame_color }
// The outer frame's on/off, style and colour follow config.frame_defaults (the editor's Frame
// section), like separate frames; gallery.show_frame/frame_style/frame_color apply only with
// frame_override: true.
// config.layout_modes = { "<layout id>": "gallery" | "slots" } — overrides a layout's own mode
(function () {
  // Widths are px at the 1920x1080 reference. A resolution scale factor would multiply these
  // (and the limits) in resolveGallery — the one place gallery sizes are read.
  const GALLERY_DEFAULTS = {
    mat_color: '#f4f1ea',
    outer_margin: 64,
    gutter: 28,
    wall_margin: 12,
    bevel: true,
    bevel_width: 4,
    bevel_color: '#fbfaf6',
    show_frame: true,
    frame_style: 'wood',
    frame_color: '#2c2318',
    frame_width: 48,
    frame_override: false,
  };
  const GALLERY_LIMITS = {
    outer_margin: [0, 120],
    gutter: [0, 80],
    wall_margin: [0, 64],
    bevel_width: [0, 16],
    frame_width: [0, 160],
  };
  // Small screens: margin, gutter and frame shrink to these fractions of the short side, so the
  // openings keep most of the screen. At 1080 px and up the limits above always fit.
  const SMALL_SCREEN = { outer_margin: 0.12, gutter: 0.08, frame_width: 1 / 6 };
  // Time-of-day periods change colours only: their widths are for separate-frame slots
  const TOD_KEYS = ['mat_color', 'frame_color', 'frame_style'];
  // Taken from config.frame_defaults unless frame_override; a missing key is what a separate
  // frame uses for it (the display's applyFrameStyle)
  const FRAME_KEYS = ['show_frame', 'frame_style', 'frame_color'];
  const FRAME_FALLBACK = { show_frame: true, frame_style: 'solid', frame_color: '#2c2318' };
  const BACKING = '#1b1b1b';  // behind a photo that doesn't fill its opening (Fit), or none yet
  const SVG_NS = 'http://www.w3.org/2000/svg';

  const isObject = v => !!v && typeof v === 'object' && !Array.isArray(v);
  const has = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

  // "gallery" or "slots" for this layout: config.layout_modes, else the layout's own default
  function effectiveMode(config, layout) {
    const modes = isObject(config) && isObject(config.layout_modes) ? config.layout_modes : {};
    const v = layout && has(modes, layout.id) ? modes[layout.id] : undefined;
    if (v === 'gallery' || v === 'slots') return v;
    return layout && layout.gallery === true ? 'gallery' : 'slots';
  }

  function clampNumber(key, v) {
    const n = Number(v);
    if (typeof v === 'boolean' || v === null || v === '' || !Number.isFinite(n)) return GALLERY_DEFAULTS[key];
    const [lo, hi] = GALLERY_LIMITS[key];
    return Math.round(Math.min(hi, Math.max(lo, n)));
  }

  // The gallery settings in effect: defaults, then config.gallery (the frame's look from
  // config.frame_defaults unless frame_override), then the time-of-day colours.
  // Values of the wrong type fall back to the default; numbers are clamped to GALLERY_LIMITS.
  function resolveGallery(config, tod) {
    const out = { ...GALLERY_DEFAULTS };
    const g = isObject(config) && isObject(config.gallery) ? config.gallery : {};
    const own = g.frame_override === true;
    if (!own) {
      const fd = isObject(config) && isObject(config.frame_defaults) ? config.frame_defaults : {};
      for (const key of FRAME_KEYS) {
        out[key] = typeof fd[key] === typeof FRAME_FALLBACK[key] && fd[key] !== '' ? fd[key] : FRAME_FALLBACK[key];
      }
    }
    for (const key of Object.keys(GALLERY_DEFAULTS)) {
      if (!has(g, key) || (!own && FRAME_KEYS.includes(key))) continue;
      const def = GALLERY_DEFAULTS[key];
      if (has(GALLERY_LIMITS, key)) out[key] = clampNumber(key, g[key]);
      else if (typeof g[key] === typeof def) out[key] = g[key];
    }
    if (isObject(tod)) {
      for (const key of TOD_KEYS) if (typeof tod[key] === 'string' && tod[key]) out[key] = tod[key];
    }
    return out;
  }

  // The slot's own shape, else the layout's shape for that opening, else a rectangle
  function slotShape(slotCfg, slotDef) {
    const S = window.PicMeShapes;
    if (slotCfg && S.has(slotCfg.shape)) return slotCfg.shape;
    if (slotDef && S.has(slotDef.shape)) return slotDef.shape;
    return 'rect';
  }

  function frameAsset(style) {
    const frames = window.PICME_FRAMES;
    return frames && style && has(frames, style) ? frames[style] : null;
  }

  // The outer frame, by the same rule as a slot's frame: a decorative style is a 9-slice
  // border-image (corners scale, edges tile), anything else a solid border. Capped to a sixth
  // of `availShort`. Returns the width used.
  function frameBorder(el, cfg, availShort) {
    const asset = cfg.show_frame ? frameAsset(cfg.frame_style) : null;
    const cap = Math.max(4, Math.floor(availShort * SMALL_SCREEN.frame_width));
    const w = cfg.show_frame ? Math.min(Number(cfg.frame_width) || 0, cap) : 0;
    if (asset && w > 0) {
      el.style.border = `${w}px solid transparent`;
      el.style.borderImage = `url("${asset.src}") ${asset.slice} / ${w}px / 0 ${asset.repeat || 'round'}`;
    } else {
      el.style.border = w > 0 ? `${w}px solid ${cfg.frame_color}` : 'none';
      el.style.borderImage = 'none';
    }
    el.dataset.frameStyle = asset && w > 0 ? cfg.frame_style : (w > 0 ? 'solid' : 'none');
    return w;
  }

  // ── DOM ──

  const CSS = `
    .gallery-frame { position: relative; box-sizing: border-box; width: 100%; height: 100%;
      background-clip: padding-box; box-shadow: 0 6px 22px rgba(0, 0, 0, 0.45); }
    .gallery-mat { position: relative; box-sizing: border-box; width: 100%; height: 100%; display: grid;
      box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.1), inset 2px 3px 10px rgba(0, 0, 0, 0.3); }
    .gallery .opening { position: relative; overflow: hidden; background: transparent; min-width: 0; min-height: 0; }
    .gallery .opening-box { position: absolute; }
    .gallery .opening .mat-frame { position: absolute; padding: 0; border: 0; background: ${BACKING}; }
    .gallery .opening-deco { position: absolute; left: 0; top: 0; width: 100%; height: 100%;
      pointer-events: none; overflow: visible; }
    .gallery .opening .slot-overlay { z-index: 10; }
    .gallery .opening .slot-overlay.caption { flex-direction: row; align-items: center; }
  `;

  function ensureStyles(doc = document) {
    if (doc.getElementById('picme-gallery-css')) return;
    const style = doc.createElement('style');
    style.id = 'picme-gallery-css';
    style.textContent = CSS;
    doc.head.appendChild(style);
  }

  // Replace `container`'s contents with the frame, the mat and an opening per slot.
  // makeSlot(slotDef) returns { matFrame, overlay } (the overlay is optional).
  // ids: false leaves out the #slot-N ids (the editor draws several copies of a layout).
  function build(container, layout, slotCount, { makeSlot, ids = true }) {
    ensureStyles(container.ownerDocument);
    container.classList.add('gallery');
    container.innerHTML = '';
    const frame = document.createElement('div');
    frame.className = 'gallery-frame';
    const mat = document.createElement('div');
    mat.className = 'gallery-mat';
    mat.style.gridTemplateColumns = layout.grid_template_columns;
    mat.style.gridTemplateRows = layout.grid_template_rows;
    for (let i = 0; i < slotCount; i++) {
      const slotDef = layout.slots[i];
      const opening = document.createElement('div');
      opening.className = 'slot opening';
      opening.dataset.slotId = slotDef.id;
      if (ids) opening.id = `slot-${slotDef.id}`;
      opening.style.gridArea = slotDef.grid_area;
      const box = document.createElement('div');
      box.className = 'opening-box';
      const { matFrame, overlay } = makeSlot(slotDef);
      const deco = document.createElementNS(SVG_NS, 'svg');
      deco.setAttribute('class', 'opening-deco');
      deco.setAttribute('aria-hidden', 'true');
      box.append(matFrame, deco);
      if (overlay) box.appendChild(overlay);
      opening.appendChild(box);
      mat.appendChild(opening);
    }
    frame.appendChild(mat);
    container.appendChild(frame);
  }

  // Frame, mat and openings for the current size of `root` (the element build() filled).
  // slotCfgs[i] belongs to layout.slots[i]. Returns layoutOpenings()'s result.
  function applyStyles(root, gcfg, layout, slotCfgs, opts = {}) {
    const frame = root.querySelector('.gallery-frame');
    const mat = root.querySelector('.gallery-mat');
    if (!frame || !mat) return {};
    root.style.display = 'block';
    root.style.padding = `${gcfg.wall_margin}px`;
    const short = Math.min(root.clientWidth, root.clientHeight);
    frameBorder(frame, gcfg, short);
    mat.style.background = gcfg.mat_color;
    mat.style.padding = `${Math.min(gcfg.outer_margin, Math.round(short * SMALL_SCREEN.outer_margin))}px`;
    mat.style.gap = `${Math.min(gcfg.gutter, Math.round(short * SMALL_SCREEN.gutter))}px`;
    const shapes = {};
    (layout.slots || []).forEach((def, i) => { shapes[def.id] = slotShape((slotCfgs || [])[i], def); });
    return layoutOpenings(root, gcfg, shapes, opts);
  }

  function svg(tag, attrs) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  }

  // The bevel ring (outer outline minus the photo's outline): one even light edge all round,
  // with no light direction and no shadow on the photo
  function drawDeco(deco, id, shape, w, h, b, gcfg, opts) {
    const S = window.PicMeShapes;
    deco.innerHTML = '';
    if (opts.deco === false) return;
    deco.setAttribute('viewBox', `0 0 ${w} ${h}`);
    const outer = S.get(shape).svgPath(0, 0, w, h);
    if (b > 0) {
      const inner = S.insetPath(shape, 0, 0, w, h, b);
      deco.appendChild(svg('path', { class: 'bevel', d: `${outer} ${inner}`, 'fill-rule': 'evenodd', fill: gcfg.bevel_color }));
    }
    // The crisp line where the knife entered the mat board, the same all round: on a light mat
    // it is what shows the (light) bevel
    deco.appendChild(svg('path', { class: 'cut-line', d: outer, fill: 'none', stroke: 'rgba(0,0,0,0.2)', 'stroke-width': '1' }));
    if (opts.selectedId === id) {
      deco.appendChild(svg('path', { class: 'opening-selected', d: outer, fill: 'none',
                                     stroke: opts.selectColor || '#4a90d9', 'stroke-width': opts.selectWidth || 8 }));
    }
  }

  // Size each opening's box for its cell and shape, clip its media, draw its bevel and place its
  // pills. Returns { [slotId]: { shape, cell, box, bevel, safe, inset, caption } } — px rects in
  // the opening's own coordinates; `safe` is where on-photo pills may sit (inside the shape and
  // the bevel), `caption` the band under the box for pills on the mat (null without one).
  // opts.captionBand (px): leave a band that tall under each box, and put the pills in it.
  // opts.placeOverlays: false leaves the pill overlays where they are (a measuring pass).
  function layoutOpenings(root, gcfg, shapes, opts = {}) {
    const S = window.PicMeShapes;
    const band = Math.max(0, Math.floor(Number(opts.captionBand) || 0));
    const result = {};
    for (const opening of root.querySelectorAll('.opening')) {
      const id = Number(opening.dataset.slotId);
      const shape = S.has(shapes[id]) ? shapes[id] : 'rect';
      const def = S.get(shape);
      const cell = { w: opening.clientWidth, h: opening.clientHeight };
      // The box fits above the caption band, and box and band are centred in the cell together
      const box = S.box(shape, cell.w, Math.max(0, cell.h - band));
      if (band) box.y = Math.max(0, Math.floor((cell.h - box.h - band) / 2));
      const b = gcfg.bevel ? Math.min(gcfg.bevel_width, Math.floor(Math.min(box.w, box.h) / 12)) : 0;
      opening.dataset.shape = shape;

      const boxEl = opening.querySelector('.opening-box');
      Object.assign(boxEl.style, { left: `${box.x}px`, top: `${box.y}px`, width: `${box.w}px`, height: `${box.h}px` });
      const matFrame = boxEl.querySelector('.mat-frame');
      const iw = Math.max(0, box.w - 2 * b), ih = Math.max(0, box.h - 2 * b);
      Object.assign(matFrame.style, { left: `${b}px`, top: `${b}px`, width: `${iw}px`, height: `${ih}px` });
      // Rectangles aren't clipped at all: clip-path can cost a TV its video overlay plane
      const clip = def.clip(iw, ih);
      matFrame.style.clipPath = clip;
      matFrame.style.webkitClipPath = clip;
      drawDeco(boxEl.querySelector('.opening-deco'), id, shape, box.w, box.h, b, gcfg, opts);

      const s = def.safe(iw, ih);
      const safe = { x: b + s.x, y: b + s.y, w: s.w, h: s.h };
      const inset = parseFloat(getComputedStyle(opening).getPropertyValue('--pill-inset')) || 10;
      // On the mat: one row under the box, starting at the photo's left edge (the shape's box
      // inside the bevel), like pills at the bottom left of a photo; it may run on past the
      // shape's right edge, up to --pill-inset from the cell's (never into the next opening)
      const captionX = Math.min(box.x + b, Math.max(0, cell.w - inset));
      const caption = band ? { x: captionX, y: box.y + box.h, w: Math.max(0, cell.w - captionX - inset), h: band } : null;
      const overlay = opening.querySelector('.slot-overlay');
      if (overlay && opts.placeOverlays !== false) {
        overlay.classList.toggle('caption', !!caption);
        if (caption) {
          // In the cell, not the box: the band is outside the box
          if (overlay.parentElement !== opening) opening.appendChild(overlay);
          Object.assign(overlay.style, {
            left: `${caption.x}px`, right: '', top: `${caption.y}px`, bottom: '',
            height: `${caption.h}px`, width: 'fit-content', margin: '', maxWidth: `${caption.w}px`, maxHeight: '',
          });
        } else {
          // On the photo: inside its safe rect, --pill-inset in from its edges
          if (overlay.parentElement !== boxEl) boxEl.appendChild(overlay);
          Object.assign(overlay.style, {
            left: `${Math.round(safe.x + inset)}px`, right: '', top: '',
            bottom: `${Math.round(box.h - (safe.y + safe.h) + inset)}px`, height: '', width: '', margin: '',
            maxWidth: `${Math.max(0, Math.floor(safe.w - 2 * inset))}px`,
            maxHeight: `${Math.max(0, Math.floor(safe.h - 2 * inset))}px`,
          });
        }
      }
      result[id] = { shape, cell, box, bevel: b, safe, inset, caption };
    }
    return result;
  }

  // applyStyles() plus the screen's labels (README → Metadata pills). Measures where each
  // opening's pills would go, takes the shortest side (one pill size for the whole screen:
  // PicMePills.sharedShort) and, for captions on the mat, lays out again with a band under every
  // opening. The band comes from the size measured before it, so the two can't feed back.
  // labels = { placement: 'mat' | 'photo', sizes: [i] -> slot i's { text_size, pill_size },
  //            or null when slot i puts no pill on screen, style: config.label_style (sizes the band) }
  // Returns { openings, short, band }: openings as layoutOpenings(); short to size the pills by.
  function layoutWithLabels(root, gcfg, layout, slotCfgs, labels, opts = {}) {
    const P = window.PicMePills;
    const mat = labels.placement === 'mat';
    let openings = applyStyles(root, gcfg, layout, slotCfgs, mat ? { ...opts, placeOverlays: false } : opts);
    const slots = layout.slots || [];
    const all = slots.map(def => openings[def.id]).filter(Boolean);
    const shown = slots.map((def, i) => (labels.sizes[i] ? openings[def.id] : null)).filter(Boolean);
    // A caption labels the whole photo; on the photo, pills keep --pill-inset inside the safe rect
    const area = o => (mat ? o.box : { w: o.safe.w - 2 * o.inset, h: o.safe.h - 2 * o.inset });
    const short = P ? P.sharedShort((shown.length ? shown : all).map(area)) : 0;
    let band = 0;
    if (mat) {
      if (P && shown.length && all.length) {
        // A label style's band (label-styles.js: a placard needs two lines), capped by the style
        const L = window.PicMeLabels, style = labels.style || 'pill';
        const need = Math.max(...labels.sizes.filter(Boolean).map(s => (L ? L.captionBand(style, s, short) : P.captionBand(s, short))));
        const minH = Math.min(...all.map(o => o.cell.h));
        band = Math.min(need, L ? L.maxBand(style, minH) : Math.floor(P.CAPTION_MAX_FRACTION * minH));
      }
      openings = applyStyles(root, gcfg, layout, slotCfgs, { ...opts, captionBand: band });
    }
    return { openings, short, band };
  }

  window.PicMeGallery = {
    GALLERY_DEFAULTS, GALLERY_LIMITS, SMALL_SCREEN, TOD_KEYS, FRAME_KEYS, BACKING,
    effectiveMode, resolveGallery, slotShape, frameBorder, ensureStyles, build, applyStyles, layoutOpenings,
    layoutWithLabels,
  };
})();
