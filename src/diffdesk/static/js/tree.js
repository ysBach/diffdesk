// Tree view: package > branch > subjects (commit order) > files.
// The whole tree is one virtual list: only the rows in view are in the DOM.
import { api } from './api.js';
import { h, statusBar, reviewedOf, frameOnce, STATUS_LABEL } from './dom.js';

// Page size of /api/subjects requests; it is the API's default `limit`.
const PAGE = 200;
const SEP = '\u0001';

const append = (el, ...nodes) => el.append(...nodes.filter(Boolean));
const pkgKey = (p) => `p${SEP}${p}`;
// Branch rows are keyed by repository too: two checkouts can have a branch of the same name.
const branchKey = (p, b, repo) => `b${SEP}${p}${SEP}${b ?? ''}${SEP}${repo ?? ''}`;
const subjectKey = (p, id) => `s${SEP}${p}${SEP}${id}`;

export class Tree {
  /**
   * @param {HTMLElement} el Scroll container.
   * @param {{onOpen: (target: {id: string, package: string, path?: string}) => void}} hooks
   */
  constructor(el, hooks) {
    this.el = el;
    this.hooks = hooks;
    this.packages = [];
    this.expanded = new Set();
    this.lists = new Map();
    this.files = new Map();
    this.filter = { q: '', status: '', pkg: null };
    this.flat = null;
    this.flatAbort = null;
    this.rows = [];
    this.focus = 0;
    this.activeKey = null;
    this.rowH = 0;
    this.viewH = 0;
    this.drawn = [-1, -1];

    this.spacer = h('div', { class: 'vl-spacer' });
    this.win = h('div', { class: 'vl-window', role: 'presentation' });
    el.append(this.spacer, this.win);
    this.schedule = frameOnce(() => this.draw());
    el.addEventListener('scroll', this.schedule, { passive: true });
    new ResizeObserver((entries) => {
      this.viewH = entries[0].contentRect.height;
      this.schedule();
    }).observe(el);
    el.addEventListener('keydown', (e) => this.onKey(e));
    el.addEventListener('click', (e) => this.onClick(e));
  }

  /* Data */

  async load() {
    this.packages = await api.tree();
    if (this.packages.length === 1) this.expanded.add(pkgKey(this.packages[0].package));
    this.rebuild();
  }

  /** Re-read the tree, every open branch list, every open file list and the search result. */
  async refresh() {
    this.packages = await api.tree();
    const jobs = [];
    for (const [key, list] of this.lists) {
      if (this.expanded.has(key)) jobs.push(this.fetchList(list, Math.max(list.items.length, 1)));
      else this.lists.delete(key);
    }
    for (const key of [...this.files.keys()]) {
      if (this.expanded.has(key)) jobs.push(this.fetchFiles(key));
      else this.files.delete(key);
    }
    if (this.flat) jobs.push(this.fetchList(this.flat, Math.max(this.flat.items.length, 1)));
    await Promise.all(jobs);
    this.rebuild();
  }

  newList(params) {
    return { params, total: null, items: [], loading: false, error: null };
  }

  /** Fetch pages from offset 0 until `want` items (or all) are loaded; replaces `list.items`. */
  async fetchList(list, want = PAGE, signal) {
    list.loading = true;
    list.error = null;
    try {
      const items = [];
      let total = Infinity;
      while (items.length < Math.min(want, total)) {
        const page = await api.subjects({ ...list.params, offset: items.length, limit: PAGE }, signal);
        total = page.total;
        items.push(...page.items);
        if (!page.items.length) break;
      }
      list.items = items;
      list.total = total === Infinity ? items.length : total;
    } catch (err) {
      if (err.name === 'AbortError') return;
      list.error = err.message;
    } finally {
      list.loading = false;
    }
  }

