// Monaco diff editor, loaded from the CDN only when the user chooses it.
const MONACO_BASE = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min';
// The server's language names are used as Monaco language IDs. Monaco has no TOML mode, and it
// shows an unknown ID as plain text.
const LANGUAGE = { toml: 'ini' };
const THEME = { light: 'vs', dark: 'vs-dark', contrast: 'hc-black' };

let loading = null;

/** Load Monaco from the CDN (first call only). `Editor` needs it. */
export function ready() {
  if (window.monaco?.editor) return Promise.resolve(window.monaco);
  loading ||= new Promise((resolve, reject) => {
    const fail = () => {
      loading = null;
      reject(new Error('Monaco did not load from cdn.jsdelivr.net. Check the network connection, or use CodeMirror.'));
    };
    const script = document.createElement('script');
    script.src = `${MONACO_BASE}/vs/loader.js`;
    script.onerror = () => {
      script.remove();
      fail();
    };
    script.onload = () => {
      const worker = `self.MonacoEnvironment = { baseUrl: '${MONACO_BASE}/' };`
        + `importScripts('${MONACO_BASE}/vs/base/worker/workerMain.js');`;
      window.MonacoEnvironment = {
        getWorkerUrl: () => `data:text/javascript;charset=utf-8,${encodeURIComponent(worker)}`,
      };
      window.require.config({ paths: { vs: `${MONACO_BASE}/vs` } });
      window.require(['vs/editor/editor.main'], () => resolve(window.monaco), fail);
    };
    document.head.append(script);
  });
  return loading;
}

export class Editor {
  constructor(host, hooks) {
    this.monaco = window.monaco;
    this.host = host;
    this.hooks = hooks;
    this.editor = null;
    this.models = null;
    this.listener = null;
    this.doc = null;
  }

  editorOptions(o) {
    return {
      renderSideBySide: o.layout !== 'inline',
      useInlineViewWhenSpaceIsLimited: false,
      ignoreTrimWhitespace: o.ignoreWhitespace,
      wordWrap: o.wrap ? 'on' : 'off',
      diffWordWrap: o.wrap ? 'on' : 'off',
      fontFamily: o.fontFamily,
      fontSize: o.fontSize,
      hideUnchangedRegions: { enabled: o.collapse, contextLineCount: o.context },
      smoothScrolling: !o.reducedMotion,
      originalEditable: false,
    };
  }

  show(doc, options) {
    const m = this.monaco;
    this.doc = doc;
    if (!this.editor) {
      this.editor = m.editor.createDiffEditor(this.host, {
        automaticLayout: true,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        theme: THEME[options.theme] || 'vs',
        ...this.editorOptions(options),
      });
      this.editor.getModifiedEditor().addCommand(m.KeyMod.CtrlCmd | m.KeyCode.KeyS, () => this.hooks.onSave());
    } else {
      this.update(options);
    }
    const language = LANGUAGE[doc.language] ?? doc.language ?? 'plaintext';
    const old = this.models;
    this.models = {
      original: m.editor.createModel(doc.original, language),
      modified: m.editor.createModel(doc.modified, language),
    };
    this.editor.setModel(this.models);
    this.listener?.dispose();
    this.listener = this.models.modified.onDidChangeContent(() => this.hooks.onChange());
    const numbers = (list) => ({ lineNumbers: list ? (n) => list[n - 1] ?? '' : 'on' });
    this.editor.getOriginalEditor().updateOptions(numbers(doc.numbersA));
    this.editor.getModifiedEditor().updateOptions({ ...numbers(doc.numbersB), readOnly: Boolean(doc.readOnly) });
    if (old) {
      old.original.dispose();
      old.modified.dispose();
    }
  }

  value() {
    return this.models ? this.models.modified.getValue() : (this.doc?.modified ?? '');
  }

  update(options) {
    if (!this.editor) return;
    this.editor.updateOptions(this.editorOptions(options));
    this.monaco.editor.setTheme(THEME[options.theme] || 'vs');
  }

  destroy() {
    this.listener?.dispose();
    this.editor?.dispose();
    this.models?.original.dispose();
    this.models?.modified.dispose();
    this.editor = null;
    this.models = null;
    this.listener = null;
  }
}
