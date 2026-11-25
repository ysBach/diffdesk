// User settings. Value order: built-in default < server `[ui]` default (`/api/meta` `ui_defaults`)
// < the user's own choice in localStorage. Only the user's own choices are stored.
import { $, h } from './dom.js';

const STORAGE_KEY = 'diffdesk.settings';

export const FIELDS = [
  {
    key: 'theme', label: 'Theme', type: 'select', def: 'system',
    options: [['system', 'Follow the system'], ['light', 'Light'], ['dark', 'Dark'], ['contrast', 'High contrast']],
  },
  { key: 'accent', label: 'Accent colour', type: 'color', def: '' },
  { key: 'ui_font_size', label: 'Text size (px)', type: 'number', min: 1, def: 13 },
  { key: 'code_font', label: 'Code font', type: 'text', def: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' },
  { key: 'code_font_size', label: 'Code size (px)', type: 'number', min: 1, def: 12 },
  {
    key: 'editor', label: 'Diff editor', type: 'select', def: 'codemirror',
    options: [['codemirror', 'CodeMirror (bundled, works offline)'], ['monaco', 'Monaco (loads from the internet)']],
  },
  {
    key: 'layout', label: 'Diff layout', type: 'select', def: 'side-by-side',
    options: [['side-by-side', 'Side by side'], ['inline', 'Inline']],
  },
  { key: 'word_wrap', label: 'Wrap long lines', type: 'checkbox', def: false },
  {
    key: 'ignore_whitespace', label: 'Ignore whitespace changes', type: 'checkbox', def: false,
    help: 'Full diffs ignore spaces and tabs at the start and end of lines. Hunk mode uses git diff -w.',
  },
  {
    key: 'collapse_unchanged', label: 'Fold unchanged lines', type: 'checkbox', def: false,
    help: 'Full diffs only. Keeps the context lines around each change.',
  },
  {
    key: 'context_lines', label: 'Context lines', type: 'number', min: 0, def: 3,
    help: 'Unchanged lines shown around each change in hunk mode and when unchanged lines are folded.',
  },
  {
    key: 'full_diff_limit', label: 'Full diff limit (lines)', type: 'number', min: 1, def: 20000,
    help: 'Files with more lines open in hunk mode. The default is arbitrary.',
  },
  {
    key: 'reduced_motion', label: 'Motion', type: 'select', def: 'system',
    options: [['system', 'Follow the system'], ['reduce', 'Reduce motion'], ['allow', 'Allow motion']],
  },
];

export const SHORTCUTS = [
  ['j / k', 'Next / previous subject'],
  ['Up / Down', 'Move in the tree'],
  ['Page Up / Page Down', 'Move one page in the tree'],
  ['Home / End', 'Move to the first / last row of the tree'],
  ['Right / Left', 'Expand / collapse a tree row'],
  ['Enter or Space', 'Open the subject or file, or expand the row'],
  ['Cmd+S or Ctrl+S', 'Save edit (in the editor)'],
  ['Cmd+F or Ctrl+F', 'Search in the editor'],
  ['Escape, then Tab', 'Leave the CodeMirror editor with the keyboard'],
];

const byKey = new Map(FIELDS.map((f) => [f.key, f]));
let serverDefaults = {};
let overrides = loadOverrides();
const listeners = new Set();
const darkQuery = matchMedia('(prefers-color-scheme: dark)');
const motionQuery = matchMedia('(prefers-reduced-motion: reduce)');

function loadOverrides() {
  try {
    const data = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    return data && typeof data === 'object' ? data : {};
  } catch {
    return {};
  }
}

function storeOverrides() {
  try {
    if (Object.keys(overrides).length) localStorage.setItem(STORAGE_KEY, JSON.stringify(overrides));
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage blocked: settings last until the page closes.
  }
}

/** Return `value` as the type of setting `key`, or `undefined` if it does not fit. */
function coerce(key, value) {
  const field = byKey.get(key);
  if (!field || value == null) return undefined;
  if (field.type === 'checkbox') return typeof value === 'boolean' ? value : undefined;
  if (field.type === 'number') {
    const n = Number(value);
    return Number.isInteger(n) && n >= (field.min ?? -Infinity) ? n : undefined;
  }
  if (field.type === 'select') return field.options.some(([v]) => v === value) ? value : undefined;
  return typeof value === 'string' ? value : undefined;
}

function defaultOf(key) {
  return coerce(key, serverDefaults[key]) ?? byKey.get(key).def;
}

export function get(key) {
  return coerce(key, overrides[key]) ?? defaultOf(key);
}

export function set(key, value) {
  const v = coerce(key, value);
  if (v === undefined) return;
  const before = get(key);
  if (v === defaultOf(key)) delete overrides[key];
  else overrides[key] = v;
  storeOverrides();
  if (get(key) !== before) changed([key]);
}

export function setServerDefaults(defaults) {
  const before = Object.fromEntries(FIELDS.map((f) => [f.key, get(f.key)]));
  serverDefaults = defaults && typeof defaults === 'object' ? defaults : {};
  changed(FIELDS.map((f) => f.key).filter((k) => get(k) !== before[k]));
}

export function reset() {
  const before = Object.fromEntries(FIELDS.map((f) => [f.key, get(f.key)]));
  overrides = {};
  storeOverrides();
  changed(FIELDS.map((f) => f.key).filter((k) => get(k) !== before[k]));
}

/** Call `fn(keys)` after settings change. */
export function onChange(fn) {
  listeners.add(fn);
}

/** `light`, `dark` or `contrast`, with `system` resolved. */
export function resolvedTheme() {
  const theme = get('theme');
  if (theme !== 'system') return theme;
  return darkQuery.matches ? 'dark' : 'light';
}

export function reducedMotion() {
  const m = get('reduced_motion');
  return m === 'reduce' || (m === 'system' && motionQuery.matches);
}

function changed(keys) {
  if (!keys.length) return;
  apply();
  syncForm();
  for (const fn of listeners) fn(keys);
}

function apply() {
  const root = document.documentElement;
  root.dataset.theme = resolvedTheme();
  root.dataset.motion = reducedMotion() ? 'reduce' : 'allow';
  root.style.setProperty('--ui-font-size', `${get('ui_font_size')}px`);
  root.style.setProperty('--code-font', get('code_font'));
  root.style.setProperty('--code-font-size', `${get('code_font_size')}px`);
  const accent = get('accent');
  if (accent) root.style.setProperty('--accent', accent);
  else root.style.removeProperty('--accent');
}

function systemChanged() {
  apply();
  for (const fn of listeners) fn(['theme', 'reduced_motion']);
}

/* Settings panel */

const inputs = new Map();

function buildForm() {
  const box = $('settings-fields');
  for (const field of FIELDS) {
    const id = `set-${field.key}`;
    let input;
    if (field.type === 'select') {
      input = h('select', { id }, field.options.map(([v, label]) => h('option', { value: v, text: label })));
    } else if (field.type === 'checkbox') {
      input = h('input', { id, type: 'checkbox' });
    } else if (field.type === 'number') {
      input = h('input', { id, type: 'number', min: field.min, step: 1 });
    } else if (field.type === 'color') {
      input = h('input', { id, type: 'color' });
    } else {
      input = h('input', { id, type: 'text', spellcheck: 'false' });
    }
    input.addEventListener(field.type === 'text' || field.type === 'number' ? 'change' : 'input', () => {
      const value = field.type === 'checkbox' ? input.checked : input.value;
      set(field.key, value);
      // A rejected value (for example 3.5 lines) shows the current setting again.
      if (field.type === 'number') input.value = String(get(field.key));
      syncForm();
    });
    inputs.set(field.key, input);
    const extra = field.type === 'color'
      ? h('button', { type: 'button', class: 'link', text: 'Use the theme colour', onclick: () => set('accent', '') })
      : null;
    box.append(h('div', { class: `field field-${field.type}` },
      h('label', { for: id, text: field.label }),
      h('div', { class: 'control' }, input, extra, field.help ? h('small', { text: field.help }) : null)));
  }
  $('shortcut-list').append(...SHORTCUTS.map(([keys, what]) => h('tr', {}, h('th', {}, h('kbd', { text: keys })), h('td', { text: what }))));
  $('settings-reset').addEventListener('click', () => reset());
}

function syncForm() {
  for (const [key, input] of inputs) {
    const value = get(key);
    if (input.type === 'checkbox') input.checked = value;
    else if (input.type === 'color') {
      input.value = value || getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#0a66c2';
    } else if (document.activeElement !== input) input.value = String(value);
  }
}

export function init() {
  apply();
  buildForm();
  syncForm();
  darkQuery.addEventListener('change', systemChanged);
  motionQuery.addEventListener('change', systemChanged);
  $('settings-open').addEventListener('click', () => {
    syncForm();
    $('settings').showModal();
  });
}
