import { api } from './js/api.js';
import { $, h, reviewedOf } from './js/dom.js';
import * as settings from './js/settings.js';
import { Tree } from './js/tree.js';
import { DiffView } from './js/diffview.js';
import { renderSubject, renderStatus, renderLastVerdict, initWhyPanel, VERDICT } from './js/subject.js';

const state = {
  meta: null,
  current: null,
  openToken: 0,
  view: 'tree',
  flow: null,
  treemap: null,
  refreshing: null,
  busy: false,
};

const tree = new Tree($('view-tree'), { onOpen: (target) => openSubject(target) });
const diff = new DiffView({ onDirtyChange: updateEditButtons, onSave: () => saveEdits(), onFile: () => writeHash() });

function showMessage(text, kind = '') {
  const el = $('message');
  el.textContent = text;
  el.className = `message${kind ? ` ${kind}` : ''}`;
}

/* Counts and title */

function renderMeta() {
  const m = state.meta;
  if (!m) return;
  const title = m.title || m.name || 'diffdesk';
  $('app-title').textContent = title;
  $('app-title').title = m.root ? `${title}\n${m.root}` : title;
  document.title = title;
  const total = m.counts?.total ?? 0;
  $('counter').textContent = `${reviewedOf(m.counts || {})} of ${total} reviewed`;
}

async function refreshMeta() {
  state.meta = await api.meta();
  settings.setServerDefaults(state.meta.ui_defaults);
  renderMeta();
}

/* Opening subjects */

function sameSubject(a, b) {
  return a && b && a.id === b.id && (b.package == null || a.package === b.package);
}

/**
 * Show a modal question. Resolves to the `value` of the chosen button, or '' for Escape.
 *
 * @param {[string, string, string?][]} buttons `[value, label, class]`; the first one (the safe choice) has focus.
 */
function ask(title, text, buttons) {
  const dialog = $('ask-dialog');
  $('ask-title').textContent = title;
  $('ask-text').textContent = text;
  $('ask-buttons').replaceChildren(...buttons.map(([value, label, cls]) => h('button', { type: 'submit', value, class: cls, text: label })));
  dialog.returnValue = '';
  dialog.showModal();
  $('ask-buttons').firstElementChild?.focus();
  return new Promise((resolve) => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue), { once: true });
  });
}

/** Ask what to do with unsaved edits. Returns `true` when it is safe to leave. */
async function confirmLeave() {
  const dirty = diff.dirtyFiles();
  if (!dirty.length) return true;
  const names = dirty.map((f) => f.path).join(', ');
  const choice = await ask('Unsaved edits', dirty.length === 1
    ? `This file has edits that are not saved: ${names}.`
    : `${dirty.length} files have edits that are not saved: ${names}.`, [
    ['stay', 'Stay here'],
    ['discard', 'Discard edits', 'reject'],
    ['save', 'Save edits', 'save'],
  ]);
  if (choice === 'save') return saveEdits();
  if (choice === 'discard') {
    diff.discardAll();
    updateEditButtons();
    return true;
  }
  return false;
}

/**
 * Open a subject. Every way to change the subject comes here, so unsaved edits are always checked.
 *
 * @param {{id: string, package?: string, branch?: string, path?: string}} target
 * @param {{reveal?: boolean}} options `reveal` expands the tree to the subject.
 */
async function openSubject(target, { reveal = false } = {}) {
  if (sameSubject(state.current, target)) {
    if (target.path) {
      const i = diff.files.findIndex((f) => f.path === target.path);
      if (i >= 0 && i !== diff.index) await diff.showFile(i);
    }
    return true;
  }
  const restore = () => tree.setActive(state.current?.package, state.current?.id ?? null);
  if (!(await confirmLeave())) {
    restore();
    writeHash();
    return false;
  }
  const token = ++state.openToken;
  let s;
  try {
    s = await api.subject(target.id, target.package);
  } catch (err) {
    if (token === state.openToken) restore();
    showMessage(`Cannot open ${target.id}: ${err.message}`, 'error');
    return false;
  }
  if (token !== state.openToken) return false;
  state.current = s;
  $('empty').hidden = true;
  $('subject').hidden = false;
  $('comment').value = '';
  showMessage('');
  renderSubject(s);
  const isolable = Array.isArray(s.files) && s.files.length > 0;
  $('no-diff').hidden = isolable;
  $('diff-area').hidden = !isolable;
  $('subject').classList.toggle('no-diff-mode', !isolable);
  tree.setActive(s.package, s.id);
  if (reveal) tree.reveal(s.package, s.id, s.branch, s.repo);
  await diff.open(s, target.path);
  if (token === state.openToken) writeHash();
  updateEditButtons();
  return true;
}

