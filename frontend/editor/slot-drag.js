// Drag-and-drop between elements tagged with data-drag-key, for mouse, pen and touch.
//
// Knows nothing about slots or config: attach() it to one or more container elements and it
// calls onDrop(fromKey, toKey) when an element is dropped on another. Containers can be
// re-rendered freely (listeners are delegated). Pointer Events rather than HTML5 drag-and-drop,
// which doesn't fire for touch on iOS Safari.
//
// Mouse and pen: the drag starts once the pointer moves a few pixels, so a click still clicks.
// Touch: the drag starts after a long press, so dragging a finger across the page still scrolls
// it. Once a drag has started, the click that follows it is swallowed.
(function () {
  'use strict';

  const TOUCH_SLOP = 8;  // px a finger may wander during the long press before it counts as a scroll

  let state = null;          // the press or drag in progress
  let suppressClick = false; // swallow the click that follows a drag
  let documentHooked = false;

  function hookDocument() {
    if (documentHooked) return;
    documentHooked = true;
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
    document.addEventListener('pointercancel', () => end(false));
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && state?.started) cancel(); });
    // Only a non-passive listener may stop the page scrolling under a touch drag
    document.addEventListener('touchmove', e => { if (state?.started) e.preventDefault(); }, { passive: false });
    // A long press would otherwise open the context menu (Android) or text selection
    document.addEventListener('contextmenu', e => { if (state) e.preventDefault(); });
    document.addEventListener('click', e => {
      if (!suppressClick) return;
      suppressClick = false;
      e.preventDefault();
      e.stopPropagation();
    }, true);
  }

  function attach(roots, opts = {}) {
    const session = {
      roots: (Array.isArray(roots) ? roots : [roots]).filter(Boolean),
      onDrop: opts.onDrop || (() => {}),
      canDrag: opts.canDrag || (() => true),
      canDrop: opts.canDrop || (() => true),
      longPressMs: opts.longPressMs ?? 300,
      mouseThreshold: opts.mouseThreshold ?? 5,
      ghost: opts.ghost || defaultGhost,
    };
    hookDocument();
    for (const root of session.roots) {
      root.addEventListener('pointerdown', e => onDown(session, e));
    }
  }

  function keyedElement(session, node) {
    const el = node?.closest?.('[data-drag-key]');
    return el && session.roots.some(r => r.contains(el)) ? el : null;
  }

  function onDown(session, e) {
    suppressClick = false;  // a drag that ended outside the window never got its click
    if (state || !e.isPrimary) return;
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    const el = keyedElement(session, e.target);
    if (!el || !session.canDrag(el)) return;
    state = {
      session, el, key: el.dataset.dragKey, pointerId: e.pointerId, touch: e.pointerType === 'touch',
      x0: e.clientX, y0: e.clientY, x: e.clientX, y: e.clientY,
      started: false, cancelled: false, target: null, ghost: null, timer: null,
    };
    if (state.touch) state.timer = setTimeout(start, session.longPressMs);
  }

  function start() {
    const s = state;
    if (!s || s.started) return;
    clearTimeout(s.timer);
    s.started = true;
    try { s.el.setPointerCapture(s.pointerId); } catch (err) { /* pointer already gone */ }
    const rect = s.el.getBoundingClientRect();
    s.offsetX = s.x0 - rect.left;
    s.offsetY = s.y0 - rect.top;
    s.ghost = s.session.ghost(s.el, rect);
    if (s.ghost) {
      s.ghost.classList.add('drag-ghost');
      s.ghost.style.transformOrigin = `${s.offsetX}px ${s.offsetY}px`;
      document.body.appendChild(s.ghost);
    }
    s.el.classList.add('drag-source');
    document.body.classList.add('drag-active');
    if (s.touch) navigator.vibrate?.(10);
    track(s.x, s.y);
  }

  // A scaled-down copy of the dragged element that follows the pointer
  function defaultGhost(el, rect) {
    const g = el.cloneNode(true);
    g.classList.remove('selected', 'drag-source', 'drop-target');
    g.removeAttribute('data-drag-key');
    const scale = Math.min(0.9, 240 / Math.max(rect.width, 1));
    Object.assign(g.style, {
      position: 'fixed', left: '0', top: '0', margin: '0', pointerEvents: 'none',
      width: `${rect.width}px`, height: `${rect.height}px`, transform: `scale(${scale})`,
    });
    return g;
  }

  function track(x, y) {
    const s = state;
    if (s.ghost) {
      s.ghost.style.left = `${x - s.offsetX}px`;
      s.ghost.style.top = `${y - s.offsetY}px`;
    }
    let target = keyedElement(s.session, document.elementFromPoint(x, y));
    if (target && (target.dataset.dragKey === s.key || !s.session.canDrop(s.key, target.dataset.dragKey))) target = null;
    if (target === s.target) return;
    s.target?.classList.remove('drop-target');
    target?.classList.add('drop-target');
    s.target = target;
  }

  function onMove(e) {
    const s = state;
    if (!s || e.pointerId !== s.pointerId || s.cancelled) return;
    s.x = e.clientX;
    s.y = e.clientY;
    if (s.started) { track(s.x, s.y); return; }
    const moved = Math.hypot(s.x - s.x0, s.y - s.y0);
    if (s.touch) {
      if (moved > TOUCH_SLOP) end(false);  // the finger is scrolling the page, not dragging
    } else if (moved > s.session.mouseThreshold) {
      start();
    }
  }

  function onUp(e) {
    if (!state || e.pointerId !== state.pointerId) return;
    if (state.started && !state.cancelled) track(e.clientX, e.clientY);
    end(true);
  }

  // Escape: drop nothing, but keep the press until the pointer is released
  function cancel() {
    clearVisuals(state);
    state.cancelled = true;
  }

  function clearVisuals(s) {
    s.ghost?.remove();
    s.ghost = null;
    s.el.classList.remove('drag-source');
    s.target?.classList.remove('drop-target');
    document.body.classList.remove('drag-active');
    try { s.el.releasePointerCapture(s.pointerId); } catch (err) { /* already released */ }
  }

  function end(drop) {
    const s = state;
    if (!s) return;
    state = null;
    clearTimeout(s.timer);
    if (!s.started) return;
    const target = s.cancelled ? null : s.target;
    clearVisuals(s);
    suppressClick = true;
    if (drop && target) s.session.onDrop(s.key, target.dataset.dragKey);
  }

  window.PicMeSlotDrag = {
    attach,
    defaultGhost,
    isDragging: () => !!state?.started,
  };
})();