  async loadMore(list) {
    if (list.loading || list.items.length >= list.total) return;
    list.loading = true;
    this.rebuild();
    try {
      const page = await api.subjects({ ...list.params, offset: list.items.length, limit: PAGE });
      list.items.push(...page.items);
      list.total = page.total;
    } catch (err) {
      list.error = err.message;
    } finally {
      list.loading = false;
    }
    this.rebuild();
  }

  async fetchFiles(key) {
    const entry = this.files.get(key) || { files: null, error: null };
    this.files.set(key, entry);
    const [, pkg, id] = key.split(SEP);
    try {
      const subject = await api.subject(id, pkg);
      entry.files = subject.files || [];
      entry.error = null;
    } catch (err) {
      entry.error = err.message;
    }
  }

  async expand(row) {
    if (this.expanded.has(row.key)) return;
    this.expanded.add(row.key);
    if (row.kind === 'branch' && !this.lists.has(row.key)) {
      const list = this.newList({ package: row.pkg, branch: row.branch, repo: row.repo });
      this.lists.set(row.key, list);
      this.rebuild();
      await this.fetchList(list);
    } else if (row.kind === 'subject' && !this.files.has(row.key)) {
      this.files.set(row.key, { files: null, error: null });
      this.rebuild();
      await this.fetchFiles(row.key);
    }
    this.rebuild();
  }

  collapse(row) {
    this.expanded.delete(row.key);
    this.rebuild();
  }

  /** Search text and status filter. A non-empty filter shows a flat result list. */
  async setFilter({ q, status }) {
    this.filter.q = q;
    this.filter.status = status;
    await this.runFilter();
  }

  /** Show only package `pkg` (or all packages for `null`). */
  async setPackage(pkg) {
    this.filter.pkg = pkg;
    if (pkg) this.expanded.add(pkgKey(pkg));
    await this.runFilter();
  }

  async runFilter() {
    this.flatAbort?.abort();
    if (!this.filter.q && !this.filter.status) {
      this.flat = null;
      this.rebuild();
      return;
    }
    this.flatAbort = new AbortController();
    const list = this.newList({ q: this.filter.q, status: this.filter.status, package: this.filter.pkg });
    this.flat = list;
    this.rebuild();
    await this.fetchList(list, PAGE, this.flatAbort.signal);
    if (this.flat === list) this.rebuild();
  }

  /** Expand the path to a subject and scroll it into view. */
  async reveal(pkg, id, branch, repo) {
    this.activeKey = subjectKey(pkg, id);
    if (!this.flat) {
      if (this.filter.pkg && this.filter.pkg !== pkg) this.filter.pkg = null;
      this.expanded.add(pkgKey(pkg));
      const known = this.packages.find((p) => p.package === pkg)?.branches?.find((b) => b.name === branch && (repo === undefined || b.repo === repo));
      if (known) {
        const bk = branchKey(pkg, known.name, known.repo);
        this.expanded.add(bk);
        let list = this.lists.get(bk);
        if (!list) {
          list = this.newList({ package: pkg, branch: known.name, repo: known.repo });
          this.lists.set(bk, list);
          await this.fetchList(list);
        }
        while (!list.items.some((s) => s.id === id) && list.items.length < list.total && !list.error) {
          await this.loadMore(list);
        }
      }
    }
    this.rebuild();
    const i = this.rows.findIndex((r) => r.key === this.activeKey);
    if (i >= 0) {
      this.focus = i;
      this.scrollTo(i);
    }
  }

  setActive(pkg, id) {
    this.activeKey = id == null ? null : subjectKey(pkg, id);
    this.drawn = [-1, -1];
    this.schedule();
  }

  /* Rows */

  rebuild() {
    const rows = [];
    if (this.flat) this.flatRows(rows);
    else this.treeRows(rows);
    const focusKey = this.rows[this.focus]?.key;
    this.rows = rows;
    const i = focusKey ? rows.findIndex((r) => r.key === focusKey) : -1;
    this.focus = i >= 0 ? i : Math.min(this.focus, Math.max(rows.length - 1, 0));
    this.drawn = [-1, -1];
    this.schedule();
  }