/* Location hash: #s=<id>&p=<package>&f=<path> */

function writeHash() {
  const s = state.current;
  if (!s) return;
  const q = new URLSearchParams({ s: s.id, p: s.package ?? '' });
  const file = sameSubject(diff.subject, s) ? diff.current()?.path : null;
  if (file) q.set('f', file);
  history.replaceState(null, '', `#${q}`);
}

function openFromHash() {
  const q = new URLSearchParams(location.hash.slice(1));
  const id = q.get('s');
  if (!id) return;
  openSubject({ id, package: q.get('p') || undefined, path: q.get('f') || undefined }, { reveal: true });
}

/* Verdicts and edits */

function setBusy(busy) {
  state.busy = busy;
  for (const id of ['btn-accept', 'btn-reject', 'btn-change']) $(id).disabled = busy;
  updateEditButtons();
}

async function sendVerdict(verdict) {
  const s = state.current;
  if (!s || state.busy) return;
  setBusy(true);
  try {
    await api.review({ id: s.id, package: s.package, verdict, comment: $('comment').value });
    $('comment').value = '';
    showMessage(`Saved: ${VERDICT[verdict]}.`, 'ok');
  } catch (err) {
    showMessage(`Cannot save the verdict: ${err.message}`, 'error');
    return;
  } finally {
    setBusy(false);
  }
  await refreshAll();
}

/** Save every changed file of the subject. Returns `true` on success. */
async function saveEdits() {
  if (!state.current || state.busy) return false;
  setBusy(true);
  let saved;
  try {
    saved = await diff.save();
  } catch (err) {
    showMessage(`Cannot save the edit: ${err.message}`, 'error');
    return false;
  } finally {
    setBusy(false);
  }
  showMessage(saved.length ? `Saved edit: ${saved.join(', ')}.` : 'No unsaved edits.', saved.length ? 'ok' : '');
  if (saved.length) refreshAll();
  return true;
}

async function discardEdit() {
  const action = diff.discardAction();
  const f = diff.current();
  if (!action || !f || state.busy) return;
  if (action === 'remove') {
    const choice = await ask('Remove the saved edit?', `The right side of ${f.path} shows the original new code again. Your saved edit file is deleted.`, [
      ['keep', 'Keep the edit'],
      ['remove', 'Remove the edit', 'reject'],
    ]);
    if (choice !== 'remove') return;
  }
  setBusy(true);
  try {
    await diff.discard();
    showMessage(action === 'remove' ? `Removed the saved edit of ${f.path}.` : `Discarded the unsaved changes in ${f.path}.`, 'ok');
  } catch (err) {
    showMessage(`Cannot remove the edit: ${err.message}`, 'error');
    return;
  } finally {
    setBusy(false);
  }
  if (action === 'remove') refreshAll();
}

function updateEditButtons() {
  const save = $('btn-save');
  const discard = $('btn-discard');
  if (!state.current) return;
  save.disabled = state.busy || !diff.dirtyFiles().length;
  const action = diff.discardAction();
  discard.disabled = state.busy || !action;
  discard.title = action === 'remove'
    ? 'Remove the saved edit of this file and show the original new code'
    : 'Undo the unsaved changes in this file';
}

/* Refresh: on Refresh, on tab focus and after every write. No timers. */

async function refreshCurrent() {
  const old = state.current;
  if (!old) return;
  let s;
  try {
    s = await api.subject(old.id, old.package);
  } catch (err) {
    showMessage(`The subject ${old.id} cannot be read now: ${err.message}`, 'error');
    return;
  }
  if (state.current !== old) return;
  state.current = s;
  const key = (x) => JSON.stringify([x.repo, x.base, x.head, (x.files || []).map((f) => (typeof f === 'string' ? f : f.path))]);
  const strip = ({ review, files, status, edited, ...rest }) => JSON.stringify(rest);
  if (strip(s) !== strip(old)) {
    renderSubject(s);
  } else {
    renderStatus(s);
    renderLastVerdict(s);
  }
  if (key(s) !== key(old)) {
    if (diff.dirtyFiles().length) {
      showMessage('The commits or files of this subject changed on disk. Save or discard your edits, then open it again.', 'error');
    } else {
      await diff.open(s, diff.current()?.path);
    }
  } else {
    diff.updateFiles(s.files);
  }
  updateEditButtons();
}

async function refreshOverview() {
  if (state.view === 'flow' && state.flow) await state.flow.refresh();
  if (state.view === 'treemap' && state.treemap) await state.treemap.refresh(tree.filter.pkg);
}

