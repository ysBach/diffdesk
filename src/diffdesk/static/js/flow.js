// Flow view: packages in dependency order as SVG. Dependencies are drawn above the packages
// that depend on them. Layered layout by longest path.
import { api } from './api.js';
import { reviewedOf, STATUS_ORDER, STATUS_LABEL } from './dom.js';

const NS = 'http://www.w3.org/2000/svg';

/**
 * Layer of every node: 0 without dependencies, else 1 + the largest layer of its dependencies.
 *
 * A dependency that closes a cycle cannot satisfy that rule. Such edges are found by a
 * depth-first search in input order and returned in `cut`; the layers use the other edges.
 *
 * @param {string[]} nodes
 * @param {[string, string][]} deps `[node, dependency]` pairs. Self-edges are ignored.
 * @returns {{layer: Map<string, number>, cut: [string, string][]}}
 */
export function longestPathLayers(nodes, deps) {
  const out = new Map(nodes.map((n) => [n, []]));
  for (const [a, b] of deps) {
    if (a === b) continue;
    if (!out.has(a)) out.set(a, []);
    if (!out.has(b)) out.set(b, []);
    out.get(a).push(b);
  }
  const ON_PATH = 1;
  const DONE = 2;
  const mark = new Map();
  const kept = new Map([...out.keys()].map((n) => [n, []]));
  const cut = [];
  for (const root of out.keys()) {
    if (mark.has(root)) continue;
    mark.set(root, ON_PATH);
    const stack = [[root, 0]];
    while (stack.length) {
      const top = stack[stack.length - 1];
      const next = out.get(top[0])[top[1]++];
      if (next === undefined) {
        mark.set(top[0], DONE);
        stack.pop();
      } else if (mark.get(next) === ON_PATH) {
        cut.push([top[0], next]);
      } else {
        kept.get(top[0]).push(next);
        if (!mark.has(next)) {
          mark.set(next, ON_PATH);
          stack.push([next, 0]);
        }
      }
    }
  }
  const layer = new Map();
  for (const root of kept.keys()) {
    if (layer.has(root)) continue;
    const stack = [[root, 0]];
    while (stack.length) {
      const top = stack[stack.length - 1];
      const next = kept.get(top[0])[top[1]++];
      if (next === undefined) {
        stack.pop();
        let l = 0;
        for (const d of kept.get(top[0])) l = Math.max(l, layer.get(d) + 1);
        layer.set(top[0], l);
      } else if (!layer.has(next)) {
        stack.push([next, 0]);
      }
    }
  }
  return { layer, cut };
}

/**
 * Group nodes by layer. Inside a layer, nodes are sorted by the mean position of their
 * dependencies in the layers above (one barycenter pass, top to bottom), then by input order.
 */
export function orderLayers(nodes, deps, layer) {
  const rows = [];
  for (const n of nodes) (rows[layer.get(n)] ||= []).push(n);
  const position = new Map();
  const depsOf = new Map(nodes.map((n) => [n, []]));
  for (const [a, b] of deps) if (a !== b && depsOf.has(a)) depsOf.get(a).push(b);
  rows.forEach((row, r) => {
    if (r > 0) {
      const key = new Map(row.map((n, i) => {
        const p = depsOf.get(n).filter((d) => position.has(d)).map((d) => position.get(d));
        return [n, p.length ? p.reduce((s, x) => s + x, 0) / p.length : i];
      }));
      row.sort((a, b) => key.get(a) - key.get(b));
    }
    row.forEach((n, i) => position.set(n, (i + 0.5) / row.length));
  });
  return rows.filter(Boolean);
}

function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) node.setAttribute(k, String(v));
  node.append(...children);
  return node;
}

export class FlowView {
  constructor(el, { onSelect }) {
    this.el = el;
    this.onSelect = onSelect;
    this.selected = null;
    this.data = null;
  }

  async refresh(selected = this.selected) {
    this.selected = selected;
    this.data = await api.flow();
    this.render();
  }

  setSelected(pkg) {
    this.selected = pkg;
    for (const g of this.el.querySelectorAll('.node')) g.classList.toggle('selected', g.dataset.pkg === pkg);
  }

