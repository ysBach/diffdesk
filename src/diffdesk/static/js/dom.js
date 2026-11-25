export const $ = (id) => document.getElementById(id);

/**
 * Create an element.
 *
 * `attrs` keys: `class`, `text`, `style` (object), `on<event>` (listener), `dataset` (object);
 * other keys become attributes. `null` and `false` values are skipped.
 */
export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'style') Object.assign(node.style, value);
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child != null && child !== false) node.append(child);
  }
  return node;
}

export function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/** Plain text with `code` spans, as DOM nodes. No other Markdown. */
export function inlineCode(text) {
  return String(text ?? '').split(/(`[^`]+`)/).filter(Boolean).map((part) => (
    part.length > 1 && part.startsWith('`') && part.endsWith('`')
      ? h('code', { text: part.slice(1, -1) })
      : document.createTextNode(part)
  ));
}

export const STATUS_LABEL = {
  pending: 'Pending',
  accepted: 'Accepted',
  rejected: 'Rejected',
  change: 'Change requested',
};
export const STATUS_ORDER = ['accepted', 'change', 'rejected', 'pending'];

/** Stacked bar of `by_status` counts. */
export function statusBar(byStatus, total) {
  const bar = h('span', { class: 'bar', 'aria-hidden': 'true' });
  if (!total) return bar;
  for (const status of STATUS_ORDER) {
    const n = byStatus?.[status] || 0;
    if (n) bar.append(h('i', { class: `st-${status}`, style: { flexGrow: String(n) } }));
  }
  return bar;
}

export function statusBadge(status) {
  const s = STATUS_LABEL[status] ? status : 'pending';
  return h('span', { class: `badge st-${s}`, text: STATUS_LABEL[s] });
}

/** `reviewed` count from a node that has `total` and `by_status`. */
export function reviewedOf(node) {
  if (typeof node.reviewed === 'number') return node.reviewed;
  return (node.total || 0) - (node.by_status?.pending || 0);
}

/** Schedule `fn` for the next animation frame; repeated calls in one frame run it once. */
export function frameOnce(fn) {
  let pending = false;
  return () => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      fn();
    });
  };
}
