// Treemap view on a canvas: packages, then subjects. Area = lines changed (added + deleted).
import { api } from './api.js';
import { h, frameOnce, STATUS_ORDER, STATUS_LABEL } from './dom.js';

/**
 * Squarified treemap layout (Bruls, Huizing and van Wijk, 2000).
 *
 * Rectangle areas are proportional to `value`. Items with `value <= 0` get no rectangle.
 *
 * @param {{value: number}[]} items
 * @param {{x: number, y: number, w: number, h: number}} rect
 * @returns {{item: object, x: number, y: number, w: number, h: number}[]}
 */
export function squarify(items, rect) {
  const list = items.filter((it) => it.value > 0).sort((a, b) => b.value - a.value);
  const total = list.reduce((s, it) => s + it.value, 0);
  const out = [];
  if (!total || !(rect.w > 0) || !(rect.h > 0)) return out;
  const areas = list.map((it) => (it.value * rect.w * rect.h) / total);
  // Worst aspect ratio of a row with area sum `s`, smallest area `lo`, largest `hi`, laid along `side`.
  const worst = (s, lo, hi, side) => Math.max((side * side * hi) / (s * s), (s * s) / (side * side * lo));
  let { x, y, w, h: ht } = rect;
  let start = 0;
  while (start < areas.length) {
    const side = Math.min(w, ht);
    let sum = areas[start];
    let lo = sum;
    let hi = sum;
    let best = worst(sum, lo, hi, side);
    let end = start + 1;
    while (end < areas.length) {
      const a = areas[end];
      const next = worst(sum + a, Math.min(lo, a), Math.max(hi, a), side);
      if (next > best) break;
      sum += a;
      lo = Math.min(lo, a);
      hi = Math.max(hi, a);
      best = next;
      end++;
    }
    const thick = sum / side;
    let offset = 0;
    for (let k = start; k < end; k++) {
      const len = areas[k] / thick;
      if (w >= ht) out.push({ item: list[k], x, y: y + offset, w: thick, h: len });
      else out.push({ item: list[k], x: x + offset, y, w: len, h: thick });
      offset += len;
    }
    if (w >= ht) {
      x += thick;
      w = Math.max(0, w - thick);
    } else {
      y += thick;
      ht = Math.max(0, ht - thick);
    }
    start = end;
  }
  return out;
}

function parseColor(text) {
  const s = text.trim();
  let m = /^#([0-9a-f]{3,8})$/i.exec(s);
  if (m) {
    let hex = m[1];
    if (hex.length <= 4) hex = [...hex].map((c) => c + c).join('');
    return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
  }
  m = /rgba?\(([^)]+)\)/.exec(s);
  return m ? m[1].split(/[ ,/]+/).slice(0, 3).map(Number) : [128, 128, 128];
}

