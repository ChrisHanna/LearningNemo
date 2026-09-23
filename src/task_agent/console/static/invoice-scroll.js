(function (root, factory) {
  const scroll = factory();
  if (typeof module === 'object' && module.exports) module.exports = scroll;
  else root.invoiceScroll = scroll;
})(typeof window === 'object' ? window : globalThis, function () {
  function capture(root, viewport) {
    const areas = new Map();
    for (const element of root.querySelectorAll('[data-scroll-key]')) {
      const key = element.getAttribute('data-scroll-key');
      areas.set(key, { left: element.scrollLeft, top: element.scrollTop,
        open: element.tagName === 'DETAILS' ? element.open : undefined });
    }
    return { left: viewport.scrollX, top: viewport.scrollY, areas };
  }
  function restore(root, snapshot, viewport) {
    if (!snapshot) return;
    const elements = [...root.querySelectorAll('[data-scroll-key]')];
    for (const element of elements) {
      const saved = snapshot.areas.get(element.getAttribute('data-scroll-key'));
      if (saved?.open !== undefined) element.open = saved.open;
    }
    for (const element of elements) {
      const saved = snapshot.areas.get(element.getAttribute('data-scroll-key'));
      if (saved) element.scrollTo({ left: saved.left, top: saved.top, behavior: 'instant' });
    }
    viewport.scrollTo({ left: snapshot.left, top: snapshot.top, behavior: 'instant' });
  }
  return { capture, restore };
});