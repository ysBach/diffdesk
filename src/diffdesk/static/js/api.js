// Client for the diffdesk HTTP API v1.

export class ApiError extends Error {}

function query(params) {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value != null && value !== '') q.set(key, String(value));
  }
  const s = q.toString();
  return s ? `?${s}` : '';
}

async function request(url, options = {}) {
  let res;
  try {
    res = await fetch(url, options);
  } catch (err) {
    if (err.name === 'AbortError') throw err;
    throw new ApiError('The server does not answer. Is diffdesk still running?');
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(data?.error || `The server answered HTTP ${res.status}.`);
  return data;
}

// Lists and counts change on disk, so the browser must ask the server each time.
const fresh = { cache: 'no-cache' };

function post(url, body) {
  return request(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export const api = {
  meta: () => request('/api/meta', fresh),
  tree: () => request('/api/tree', fresh),
  subjects: (params, signal) => request(`/api/subjects${query(params)}`, { ...fresh, signal }),
  // `package` tells apart subjects that share an ID in different packages.
  subject: (id, pkg) => request(`/api/subject${query({ id, package: pkg })}`, fresh),
  flow: () => request('/api/flow', fresh),
  treemap: (pkg) => request(`/api/treemap${query({ package: pkg })}`, fresh),
  /**
   * Full mode carries the saved edit, which changes, so it is revalidated.
   * Hunk mode with `base` and `head` given depends only on the commits; the server marks it immutable.
   */
  diff: ({ id, pkg, path, mode, context, ignoreWhitespace, base, head }) => request(
    `/api/diff${query(mode === 'hunks'
      ? { id, package: pkg, path, mode, context, ignore_ws: ignoreWhitespace ? 1 : null, base, head }
      : { id, package: pkg, path, mode })}`,
    mode === 'hunks' ? {} : fresh,
  ),
  review: (body) => post('/api/review', body),
  edit: (body) => post('/api/edit', body),
};
