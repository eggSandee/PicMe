// Gallery mat mode (README → Gallery mat): one outer frame around the whole display, one mat
// filling it, and each slot an opening cut into the mat — optionally shaped and bevelled.
// Shared by the display and the editor (loaded as /display/gallery.js), so the editor's preview
// is the display's renderer. No fetch and no media logic: the caller makes each opening's media
// (makeSlot) and the openings keep the slot DOM (#slot-N.slot > .mat-frame, .slot-overlay), so
// photo, video and pill code works on them unchanged. Needs shapes.js; uses frames.js if loaded.
//
// config.gallery = { mat_color, outer_margin, gutter, wall_margin, bevel, bevel_width,
//                    bevel_color, show_frame, frame_style, frame_color, frame_width }
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

  // The gallery settings in effect: defaults, then config.gallery, then the time-of-day colours.
  // Values of the wrong type fall back to the default; numbers are clamped to GALLERY_LIMITS.
  function resolveGallery(config, tod) {
    const out = { ...GALLERY_DEFAULTS };
    const g = isObject(config) && isObject(config.gallery) ? config.gallery : {};
    for (const key of Object.keys(GALLERY_DEFAULTS)) {
      if (!has(g, key)) continue;
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
      el.style.borderImage = `url("${asset.src}") ${asset.slice} / ${w}px / 0 round`;
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

  // The bevel ring (outer outline minus the photo's outline), lit from the top left, and the
  // shadow the mat's cut edge casts onto the photo
  function drawDeco(deco, id, shape, w, h, b, gcfg, opts) {
    const S = window.PicMeShapes;
    deco.innerHTML = '';
    if (opts.deco === false) return;
    deco.setAttribute('viewBox', `0 0 ${w} ${h}`);
    const outer = S.get(shape).svgPath(0, 0, w, h);
    const inner = S.insetPath(shape, 0, 0, w, h, b);
    const uid = `${opts.idPrefix || 'pm'}-${id}`;
    const defs = svg('defs', {});
    const clip = svg('clipPath', { id: `${uid}-in` });
    clip.appendChild(svg('path', { d: inner }));
    const blur = svg('filter', { id: `${uid}-blur`, x: '-10%', y: '-10%', width: '120%', height: '120%' });
    blur.appendChild(svg('feGaussianBlur', { stdDeviation: Math.max(1, Math.min(3, w / 120)) }));
    const light = svg('linearGradient', { id: `${uid}-light`, x1: '0', y1: '0', x2: '1', y2: '1' });
    light.append(svg('stop', { offset: '0', 'stop-color': '#fff', 'stop-opacity': '0.45' }),
                 svg('stop', { offset: '0.5', 'stop-color': '#fff', 'stop-opacity': '0' }),
                 svg('stop', { offset: '0.5', 'stop-color': '#000', 'stop-opacity': '0' }),
                 svg('stop', { offset: '1', 'stop-color': '#000', 'stop-opacity': '0.22' }));
    defs.append(clip, blur, light);
    deco.appendChild(defs);

    // Inner shadow: everything outside the photo's outline, nudged down-right, blurred, and
    // clipped to the photo — the mat edge's shadow falls on the top and left of the photo
    const shadow = svg('g', { 'clip-path': `url(#${uid}-in)` });
    const pad = 40;
    shadow.appendChild(svg('path', {
      d: `M${-pad} ${-pad} H${w + pad} V${h + pad} H${-pad} Z ${inner}`,
      'fill-rule': 'evenodd', fill: '#000', opacity: '0.4',
      transform: 'translate(1.5 2)', filter: `url(#${uid}-blur)`,
    }));
    deco.appendChild(shadow);

    if (b > 0) {
      const ring = `${outer} ${inner}`;
      deco.appendChild(svg('path', { class: 'bevel', d: ring, 'fill-rule': 'evenodd', fill: gcfg.bevel_color }));
      deco.appendChild(svg('path', { d: ring, 'fill-rule': 'evenodd', fill: `url(#${uid}-light)` }));
    }
    // The crisp line where the knife entered the mat board
    deco.appendChild(svg('path', { d: outer, fill: 'none', stroke: 'rgba(0,0,0,0.18)', 'stroke-width': '1' }));
    if (opts.selectedId === id) {
      deco.appendChild(svg('path', { class: 'opening-selected', d: outer, fill: 'none',
                                     stroke: opts.selectColor || '#4a90d9', 'stroke-width': opts.selectWidth || 8 }));
    }
  }

  // Size each opening's box for its cell and shape, clip its media, draw its bevel and place its
  // pills. Returns { [slotId]: { shape, box, bevel, safe } } — px rects in the opening's own
  // coordinates; `safe` is where the pills may sit (inside the shape and the bevel).
  function layoutOpenings(root, gcfg, shapes, opts = {}) {
    const S = window.PicMeShapes;
    const result = {};
    for (const opening of root.querySelectorAll('.opening')) {
      const id = Number(opening.dataset.slotId);
      const shape = S.has(shapes[id]) ? shapes[id] : 'rect';
      const def = S.get(shape);
      const box = S.box(shape, opening.clientWidth, opening.clientHeight);
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

      // Pills: inside the photo's safe rect, --pill-inset in from its edges
      const s = def.safe(iw, ih);
      const safe = { x: b + s.x, y: b + s.y, w: s.w, h: s.h };
      const overlay = boxEl.querySelector('.slot-overlay');
      if (overlay) {
        const inset = parseFloat(getComputedStyle(opening).getPropertyValue('--pill-inset')) || 10;
        Object.assign(overlay.style, {
          left: `${Math.round(safe.x + inset)}px`,
          bottom: `${Math.round(box.h - (safe.y + safe.h) + inset)}px`,
          maxWidth: `${Math.max(0, Math.floor(safe.w - 2 * inset))}px`,
          maxHeight: `${Math.max(0, Math.floor(safe.h - 2 * inset))}px`,
        });
      }
      result[id] = { shape, box, bevel: b, safe };
    }
    return result;
  }

  window.PicMeGallery = {
    GALLERY_DEFAULTS, GALLERY_LIMITS, SMALL_SCREEN, TOD_KEYS, BACKING,
    effectiveMode, resolveGallery, slotShape, frameBorder, ensureStyles, build, applyStyles, layoutOpenings,
  };
})();
