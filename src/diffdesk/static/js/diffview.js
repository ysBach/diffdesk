// File tabs, the diff editor (CodeMirror or Monaco), hunk mode and edit state of one subject.
import { api } from './api.js';
import { $, h, frameOnce } from './dom.js';
import * as settings from './settings.js';
import { hunkExcerpt } from './hunks.js';

const EDITORS = {
  codemirror: () => import('./editor-cm.js'),
  monaco: () => import('./editor-monaco.js'),
};
const LANGUAGE_BY_EXT = {
  py: 'python', pyi: 'python', rs: 'rust', md: 'markdown', toml: 'toml', yaml: 'yaml', yml: 'yaml',
};
const REBUILD_KEYS = ['editor', 'full_diff_limit'];
const EDITOR_KEYS = [
  'layout', 'word_wrap', 'ignore_whitespace', 'collapse_unchanged', 'context_lines',
  'theme', 'code_font', 'code_font_size', 'reduced_motion',
];

function languageOf(path) {
  const ext = path.split('/').pop().split('.').pop().toLowerCase();
  return LANGUAGE_BY_EXT[ext] || 'plaintext';
}

const short = (sha) => (sha ? String(sha).slice(0, 10) : 'none');
const lineCount = (f) => Math.max(Number(f.lines_base) || 0, Number(f.lines_head) || 0);

