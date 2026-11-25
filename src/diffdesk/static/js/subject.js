// Subject header, Why panel, Details table and last verdict line.
import { $, h, inlineCode, statusBadge } from './dom.js';

const KNOWN = new Set([
  'id', 'package', 'title', 'priority', 'origin', 'recommended', 'decisions', 'summary', 'why', 'refs',
  'repo', 'base', 'head', 'files', 'tests', 'senior_verdict', 'branch', 'order', 'depends_on', 'tags',
  'review', 'status', 'edited',
]);
const RECOMMENDED = { accept: 'Accept', reject: 'Reject', 'user-decision': 'Your decision' };
export const VERDICT = { accept: 'Accepted', reject: 'Rejected', change: 'Change requested' };
const VERDICT_STATUS = { accept: 'accepted', reject: 'rejected', change: 'change' };
const WHY_OPEN_KEY = 'diffdesk.whyOpen';

let whyShown = null;
let whySource = '';

export function statusOf(s) {
  return s.status || VERDICT_STATUS[s.review?.verdict] || 'pending';
}

export function editedOf(s) {
  if (typeof s.edited === 'boolean') return s.edited;
  return (s.files || []).some((f) => f && f.edited);
}

function fact(dl, label, ...value) {
  dl.append(h('dt', { text: label }), h('dd', {}, ...value));
}

function showValue(value) {
  if (value == null) return h('span', { class: 'dim', text: 'none' });
  if (typeof value === 'string') return document.createTextNode(value);
  return h('code', { class: 'json', text: JSON.stringify(value, null, 1) });
}

export function renderSubject(s) {
  $('s-id').textContent = s.id;
  $('s-title').textContent = s.title || '(no title)';
  renderStatus(s);

  const tags = [s.package, s.branch, s.priority && `priority ${s.priority}`, s.origin && `origin ${s.origin}`, ...(Array.isArray(s.tags) ? s.tags : [])];
  $('s-tags').replaceChildren(...tags.filter(Boolean).map((t) => h('span', { class: 'tag', text: String(t) })));

  const facts = $('s-facts');
  facts.replaceChildren();
  const rec = s.recommended;
  fact(facts, 'Recommended', rec ? h('span', { class: `rec rec-${rec}`, text: RECOMMENDED[rec] || rec }) : h('span', { class: 'dim', text: 'not given' }));
  fact(facts, 'Senior verdict', s.senior_verdict ? h('span', { class: `rec rec-${s.senior_verdict}`, text: String(s.senior_verdict) }) : h('span', { class: 'dim', text: 'not given' }));
  const decisions = Array.isArray(s.decisions) && s.decisions.length ? s.decisions.join(', ') : 'none';
  fact(facts, 'Decisions', decisions);
  if (Array.isArray(s.depends_on) && s.depends_on.length) fact(facts, 'Depends on', s.depends_on.join(', '));

  $('s-summary').replaceChildren(...inlineCode(s.summary || ''));

  const more = $('s-more');
  more.replaceChildren();
  if (s.repo) fact(more, 'Repository', h('code', { text: s.repo }));
  if (s.base || s.head) fact(more, 'Commits', h('code', { text: `${s.base ?? 'none'}..${s.head ?? 'none'}` }));
  if (s.tests) fact(more, 'Tests', ...inlineCode(s.tests));
  if (Array.isArray(s.refs) && s.refs.length) fact(more, 'References', s.refs.join(', '));
  if (s.order != null) fact(more, 'Order', String(s.order));
  for (const [key, value] of Object.entries(s)) {
    if (!KNOWN.has(key)) fact(more, key, showValue(value));
  }
  $('more-panel').hidden = !more.childElementCount;

  whySource = s.why || '(no explanation given)';
  whyShown = null;
  const panel = $('why-panel');
  if (panel.open) showWhy();
  else $('s-why').replaceChildren();
  renderLastVerdict(s);
}

export function renderStatus(s) {
  $('s-status').replaceChildren(statusBadge(statusOf(s)), ...(editedOf(s) ? [h('span', { class: 'badge edited', text: 'edited' })] : []));
}

export function renderLastVerdict(s) {
  const r = s.review;
  const box = $('last-verdict');
  if (!r || !r.verdict) {
    box.textContent = 'No verdict yet.';
    return;
  }
  const when = r.at ? new Date(r.at).toLocaleString() : '';
  const earlier = r.history_count > 1 ? ` (${r.history_count - 1} earlier)` : '';
  box.replaceChildren(
    'Last verdict: ', h('b', { text: VERDICT[r.verdict] || r.verdict }),
    when ? `, ${when}` : '', earlier,
    r.comment ? h('div', { class: 'comment-text', text: `Comment: ${r.comment}` }) : '',
  );
}

async function showWhy() {
  if (whyShown === whySource) return;
  const source = whySource;
  whyShown = source;
  const target = $('s-why');
  target.textContent = 'Loading...';
  try {
    const { renderMarkdown } = await import('./markdown.js');
    if (whySource !== source) return;
    await renderMarkdown(source, target);
  } catch (err) {
    if (whySource !== source) return;
    target.textContent = source;
    target.prepend(h('div', { class: 'error', text: `Cannot render Markdown: ${err.message}` }));
  }
}

export function initWhyPanel() {
  const panel = $('why-panel');
  try {
    panel.open = localStorage.getItem(WHY_OPEN_KEY) === '1';
  } catch {
    // Storage blocked: start closed.
  }
  panel.addEventListener('toggle', () => {
    try {
      localStorage.setItem(WHY_OPEN_KEY, panel.open ? '1' : '0');
    } catch {
      // Storage blocked: the choice lasts until the page closes.
    }
    if (panel.open) showWhy();
  });
}