  treeRows(rows) {
    const pkgs = this.filter.pkg ? this.packages.filter((p) => p.package === this.filter.pkg) : this.packages;
    if (!pkgs.length) rows.push({ kind: 'note', depth: 0, key: 'none', text: 'No subjects yet. Click Refresh later.' });
    for (const p of pkgs) {
      const pk = pkgKey(p.package);
      rows.push({ kind: 'package', depth: 0, key: pk, pkg: p.package, node: p, open: this.expanded.has(pk) });
      if (!this.expanded.has(pk)) continue;
      for (const b of p.branches || []) {
        const bk = branchKey(p.package, b.name, b.repo);
        const open = this.expanded.has(bk);
        rows.push({ kind: 'branch', depth: 1, key: bk, pkg: p.package, branch: b.name, repo: b.repo, node: b, open });
        if (open) this.listRows(rows, this.lists.get(bk), 2, false);
      }
    }
  }

  flatRows(rows) {
    this.listRows(rows, this.flat, 1, true);
    if (!this.flat.loading && !this.flat.error && !this.flat.items.length) {
      rows.push({ kind: 'note', depth: 0, key: 'nomatch', text: 'No subject matches the filter.' });
    }
  }

  listRows(rows, list, depth, grouped) {
    if (!list) return;
    let group = null;
    for (const s of list.items) {
      if (grouped) {
        const g = `${s.package}${SEP}${s.branch ?? ''}`;
        if (g !== group) {
          group = g;
          rows.push({ kind: 'group', depth: 0, key: `g${SEP}${g}`, text: s.branch ? `${s.package} / ${s.branch}` : s.package });
        }
      }
      const sk = subjectKey(s.package, s.id);
      const open = this.expanded.has(sk);
      rows.push({ kind: 'subject', depth, key: sk, pkg: s.package, item: s, open });
      if (!open) continue;
      const entry = this.files.get(sk);
      if (!entry || (!entry.files && !entry.error)) {
        rows.push({ kind: 'note', depth: depth + 1, key: `${sk}${SEP}loading`, text: 'Loading files...' });
      } else if (entry.error) {
        rows.push({ kind: 'note', depth: depth + 1, key: `${sk}${SEP}error`, text: entry.error, error: true });
      } else if (!entry.files.length) {
        rows.push({ kind: 'note', depth: depth + 1, key: `${sk}${SEP}empty`, text: 'No isolable diff' });
      } else {
        for (const f of entry.files) {
          rows.push({ kind: 'file', depth: depth + 1, key: `f${SEP}${s.package}${SEP}${s.id}${SEP}${f.path}`, pkg: s.package, item: s, file: f });
        }
      }
    }
    const key = `m${SEP}${JSON.stringify(list.params)}`;
    if (list.error) rows.push({ kind: 'note', depth, key: `${key}e`, text: list.error, error: true });
    else if (list.loading) rows.push({ kind: 'note', depth, key: `${key}l`, text: 'Loading...' });
    else if (list.total != null && list.items.length < list.total) {
      rows.push({ kind: 'more', depth, key, list, text: `Load ${list.total - list.items.length} more` });
    }
  }

  /* Drawing */

  measure() {
    const probe = this.rowEl({ kind: 'note', depth: 0, key: 'probe', text: 'M' }, 0);
    probe.style.visibility = 'hidden';
    this.win.append(probe);
    this.rowH = probe.getBoundingClientRect().height || 24;
    probe.remove();
  }

  /** Call after the UI font size changes. */
  remeasure() {
    this.rowH = 0;
    this.drawn = [-1, -1];
    this.schedule();
  }

