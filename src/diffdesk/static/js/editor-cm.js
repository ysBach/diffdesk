// CodeMirror 6 merge view (bundled). Left side read-only, right side editable.
import * as CM from '../vendor/codemirror.js';

// The merge package's own default `scanLimit` (its `defaultDiffConfig`), kept when the diff is replaced.
const MERGE_DEFAULT_SCAN_LIMIT = 500;

const streamLanguages = new Map();

function languageSupport(language) {
  switch (language) {
    case 'python': return CM.python();
    case 'rust': return CM.rust();
    case 'markdown': return new CM.LanguageSupport(CM.markdownLanguage);
    case 'toml':
    case 'yaml':
      if (!streamLanguages.has(language)) {
        streamLanguages.set(language, CM.StreamLanguage.define(language === 'toml' ? CM.toml : CM.yaml));
      }
      return streamLanguages.get(language);
    default: return [];
  }
}

const t = CM.tags;
const highlight = CM.HighlightStyle.define([
  { tag: [t.keyword, t.controlKeyword, t.moduleKeyword, t.operatorKeyword], color: 'var(--syn-keyword)' },
  { tag: [t.function(t.variableName), t.function(t.propertyName), t.labelName], color: 'var(--syn-function)' },
  { tag: [t.definition(t.variableName), t.definition(t.propertyName), t.function(t.definition(t.variableName))], color: 'var(--syn-def)' },
  { tag: [t.typeName, t.className, t.namespace, t.macroName], color: 'var(--syn-type)' },
  { tag: [t.number, t.bool, t.null, t.atom, t.self], color: 'var(--syn-number)' },
  { tag: [t.string, t.special(t.string), t.regexp, t.character], color: 'var(--syn-string)' },
  { tag: [t.comment, t.lineComment, t.blockComment, t.docComment], color: 'var(--syn-comment)', fontStyle: 'italic' },
  { tag: [t.meta, t.annotation, t.processingInstruction], color: 'var(--syn-meta)' },
  { tag: t.propertyName, color: 'var(--syn-property)' },
  { tag: t.heading, color: 'var(--syn-keyword)', fontWeight: 'bold' },
  { tag: t.emphasis, fontStyle: 'italic' },
  { tag: t.strong, fontWeight: 'bold' },
  { tag: [t.link, t.url], color: 'var(--syn-string)', textDecoration: 'underline' },
  { tag: t.invalid, color: 'var(--bad)' },
]);

function editorTheme(dark) {
  return CM.EditorView.theme({
    '&': { backgroundColor: 'var(--bg)', color: 'var(--fg)' },
    '.cm-scroller': { fontFamily: 'var(--code-font)', fontSize: 'var(--code-font-size)', lineHeight: '1.5' },
    '.cm-gutters': { backgroundColor: 'var(--bg-side)', color: 'var(--fg-dim)', borderRight: '1px solid var(--border)' },
    '.cm-activeLine, .cm-activeLineGutter': { backgroundColor: 'var(--active-line)' },
    '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection': { backgroundColor: 'var(--selection)' },
    '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--fg)' },
    '.cm-panels': { backgroundColor: 'var(--bg-side)', color: 'var(--fg)' },
    '.cm-panels input, .cm-panels button': { font: 'inherit', color: 'inherit' },
    '.cm-searchMatch': { backgroundColor: 'var(--search-match)', outline: '1px solid var(--border)' },
    '.cm-selectionMatch': { backgroundColor: 'var(--selection-match)' },
    '&.cm-merge-a .cm-changedLine, .cm-deletedChunk': { backgroundColor: 'var(--diff-del-line)' },
    '&.cm-merge-b .cm-changedLine, .cm-inlineChangedLine': { backgroundColor: 'var(--diff-ins-line)' },
    '&.cm-merge-a .cm-changedText, .cm-deletedChunk .cm-deletedText': { background: 'var(--diff-del-text)' },
    '&.cm-merge-b .cm-changedText': { background: 'var(--diff-ins-text)' },
    '&.cm-merge-a .cm-changedLineGutter, .cm-deletedLineGutter': { background: 'var(--bad)' },
    '&.cm-merge-b .cm-changedLineGutter': { background: 'var(--ok)' },
    '.cm-collapsedLines': { color: 'var(--fg-dim)', background: 'var(--bg-side)' },
  }, { dark });
}

/**
 * Remove spaces and tabs at the start and end of every line (and a trailing CR).
 * Returns the text and `map`, where `map[i]` is the index in `text` of result character `i`
 * and `map[result.length]` is `text.length`.
 */
function trimLines(text) {
  const parts = [];
  const map = new Int32Array(text.length + 1);
  let k = 0;
  let i = 0;
  while (i <= text.length) {
    let end = text.indexOf('\n', i);
    if (end < 0) end = text.length;
    let s = i;
    let e = end;
    while (s < e && (text[s] === ' ' || text[s] === '\t')) s++;
    while (e > s && (text[e - 1] === ' ' || text[e - 1] === '\t' || text[e - 1] === '\r')) e--;
    parts.push(text.slice(s, e));
    for (let j = s; j < e; j++) map[k++] = j;
    if (end < text.length) {
      parts.push('\n');
      map[k++] = end;
    }
    i = end + 1;
  }
  map[k] = text.length;
  return [parts.join(''), map];
}