function luminance([r, g, b]) {
  const f = (c) => {
    const v = c / 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

/** Black or white, whichever has the larger WCAG contrast ratio on `fill`. */
function textOn(fill) {
  const l = luminance(parseColor(fill));
  return (l + 0.05) / 0.05 >= 1.05 / (l + 0.05) ? '#000' : '#fff';
}

const linesOf = (it) => (it.lines_added || 0) + (it.lines_deleted || 0);

export class TreemapView {
  constructor(el, { onOpen }) {
    this.el = el;
    this.onOpen = onOpen;
    this.items = [];
    this.rects = [];
    this.groups = [];
    this.pkg = null;
    this.box = h('div', { class: 'canvas-box' });
    this.canvas = h('canvas', { role: 'img', 'aria-label': 'Treemap of subjects. Area is lines changed, colour is status.' });
    this.box.append(this.canvas);
    this.legend = h('div', { class: 'legend' });
    this.note = h('div', { class: 'view-note' });
    el.append(this.legend, this.box, this.note);
    this.tooltip = document.getElementById('tooltip');
    this.redraw = frameOnce(() => this.draw());
    new ResizeObserver(this.redraw).observe(this.box);
    this.canvas.addEventListener('mousemove', (e) => this.hover(e));
    this.canvas.addEventListener('mouseleave', () => { this.tooltip.hidden = true; });
    this.canvas.addEventListener('click', (e) => {
      const hit = this.hit(e);
      if (hit) this.onOpen(hit.item);
    });
  }

  async refresh(pkg = this.pkg) {
    this.pkg = pkg;
    this.items = await api.treemap(pkg);
    const zero = this.items.filter((it) => linesOf(it) <= 0);
    this.note.replaceChildren(zero.length
      ? `${zero.length} subject${zero.length === 1 ? ' has' : 's have'} no changed lines and ${zero.length === 1 ? 'is' : 'are'} not shown: ${zero.map((it) => it.id).join(', ')}.`
      : '');
    const counts = Object.fromEntries(STATUS_ORDER.map((s) => [s, this.items.filter((it) => it.status === s).length]));
    this.legend.replaceChildren(...STATUS_ORDER.map((s) => h('span', {}, h('i', { class: `st-${s}` }), `${STATUS_LABEL[s]} ${counts[s]}`)));
    this.redraw();
  }

  draw() {
    const rect = this.box.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const width = Math.floor(rect.width);
    const height = Math.floor(rect.height);
    if (!width || !height) return;
    this.canvas.width = width * dpr;
    this.canvas.height = height * dpr;
    const ctx = this.canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const css = getComputedStyle(document.documentElement);
    const color = (name) => css.getPropertyValue(name).trim();
    const fills = Object.fromEntries(STATUS_ORDER.map((s) => [s, color(`--st-${s}`)]));
    const bg = color('--bg');
    const fg = color('--fg');
    const font = parseFloat(css.getPropertyValue('--ui-font-size')) || 13;
    ctx.clearRect(0, 0, width, height);

    const byPkg = new Map();
    for (const it of this.items) {
      if (!byPkg.has(it.package)) byPkg.set(it.package, []);
      byPkg.get(it.package).push(it);
    }
    const pkgs = [...byPkg].map(([name, list]) => ({ name, list, value: list.reduce((s, it) => s + linesOf(it), 0) }));
    this.groups = squarify(pkgs, { x: 0, y: 0, w: width, h: height });
    this.rects = [];
    ctx.textBaseline = 'top';
    ctx.font = `${font * 0.85}px ${css.fontFamily}`;
    for (const g of this.groups) {
      const cells = squarify(g.item.list.map((it) => ({ ...it, value: linesOf(it) })), g);
      for (const c of cells) {
        const fill = fills[c.item.status] || fills.pending;
        ctx.fillStyle = fill;
        ctx.fillRect(c.x, c.y, c.w, c.h);
        ctx.strokeStyle = bg;
        ctx.lineWidth = 1;
        ctx.strokeRect(c.x + 0.5, c.y + 0.5, c.w - 1, c.h - 1);
        const label = c.item.id;
        const tw = ctx.measureText(label).width;
        if (tw + 6 <= c.w && font + 4 <= c.h) {
          ctx.fillStyle = textOn(fill);
          ctx.fillText(label, c.x + 3, c.y + 3);
        }
        this.rects.push(c);
      }
    }
    ctx.font = `600 ${font}px ${css.fontFamily}`;
    for (const g of this.groups) {
      ctx.strokeStyle = fg;
      ctx.lineWidth = 2;
      ctx.strokeRect(g.x + 1, g.y + 1, g.w - 2, g.h - 2);
      const tw = ctx.measureText(g.item.name).width;
      const lh = font * 1.3;
      if (tw + 8 <= g.w && lh + 8 <= g.h) {
        const y = g.y + g.h - lh - 3;
        ctx.fillStyle = bg;
        ctx.fillRect(g.x + 3, y, tw + 6, lh);
        ctx.fillStyle = fg;
        ctx.fillText(g.item.name, g.x + 6, y + (lh - font) / 2);
      }
    }
  }

  hit(e) {
    const r = this.canvas.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    return this.rects.find((c) => x >= c.x && x < c.x + c.w && y >= c.y && y < c.y + c.h) || null;
  }

  hover(e) {
    const hit = this.hit(e);
    const tip = this.tooltip;
    if (!hit) {
      tip.hidden = true;
      return;
    }
    const it = hit.item;
    tip.replaceChildren(
      h('b', { text: it.id }), ` ${it.title}`,
      h('div', { class: 'dim', text: `${it.package}${it.branch ? ` / ${it.branch}` : ''}. ${STATUS_LABEL[it.status] || it.status}. +${it.lines_added || 0} −${it.lines_deleted || 0} lines.` }),
    );
    tip.hidden = false;
    const pad = 12;
    const w = tip.offsetWidth;
    const ht = tip.offsetHeight;
    const left = e.clientX + pad + w > window.innerWidth ? e.clientX - pad - w : e.clientX + pad;
    const top = e.clientY + pad + ht > window.innerHeight ? e.clientY - pad - ht : e.clientY + pad;
    tip.style.left = `${Math.max(0, left)}px`;
    tip.style.top = `${Math.max(0, top)}px`;
  }
}