  draw() {
    if (!this.rowH) this.measure();
    const top = this.el.scrollTop;
    const start = Math.max(0, Math.floor(top / this.rowH));
    const end = Math.min(this.rows.length, Math.ceil((top + this.viewH) / this.rowH) + 1);
    this.spacer.style.height = `${this.rows.length * this.rowH}px`;
    if (start === this.drawn[0] && end === this.drawn[1]) return;
    this.drawn = [start, end];
    this.win.style.transform = `translateY(${start * this.rowH}px)`;
    const nodes = [];
    for (let i = start; i < end; i++) {
      const row = this.rows[i];
      nodes.push(this.rowEl(row, i));
      if (row.kind === 'more') this.loadMore(row.list);
    }
    this.win.replaceChildren(...nodes);
    this.el.setAttribute('aria-activedescendant', this.focus >= start && this.focus < end ? `tr-${this.focus}` : '');
  }

  rowEl(row, i) {
    const cls = ['row', `row-${row.kind}`];
    if (i === this.focus) cls.push('focus');
    if (row.key === this.activeKey) cls.push('active');
    const expandable = row.kind === 'package' || row.kind === 'branch' || (row.kind === 'subject' && row.item.files_count > 0);
    const el = h('div', {
      id: `tr-${i}`,
      class: cls.join(' '),
      role: row.kind === 'group' || row.kind === 'note' ? 'presentation' : 'treeitem',
      'aria-level': String(row.depth + 1),
      'aria-expanded': expandable ? String(Boolean(row.open)) : null,
      'aria-selected': row.key === this.activeKey ? 'true' : null,
      dataset: { i: String(i) },
      style: { paddingLeft: `calc(${row.depth} * 1.1em + 4px)` },
    });
    el.append(h('span', { class: 'caret', 'aria-hidden': 'true', text: expandable ? (row.open ? '▾' : '▸') : '' }));
    if (row.kind === 'package' || row.kind === 'branch') {
      const n = row.node;
      const label = row.kind === 'branch' ? (n.name ?? '(no branch)') : row.pkg;
      el.title = row.kind === 'branch' && n.repo ? `${label}\n${n.repo}` : label;
      append(el,
        h('span', { class: 'name', text: label }),
        h('span', { class: 'count', text: `${reviewedOf(n)}/${n.total}` }),
        statusBar(n.by_status, n.total),
      );
    } else if (row.kind === 'subject') {
      const s = row.item;
      el.title = `${s.id}: ${s.title}\n${STATUS_LABEL[s.status] || s.status}${s.edited ? ', edited' : ''}`;
      append(el,
        h('span', { class: 'sid', text: s.id }),
        h('span', { class: 'name dim', text: s.title }),
        s.edited ? h('span', { class: 'mark', text: 'edited' }) : null,
        h('span', { class: 'count', text: s.status === 'pending' ? '0/1' : '1/1' }),
        statusBar({ [s.status]: 1 }, 1),
      );
    } else if (row.kind === 'file') {
      const f = row.file;
      el.title = f.path;
      append(el,
        h('span', { class: 'name mono', text: f.path }),
        f.edited ? h('span', { class: 'mark', text: 'edited' }) : null,
        h('span', { class: 'count' }, h('span', { class: 'add', text: `+${f.lines_added ?? 0}` }), ' ', h('span', { class: 'del', text: `−${f.lines_deleted ?? 0}` })),
      );
    } else if (row.kind === 'group') {
      el.append(h('span', { class: 'name group', text: row.text }));
    } else {
      el.append(h('span', { class: `name ${row.error ? 'error' : 'dim'}`, text: row.text }));
    }
    return el;
  }

  scrollTo(i) {
    if (!this.rowH) this.measure();
    const top = i * this.rowH;
    const view = this.viewH || this.el.clientHeight;
    if (top < this.el.scrollTop) this.el.scrollTop = top;
    else if (top + this.rowH > this.el.scrollTop + view) this.el.scrollTop = top + this.rowH - view;
    this.drawn = [-1, -1];
    this.schedule();
  }