/** Line diff that ignores whitespace at the start and end of lines, in positions of the input. */
function trimmedDiff(a, b) {
  const [ta, ma] = trimLines(a);
  const [tb, mb] = trimLines(b);
  return CM.diff(ta, tb, { scanLimit: MERGE_DEFAULT_SCAN_LIMIT }).map((c) => new CM.Change(
    ma[c.fromA], c.toA > c.fromA ? ma[c.toA - 1] + 1 : ma[c.fromA],
    mb[c.fromB], c.toB > c.fromB ? mb[c.toB - 1] + 1 : mb[c.fromB],
  ));
}

const REBUILD = ['layout', 'ignoreWhitespace', 'collapse', 'context'];

/** Keep CRLF when every line break of `text` is CRLF, so the text round-trips unchanged. */
function lineEnding(text) {
  const crlfOnly = text.includes('\r\n') && !/(^|[^\r])\n/.test(text) && !/\r(?!\n)/.test(text);
  return crlfOnly ? CM.EditorState.lineSeparator.of('\r\n') : [];
}

export class Editor {
  constructor(host, hooks) {
    this.host = host;
    this.hooks = hooks;
    this.doc = null;
    this.options = null;
    this.merge = null;
    this.view = null;
    this.right = null;
    this.wrap = new CM.Compartment();
    this.theme = new CM.Compartment();
  }

  show(doc, options) {
    this.destroyViews();
    this.doc = { ...doc };
    this.options = options;
    this.build();
  }

  build() {
    const { original, modified, language, readOnly, numbersA, numbersB } = this.doc;
    const o = this.options;
    const lang = languageSupport(language);
    const dark = o.theme !== 'light';
    const common = (numbers) => [
      CM.lineNumbers(numbers ? { formatNumber: (n) => numbers[n - 1] ?? '' } : {}),
      CM.highlightSpecialChars(),
      CM.drawSelection(),
      CM.highlightActiveLine(),
      CM.highlightActiveLineGutter(),
      CM.syntaxHighlighting(highlight),
      CM.bracketMatching(),
      CM.highlightSelectionMatches(),
      CM.search({ top: true }),
      this.wrap.of(o.wrap ? CM.EditorView.lineWrapping : []),
      this.theme.of(editorTheme(dark)),
      lang,
    ];
    const viewOnly = [CM.EditorState.readOnly.of(true), CM.keymap.of([...CM.searchKeymap, ...CM.defaultKeymap])];
    const editable = [
      CM.history(),
      CM.indentOnInput(),
      CM.Prec.high(CM.keymap.of([{ key: 'Mod-s', preventDefault: true, run: () => { this.hooks.onSave(); return true; } }])),
      CM.keymap.of([...CM.searchKeymap, ...CM.defaultKeymap, ...CM.historyKeymap, CM.indentWithTab]),
      CM.EditorView.updateListener.of((u) => { if (u.docChanged) this.hooks.onChange(); }),
    ];
    const collapseUnchanged = o.collapse ? { margin: o.context } : undefined;
    const diffConfig = o.ignoreWhitespace ? { override: trimmedDiff } : undefined;
    if (o.layout === 'inline') {
      this.view = new CM.EditorView({
        parent: this.host,
        doc: modified,
        extensions: [
          lineEnding(modified),
          common(numbersB),
          readOnly ? viewOnly : editable,
          CM.unifiedMergeView({ original, mergeControls: false, highlightChanges: true, gutter: true, collapseUnchanged, diffConfig }),
        ],
      });
      this.right = this.view;
    } else {
      this.merge = new CM.MergeView({
        parent: this.host,
        a: { doc: original, extensions: [lineEnding(original), common(numbersA), viewOnly] },
        b: { doc: modified, extensions: [lineEnding(modified), common(numbersB), readOnly ? viewOnly : editable] },
        highlightChanges: true,
        gutter: true,
        collapseUnchanged,
        diffConfig,
      });
      this.right = this.merge.b;
    }
  }

  value() {
    // sliceDoc joins lines with the state's line separator; doc.toString always uses \n.
    return this.right ? this.right.state.sliceDoc() : (this.doc?.modified ?? '');
  }

  views() {
    if (this.merge) return [this.merge.a, this.merge.b];
    return this.view ? [this.view] : [];
  }

  update(options) {
    const before = this.options;
    this.options = options;
    if (!this.doc) return;
    if (REBUILD.some((k) => before[k] !== options[k])) {
      this.doc.modified = this.value();
      this.destroyViews();
      this.build();
      return;
    }
    const effects = [
      this.wrap.reconfigure(options.wrap ? CM.EditorView.lineWrapping : []),
      this.theme.reconfigure(editorTheme(options.theme !== 'light')),
    ];
    for (const view of this.views()) view.dispatch({ effects });
  }

  destroyViews() {
    this.merge?.destroy();
    this.view?.destroy();
    this.merge = null;
    this.view = null;
    this.right = null;
  }

  destroy() {
    this.destroyViews();
    this.doc = null;
  }
}