  render() {
    const { nodes, edges } = this.data;
    const names = nodes.map((n) => n.package);
    const byName = new Map(nodes.map((n) => [n.package, n]));
    const { layer, cut } = longestPathLayers(names, edges);
    const rows = orderLayers(names, edges, layer);
    const cutSet = new Set(cut.map(([a, b]) => `${a}\u0001${b}`));

    const fontSize = parseFloat(getComputedStyle(this.el).fontSize) || 13;
    const padX = fontSize * 0.8;
    const lineH = fontSize * 1.35;
    const boxH = lineH * 2 + fontSize * 1.1;
    const gapX = fontSize * 1.2;
    const gapY = fontSize * 2.6;

    const root = svg('svg', { role: 'img', 'aria-label': 'Packages in dependency order' });
    root.append(svg('defs', {}, svg('marker', {
      id: 'flow-arrow', viewBox: '0 0 10 10', refX: 10, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse',
    }, svg('path', { d: 'M0,0 L10,5 L0,10 z', class: 'arrow' }))));
    const edgeLayer = svg('g');
    const nodeLayer = svg('g');
    root.append(edgeLayer, nodeLayer);
    this.el.replaceChildren(root);
    if (!edges.length) {
      this.el.append(Object.assign(document.createElement('p'), {
        className: 'view-note',
        textContent: 'No dependencies are set. Add depends_on to the packages in diffdesk.toml.',
      }));
    }

    // Build nodes first, then measure all text in one pass, then place.
    const groups = new Map();
    for (const name of names) {
      const n = byName.get(name);
      const reviewed = reviewedOf(n);
      const title = svg('text', { 'font-weight': 600 }, name);
      const sub = svg('text', { class: 'sub' }, `${reviewed} of ${n.total} reviewed`);
      const g = svg('g', {
        class: `node${name === this.selected ? ' selected' : ''}`, tabindex: 0, role: 'button',
        'aria-label': `${name}: ${reviewed} of ${n.total} reviewed. Show only this package in the tree.`,
      }, svg('title', {}, `${name}\n${STATUS_ORDER.map((s) => `${STATUS_LABEL[s]}: ${n.by_status?.[s] || 0}`).join('\n')}`), title, sub);
      g.dataset.pkg = name;
      g.addEventListener('click', () => this.onSelect(name));
      g.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          this.onSelect(name);
        }
      });
      nodeLayer.append(g);
      groups.set(name, { g, title, sub, n });
    }
    const widths = new Map([...groups].map(([name, x]) => [name, Math.max(x.title.getComputedTextLength(), x.sub.getComputedTextLength()) + 2 * padX]));
    const boxW = Math.max(...widths.values(), fontSize * 6);

    const place = new Map();
    let width = 0;
    for (const row of rows) width = Math.max(width, row.length * boxW + (row.length - 1) * gapX);
    rows.forEach((row, r) => {
      const rowW = row.length * boxW + (row.length - 1) * gapX;
      const x0 = (width - rowW) / 2;
      row.forEach((name, i) => place.set(name, { x: x0 + i * (boxW + gapX), y: r * (boxH + gapY) }));
    });
    const height = rows.length * boxH + (rows.length - 1) * gapY;
    const pad = 2;
    root.setAttribute('viewBox', `${-pad} ${-pad} ${width + 2 * pad} ${height + 2 * pad}`);
    root.setAttribute('width', '100%');
    root.style.maxWidth = `${width + 2 * pad}px`;

    for (const [name, { g, title, sub, n }] of groups) {
      const { x, y } = place.get(name);
      g.prepend(svg('rect', { class: 'box', x, y, width: boxW, height: boxH, rx: 5 }));
      title.setAttribute('x', x + padX);
      title.setAttribute('y', y + lineH);
      sub.setAttribute('x', x + padX);
      sub.setAttribute('y', y + 2 * lineH);
      const barY = y + boxH - fontSize * 0.75;
      const barW = boxW - 2 * padX;
      let bx = x + padX;
      for (const s of STATUS_ORDER) {
        const count = n.by_status?.[s] || 0;
        if (!count || !n.total) continue;
        const w = (barW * count) / n.total;
        g.append(svg('rect', { class: `seg-${s}`, x: bx, y: barY, width: w, height: fontSize * 0.4 }));
        bx += w;
      }
      if (!n.total) g.append(svg('rect', { class: 'seg-pending', x: bx, y: barY, width: barW, height: fontSize * 0.4 }));
    }

    for (const [from, to] of edges) {
      if (from === to || !place.has(from) || !place.has(to)) continue;
      // `from` depends on `to`: the arrow runs from the dependency to the package.
      const a = place.get(to);
      const b = place.get(from);
      const reversed = cutSet.has(`${from}\u0001${to}`);
      const x1 = a.x + boxW / 2;
      const y1 = a.y + boxH;
      const x2 = b.x + boxW / 2;
      const y2 = b.y;
      const dy = (y2 - y1) / 2;
      edgeLayer.append(svg('path', {
        class: 'edge', d: `M${x1},${y1} C${x1},${y1 + dy} ${x2},${y2 - dy} ${x2},${y2}`,
        'marker-end': 'url(#flow-arrow)', 'stroke-dasharray': reversed ? '4 3' : null,
      }, svg('title', {}, reversed ? `${from} depends on ${to} (this closes a cycle)` : `${from} depends on ${to}`)));
    }
  }
}