  /* Input */

  activate(row) {
    if (!row) return;
    // Mark the target at once, so a quick second j or k steps on from it.
    if (row.kind === 'subject' || row.kind === 'file') this.setActive(row.pkg, row.item.id);
    if (row.kind === 'subject') this.hooks.onOpen({ id: row.item.id, package: row.pkg, branch: row.item.branch });
    else if (row.kind === 'file') this.hooks.onOpen({ id: row.item.id, package: row.pkg, branch: row.item.branch, path: row.file.path });
    else if (row.kind === 'more') this.loadMore(row.list);
    else if (row.kind === 'package' || row.kind === 'branch') {
      if (row.open) this.collapse(row);
      else this.expand(row);
    }
  }

  onClick(e) {
    const rowEl = e.target.closest('.row');
    if (!rowEl) return;
    const i = Number(rowEl.dataset.i);
    const row = this.rows[i];
    if (!row || row.kind === 'note' || row.kind === 'group') return;
    this.focus = i;
    if (e.target.closest('.caret') && row.kind === 'subject' && row.item.files_count > 0) {
      if (row.open) this.collapse(row);
      else this.expand(row);
    } else {
      this.activate(row);
    }
    this.drawn = [-1, -1];
    this.schedule();
  }

  moveFocus(i) {
    if (!this.rows.length) return;
    this.focus = Math.max(0, Math.min(this.rows.length - 1, i));
    this.scrollTo(this.focus);
  }

  onKey(e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const row = this.rows[this.focus];
    const page = Math.max(1, Math.floor((this.viewH || this.el.clientHeight) / (this.rowH || 24)));
    switch (e.key) {
      case 'ArrowDown': this.moveFocus(this.focus + 1); break;
      case 'ArrowUp': this.moveFocus(this.focus - 1); break;
      case 'PageDown': this.moveFocus(this.focus + page); break;
      case 'PageUp': this.moveFocus(this.focus - page); break;
      case 'Home': this.moveFocus(0); break;
      case 'End': this.moveFocus(this.rows.length - 1); break;
      case 'ArrowRight':
        if (row && !row.open && (row.kind === 'package' || row.kind === 'branch' || (row.kind === 'subject' && row.item.files_count > 0))) this.expand(row);
        else this.moveFocus(this.focus + 1);
        break;
      case 'ArrowLeft':
        if (row?.open) this.collapse(row);
        else if (row) {
          for (let i = this.focus - 1; i >= 0; i--) {
            if (this.rows[i].depth < row.depth && this.rows[i].kind !== 'group') {
              this.moveFocus(i);
              break;
            }
          }
        }
        break;
      case 'Enter':
      case ' ':
        this.activate(row);
        break;
      default:
        return;
    }
    e.preventDefault();
  }

  /** Open the next (`dir` = 1) or previous (`dir` = -1) subject, expanding groups on the way. */
  async step(dir) {
    let i = this.rows.findIndex((r) => r.key === this.activeKey);
    if (i < 0) i = dir > 0 ? -1 : this.rows.length;
    for (;;) {
      i += dir;
      if (i < 0 || i >= this.rows.length) return;
      const row = this.rows[i];
      if (row.kind === 'subject') {
        this.focus = i;
        this.scrollTo(i);
        this.activate(row);
        return;
      }
      if ((row.kind === 'package' || row.kind === 'branch') && !row.open) {
        await this.expand(row);
        const at = this.rows.findIndex((r) => r.key === row.key);
        if (dir > 0) i = at;
        else {
          let end = at + 1;
          while (end < this.rows.length && this.rows[end].depth > row.depth) end++;
          i = end;
        }
      } else if (row.kind === 'more' && dir > 0) {
        const before = this.rows.length;
        await this.loadMore(row.list);
        if (this.rows.length === before) return;
        i -= dir;
      }
    }
  }
}
