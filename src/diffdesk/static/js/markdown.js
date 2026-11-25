// Markdown with $...$ and $$...$$ math. marked loads on first use, KaTeX only when math is present.
import { escapeHtml } from './dom.js';

let markedReady = null;
let katexReady = null;

function loadMarked() {
  markedReady ||= import('../vendor/marked.js').then(({ marked }) => {
    marked.use({
      gfm: true,
      renderer: {
        // Raw HTML in the source is shown as text, never run.
        html: (token) => escapeHtml(token.text),
      },
      walkTokens(token) {
        if ((token.type === 'link' || token.type === 'image') && /^\s*(javascript|vbscript|data):/i.test(token.href)) {
          token.href = '#';
        }
      },
    });
    return marked;
  });
  return markedReady;
}

function loadKatex() {
  katexReady ||= new Promise((resolve, reject) => {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = 'vendor/katex.css';
    link.onload = resolve;
    link.onerror = () => reject(new Error('Cannot load vendor/katex.css'));
    document.head.append(link);
  }).then(() => import('../vendor/katex.js')).then((m) => m.default);
  return katexReady;
}

// Inline code spans are matched first so that a `$` inside them is not math.
const INLINE_TOKEN = new RegExp([
  '(`+)[\\s\\S]*?\\1(?!`)',
  '\\\\\\$',
  '\\$\\$[\\s\\S]+?\\$\\$',
  '\\$(?![\\s$])(?:\\\\.|[^\\\\$\\n]|\\n(?!\\n))*?(?<![\\s\\\\])\\$(?!\\d)',
].join('|'), 'g');

function protectInline(text, store) {
  return text.replace(INLINE_TOKEN, (match) => {
    if (match[0] !== '$') return match;
    store.push(match);
    return `MATHPLACEHOLDER${store.length - 1}END`;
  });
}

/** Replace math outside fenced code blocks with placeholders; push the math source to `store`. */
function protectMath(src, store) {
  const out = [];
  let buffer = [];
  let fence = null;
  const flush = () => {
    if (buffer.length) out.push(protectInline(buffer.join('\n'), store));
    buffer = [];
  };
  for (const line of src.split('\n')) {
    const open = /^ {0,3}(`{3,}|~{3,})/.exec(line);
    if (fence === null && open) {
      flush();
      fence = open[1];
      out.push(line);
    } else if (fence !== null) {
      out.push(line);
      const close = /^ {0,3}(`{3,}|~{3,})\s*$/.exec(line);
      if (close && close[1][0] === fence[0] && close[1].length >= fence.length) fence = null;
    } else {
      buffer.push(line);
    }
  }
  flush();
  return out.join('\n');
}

/** Render Markdown `src` into `target`. */
export async function renderMarkdown(src, target) {
  const marked = await loadMarked();
  const store = [];
  const html = marked.parse(protectMath(src, store)).replace(/MATHPLACEHOLDER(\d+)END/g, (_, i) => (
    `<span class="math-src" data-i="${i}">${escapeHtml(store[Number(i)])}</span>`
  ));
  target.innerHTML = html;
  if (!store.length) return;
  let katex;
  try {
    katex = await loadKatex();
  } catch {
    return;
  }
  for (const span of target.querySelectorAll('.math-src')) {
    const raw = store[Number(span.dataset.i)];
    const display = raw.startsWith('$$');
    const tex = display ? raw.slice(2, -2) : raw.slice(1, -1);
    katex.render(tex, span, { displayMode: display, throwOnError: false });
  }
}