export class DiffView {
  /** @param {{onDirtyChange: () => void, onSave: () => void, onFile: () => void}} hooks */
  constructor(hooks) {
    this.hooks = hooks;
    this.tabs = $('file-tabs');
    this.info = $('file-info');
    this.host = $('editor');
    this.subject = null;
    this.files = [];
    this.index = -1;
    this.editor = null;
    this.editorKind = null;
    this.token = 0;
    this.changed = frameOnce(() => {
      this.renderTabs();
      this.hooks.onDirtyChange();
    });
    this.tabs.addEventListener('click', (e) => {
      const tab = e.target.closest('[data-i]');
      if (tab) this.showFile(Number(tab.dataset.i));
    });
    this.info.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-action]');
      if (!btn) return;
      if (btn.dataset.action === 'full') this.loadFullFile();
      else if (btn.dataset.action === 'codemirror') settings.set('editor', 'codemirror');
    });
  }

  editorOptions() {
    return {
      layout: settings.get('layout'),
      wrap: settings.get('word_wrap'),
      ignoreWhitespace: settings.get('ignore_whitespace'),
      collapse: settings.get('collapse_unchanged'),
      context: settings.get('context_lines'),
      theme: settings.resolvedTheme(),
      fontFamily: settings.get('code_font'),
      fontSize: settings.get('code_font_size'),
      reducedMotion: settings.reducedMotion(),
    };
  }

  /** Show `subject`; the caller has already dealt with unsaved edits of the previous one. */
  async open(subject, path) {
    this.token++;
    this.subject = subject;
    this.files = (subject.files || []).map((f) => ({
      ...(typeof f === 'string' ? { path: f } : f),
      mode: null, data: null, excerpt: null, context: null, saved: null, clean: null, current: null, forceFull: false,
    }));
    this.index = -1;
    if (!this.files.length) {
      this.clear();
      return;
    }
    const i = path ? this.files.findIndex((f) => f.path === path) : 0;
    await this.showFile(Math.max(i, 0));
  }

  /** Update per-file flags after a refresh, keeping loaded text and unsaved edits. */
  updateFiles(files) {
    for (const fresh of files || []) {
      if (typeof fresh !== 'object') continue;
      const f = this.files.find((x) => x.path === fresh.path);
      if (f) Object.assign(f, { lines_added: fresh.lines_added, lines_deleted: fresh.lines_deleted, edited: fresh.edited });
    }
    this.renderTabs();
  }

  current() {
    return this.files[this.index] || null;
  }

  /** Copy the editor text into the shown file. */
  capture() {
    const f = this.current();
    if (f && f.mode === 'full' && f.data && this.editor) f.current = this.editor.value();
  }

  // `clean` is the saved text as the editor returns it. Editors may normalise line endings,
  // so comparing with the raw server text would mark untouched files as changed.
  isDirty(f) {
    return f.mode === 'full' && f.current != null && f.current !== f.clean;
  }

  dirtyFiles() {
    this.capture();
    return this.files.filter((f) => this.isDirty(f));
  }

  wantsHunks(f) {
    if (f.forceFull || this.isDirty(f)) return false;
    return lineCount(f) > settings.get('full_diff_limit');
  }

  async showFile(i) {
    const f = this.files[i];
    if (!f) return;
    this.capture();
    this.index = i;
    const token = ++this.token;
    const mode = this.wantsHunks(f) ? 'hunks' : 'full';
    const context = settings.get('context_lines');
    const ignoreWhitespace = settings.get('ignore_whitespace');
    this.renderTabs();
    this.info.textContent = 'Loading the diff...';
    try {
      if (f.mode !== mode || !f.data
        || (mode === 'hunks' && (f.context !== context || f.ignoreWhitespace !== ignoreWhitespace))) {
        const s = this.subject;
        const data = await api.diff({
          id: s.id, pkg: s.package, path: f.path, mode, context, ignoreWhitespace, base: s.base, head: s.head,
        });
        if (token !== this.token) return;
        f.mode = mode;
        f.data = data;
        if (mode === 'full') {
          f.saved = data.edit ?? data.head ?? '';
          if (f.current == null) {
            f.current = f.saved;
            f.clean = f.saved;
          }
          f.edited = data.edit != null;
        } else {
          f.excerpt = hunkExcerpt(data.hunks || []);
          f.context = context;
          f.ignoreWhitespace = ignoreWhitespace;
        }
      }
      if (f.data.binary) {
        this.editor?.destroy();
        this.editor = null;
        this.editorKind = null;
        this.host.replaceChildren(h('div', { class: 'editor-note', text: 'This is a binary file. There is no text diff.' }));
        this.info.replaceChildren();
      } else {
        const editor = await this.ensureEditor();
        if (token !== this.token) return;
        this.showInEditor(f);
        this.renderInfo();
      }
    } catch (err) {
      if (token !== this.token) return;
      this.showError(err);
    }
    this.renderTabs();
    this.hooks.onDirtyChange();
    this.hooks.onFile();
  }

  /** Show `f` in the editor; for an unchanged file, take the editor's text as the clean text. */
  showInEditor(f) {
    const clean = f.mode === 'full' && !this.isDirty(f);
    this.editor.show(this.docFor(f), this.editorOptions());
    if (clean) {
      f.current = this.editor.value();
      f.clean = f.current;
    }
  }

  docFor(f) {
    if (f.mode === 'hunks') {
      const x = f.excerpt;
      return {
        original: x.original, modified: x.modified, language: languageOf(f.path),
        readOnly: true, numbersA: x.numbersA, numbersB: x.numbersB,
      };
    }
    const language = f.data.language && f.data.language !== 'plaintext' ? f.data.language : languageOf(f.path);
    return { original: f.data.base ?? '', modified: f.current, language, readOnly: f.data.head == null };
  }

  async ensureEditor() {
    const kind = settings.get('editor');
    if (this.editor && this.editorKind === kind) return this.editor;
    this.editor?.destroy();
    this.editor = null;
    this.editorKind = null;
    this.host.replaceChildren(h('div', { class: 'editor-note', text: kind === 'monaco' ? 'Loading Monaco from cdn.jsdelivr.net...' : 'Loading the editor...' }));
    const mod = await EDITORS[kind]();
    await mod.ready?.();
    const editor = new mod.Editor(this.host, {
      onChange: () => this.changed(),
      onSave: () => this.hooks.onSave(),
    });
    if (settings.get('editor') !== kind) {
      editor.destroy();
      return this.ensureEditor();
    }
    this.host.replaceChildren();
    this.editor = editor;
    this.editorKind = kind;
    return editor;
  }

  showError(err) {
    this.editor?.destroy();
    this.editor = null;
    this.editorKind = null;
    const monaco = settings.get('editor') === 'monaco';
    this.host.replaceChildren(h('div', { class: 'editor-note error', text: `Cannot show the diff: ${err.message}` }));
    this.info.replaceChildren(monaco ? h('button', { type: 'button', 'data-action': 'codemirror', text: 'Use CodeMirror' }) : '');
  }

  clear() {
    this.token++;
    this.editor?.destroy();
    this.editor = null;
    this.editorKind = null;
    this.host.replaceChildren();
    this.tabs.replaceChildren();
    this.info.replaceChildren();
  }

  /** React to changed settings without losing unsaved text. */
  applySettings(keys) {
    const f = this.current();
    if (!f) return;
    if (keys.some((k) => REBUILD_KEYS.includes(k))
      || (f.mode === 'hunks' && (keys.includes('context_lines') || keys.includes('ignore_whitespace')))) {
      this.showFile(this.index);
    } else if (keys.some((k) => EDITOR_KEYS.includes(k))) {
      this.editor?.update(this.editorOptions());
    }
  }

  loadFullFile() {
    const f = this.current();
    if (!f) return;
    f.forceFull = true;
    this.showFile(this.index);
  }

  /** Save every changed file. Returns the saved paths. */
  async save() {
    const dirty = this.dirtyFiles();
    const saved = [];
    try {
      for (const f of dirty) {
        const content = f.current;
        await api.edit({ id: this.subject.id, package: this.subject.package, path: f.path, content });
        f.saved = content;
        f.clean = content;
        f.edited = true;
        f.data.edit = content;
        saved.push(f.path);
      }
    } finally {
      this.renderTabs();
      this.renderInfo();
    }
    return saved;
  }

  /** What Discard edit does for the shown file: 'revert', 'remove' or `null` (nothing). */
  discardAction() {
    const f = this.current();
    if (!f || f.mode !== 'full') return null;
    if (this.isDirty(f)) return 'revert';
    return f.edited ? 'remove' : null;
  }

  /** Revert unsaved changes of the shown file, or remove its saved edit. */
  async discard() {
    this.capture();
    const f = this.current();
    const action = this.discardAction();
    if (action === 'remove') {
      await api.edit({ id: this.subject.id, package: this.subject.package, path: f.path, content: null });
      f.edited = false;
      f.data.edit = null;
      f.saved = f.data.head ?? '';
    }
    if (action) {
      f.current = f.saved;
      f.clean = f.saved;
      if (this.editor) this.showInEditor(f);
      this.renderTabs();
      this.renderInfo();
    }
    return action;
  }

  /** Forget unsaved changes in every file (used when leaving the subject). */
  discardAll() {
    for (const f of this.files) {
      if (f.mode === 'full') f.current = f.clean;
    }
  }

  renderTabs() {
    this.tabs.replaceChildren(...this.files.map((f, i) => {
      const active = i === this.index;
      return h('button', {
        type: 'button', role: 'tab', class: `tab${active ? ' active' : ''}`,
        'aria-selected': String(active), title: f.path, dataset: { i: String(i) },
      },
      f.path,
      f.lines_added != null ? h('span', { class: 'add', text: ` +${f.lines_added}` }) : null,
      f.lines_deleted != null ? h('span', { class: 'del', text: ` −${f.lines_deleted}` }) : null,
      f.edited ? h('span', { class: 'mark', text: 'edited' }) : null,
      this.isDirty(f) || (active && f.mode === 'full' && this.editor && f.data && this.editor.value() !== f.clean)
        ? h('span', { class: 'mark dirty', text: 'unsaved' }) : null);
    }));
  }

  renderInfo() {
    const f = this.current();
    const s = this.subject;
    if (!f || !s || !f.data) return;
    if (f.mode === 'hunks') {
      const lines = lineCount(f);
      const n = f.data.hunks?.length ?? 0;
      this.info.replaceChildren(
        h('span', { class: 'mode', text: 'Hunk mode' }),
        ` ${n} changed region${n === 1 ? '' : 's'}, ${f.context} context line${f.context === 1 ? '' : 's'}. `
        + `The file has ${lines.toLocaleString()} lines, more than the Full diff limit (${settings.get('full_diff_limit').toLocaleString()}). Read-only. `
        + (f.edited ? 'Your saved edit is not shown in hunk mode. ' : ''),
        h('button', { type: 'button', 'data-action': 'full', text: 'Load full file' }),
      );
      return;
    }
    const right = f.data.head == null
      ? 'The file is deleted at '
      : f.edited ? 'Right: your saved edit of ' : 'Right: ';
    this.info.replaceChildren(
      'Left: ', h('code', { text: short(s.base) }), ' (read-only). ', right, h('code', { text: short(s.head) }),
      f.data.head == null ? ' (read-only).' : ' (editable).',
    );
  }
}