function refreshAll() {
  state.refreshing ||= (async () => {
    try {
      await Promise.all([refreshMeta(), tree.refresh(), refreshCurrent(), refreshOverview()]);
    } catch (err) {
      showMessage(`Refresh failed: ${err.message}`, 'error');
    } finally {
      state.refreshing = null;
    }
  })();
  return state.refreshing;
}

/* Overview views */

async function showView(view) {
  state.view = view;
  for (const btn of document.querySelectorAll('[data-view]')) btn.setAttribute('aria-selected', String(btn.dataset.view === view));
  for (const name of ['tree', 'flow', 'treemap']) $(`view-${name}`).hidden = name !== view;
  try {
    if (view === 'flow') {
      if (!state.flow) {
        const { FlowView } = await import('./js/flow.js');
        state.flow = new FlowView($('view-flow'), { onSelect: (pkg) => filterPackage(pkg) });
      }
      await state.flow.refresh(tree.filter.pkg);
    } else if (view === 'treemap') {
      if (!state.treemap) {
        const { TreemapView } = await import('./js/treemap.js');
        state.treemap = new TreemapView($('view-treemap'), {
          onOpen: (item) => openSubject({ id: item.id, package: item.package, branch: item.branch }, { reveal: true }),
        });
      }
      await state.treemap.refresh(tree.filter.pkg);
    }
  } catch (err) {
    showMessage(`Cannot show the ${view} view: ${err.message}`, 'error');
  }
}

async function filterPackage(pkg) {
  $('pkg-filter').hidden = !pkg;
  $('pkg-filter-name').textContent = pkg || '';
  await tree.setPackage(pkg);
  state.flow?.setSelected(pkg);
  if (pkg) await showView('tree');
  else if (state.view !== 'tree') await refreshOverview();
}

/* Settings */

function syncEditorToggle() {
  const kind = settings.get('editor');
  for (const btn of document.querySelectorAll('[data-editor]')) btn.setAttribute('aria-pressed', String(btn.dataset.editor === kind));
}

settings.onChange((keys) => {
  if (keys.includes('editor')) syncEditorToggle();
  if (keys.includes('ui_font_size')) {
    tree.remeasure();
    if (state.view === 'flow' && state.flow?.data) state.flow.render();
  }
  if (keys.some((k) => k === 'theme' || k === 'accent' || k === 'ui_font_size')) state.treemap?.redraw();
  diff.applySettings(keys);
});

/* Events */

$('btn-accept').addEventListener('click', () => sendVerdict('accept'));
$('btn-reject').addEventListener('click', () => sendVerdict('reject'));
$('btn-change').addEventListener('click', () => sendVerdict('change'));
$('btn-save').addEventListener('click', () => saveEdits());
$('btn-discard').addEventListener('click', () => discardEdit());
$('refresh').addEventListener('click', () => refreshAll());
$('pkg-filter-clear').addEventListener('click', () => filterPackage(null));
for (const btn of document.querySelectorAll('[data-view]')) btn.addEventListener('click', () => showView(btn.dataset.view));
for (const btn of document.querySelectorAll('[data-editor]')) btn.addEventListener('click', () => settings.set('editor', btn.dataset.editor));

const runFilter = () => tree.setFilter({ q: $('q').value.trim(), status: $('status-filter').value });
$('q').addEventListener('input', runFilter);
$('status-filter').addEventListener('change', runFilter);

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && state.meta) refreshAll();
});

window.addEventListener('beforeunload', (e) => {
  if (diff.dirtyFiles().length) {
    e.preventDefault();
    e.returnValue = '';
  }
});

window.addEventListener('hashchange', () => {
  const q = new URLSearchParams(location.hash.slice(1));
  if (q.get('s') && !sameSubject(state.current, { id: q.get('s'), package: q.get('p') || undefined })) openFromHash();
});

document.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented) return;
  const t = e.target;
  if (t instanceof Element && t.closest('input, textarea, select, [contenteditable], .cm-editor, .monaco-editor, dialog')) return;
  if (e.key === 'j') tree.step(1);
  else if (e.key === 'k') tree.step(-1);
  else return;
  e.preventDefault();
});

/* Start */

async function start() {
  settings.init();
  syncEditorToggle();
  initWhyPanel();
  try {
    await refreshMeta();
    await tree.load();
  } catch (err) {
    $('counter').textContent = 'Cannot load subjects';
    showMessage(err.message, 'error');
    $('empty').textContent = `Cannot load subjects: ${err.message}`;
    return;
  }
  openFromHash();
}

start();
