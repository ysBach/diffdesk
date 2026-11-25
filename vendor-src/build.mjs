// Rebuild src/diffdesk/static/vendor/ from the exact npm versions in package.json.
//
//   cd vendor-src
//   npm ci
//   node build.mjs            # writes ../src/diffdesk/static/vendor/
//   node build.mjs --out DIR  # writes DIR instead
//
// Output: codemirror.js, marked.js, katex.js (minified ES modules), katex.css with
// woff2 fonts only (fonts/), and LICENSES.txt with the license text of every bundled package.
import { build } from 'esbuild';
import { copyFile, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';

const here = path.dirname(fileURLToPath(import.meta.url));
const outFlag = process.argv.indexOf('--out');
const out = outFlag > 0
  ? path.resolve(process.argv[outFlag + 1])
  : path.resolve(here, '../src/diffdesk/static/vendor');
const nodeModules = path.join(here, 'node_modules');

const CODEMIRROR_ENTRY = `
export { EditorState, EditorSelection, Compartment, Prec } from '@codemirror/state';
export {
  EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter,
  drawSelection, highlightSpecialChars,
} from '@codemirror/view';
export {
  syntaxHighlighting, defaultHighlightStyle, HighlightStyle, StreamLanguage,
  bracketMatching, indentOnInput, indentUnit, LanguageSupport,
} from '@codemirror/language';
export { tags } from '@lezer/highlight';
export { history, defaultKeymap, historyKeymap, indentWithTab } from '@codemirror/commands';
export { search, searchKeymap, highlightSelectionMatches } from '@codemirror/search';
export {
  MergeView, unifiedMergeView, diff, Change, goToNextChunk, goToPreviousChunk,
} from '@codemirror/merge';
export { python } from '@codemirror/lang-python';
export { rust } from '@codemirror/lang-rust';
export { markdownLanguage } from '@codemirror/lang-markdown';
export { toml } from '@codemirror/legacy-modes/mode/toml';
export { yaml } from '@codemirror/legacy-modes/mode/yaml';
`;

async function readPackage(dir) {
  return JSON.parse(await readFile(path.join(dir, 'package.json'), 'utf8'));
}

function packageDirOf(file) {
  const rel = path.relative(nodeModules, path.resolve(here, file));
  const parts = rel.split(path.sep);
  if (parts[0] === '..') return null;
  const n = parts[0].startsWith('@') ? 2 : 1;
  return path.join(nodeModules, ...parts.slice(0, n));
}

async function banner(names) {
  const lines = [];
  for (const name of names) {
    const pkg = await readPackage(path.join(nodeModules, name));
    lines.push(`${name} ${pkg.version}`);
  }
  return `/* Bundled for diffdesk from ${lines.join(', ')}. MIT license, see LICENSES.txt. */`;
}

async function bundle({ entry, stdin, outfile, names }) {
  const result = await build({
    entryPoints: entry ? [entry] : undefined,
    stdin: stdin ? { contents: stdin, resolveDir: here, loader: 'js' } : undefined,
    outfile: path.join(out, outfile),
    absWorkingDir: here,
    bundle: true,
    format: 'esm',
    minify: true,
    target: 'es2020',
    legalComments: 'none',
    metafile: true,
    banner: { js: await banner(names) },
    logLevel: 'warning',
  });
  const [output] = Object.values(result.metafile.outputs);
  return Object.entries(output.inputs).filter(([, v]) => v.bytesInOutput > 0).map(([file]) => file);
}

async function katexCss() {
  const dist = path.join(nodeModules, 'katex', 'dist');
  const css = await readFile(path.join(dist, 'katex.min.css'), 'utf8');
  const woff2Only = css.replace(/,url\(fonts\/[^)]+\.(?:woff|ttf)\) format\("(?:woff|truetype)"\)/g, '');
  if (/\.(?:woff|ttf)\)/.test(woff2Only)) throw new Error('katex.min.css has a font url that is not woff2');
  const fonts = [...new Set([...woff2Only.matchAll(/url\(fonts\/([^)]+\.woff2)\)/g)].map((m) => m[1]))];
  await mkdir(path.join(out, 'fonts'), { recursive: true });
  for (const font of fonts) await copyFile(path.join(dist, 'fonts', font), path.join(out, 'fonts', font));
  const head = await banner(['katex']);
  await writeFile(path.join(out, 'katex.css'), `${head}\n${woff2Only}`);
  return fonts.length;
}

async function licenses(inputs) {
  const dirs = new Set(inputs.map(packageDirOf).filter(Boolean));
  dirs.add(path.join(nodeModules, 'katex'));
  const parts = [
    'Third-party code bundled in this folder by vendor-src/build.mjs.',
    'Every package below is under the MIT license. Each section is the package\'s own LICENSE file.',
  ];
  for (const dir of [...dirs].sort()) {
    const pkg = await readPackage(dir);
    if (pkg.license !== 'MIT') throw new Error(`${pkg.name} is not MIT: ${pkg.license}`);
    const file = (await readdir(dir)).find((f) => /^licen[cs]e/i.test(f));
    if (!file) throw new Error(`${pkg.name} has no LICENSE file`);
    const text = (await readFile(path.join(dir, file), 'utf8')).trim();
    parts.push(`\n==== ${pkg.name} ${pkg.version} (${pkg.license}) ====\n\n${text}`);
  }
  await writeFile(path.join(out, 'LICENSES.txt'), parts.join('\n') + '\n');
  return dirs.size;
}

async function report() {
  const rows = [];
  async function walk(dir) {
    for (const entry of await readdir(dir, { withFileTypes: true })) {
      const file = path.join(dir, entry.name);
      if (entry.isDirectory()) await walk(file);
      else {
        const data = await readFile(file);
        rows.push([path.relative(out, file), data.length, gzipSync(data, { level: 9 }).length]);
      }
    }
  }
  await walk(out);
  rows.sort((a, b) => a[0].localeCompare(b[0]));
  const fonts = rows.filter((r) => r[0].startsWith('fonts'));
  const shown = rows.filter((r) => !r[0].startsWith('fonts'));
  shown.push([`fonts/ (${fonts.length} woff2)`, fonts.reduce((s, r) => s + r[1], 0), fonts.reduce((s, r) => s + r[2], 0)]);
  for (const [name, raw, gz] of shown) {
    console.log(`${name.padEnd(28)} ${String(raw).padStart(9)} B raw  ${String(gz).padStart(9)} B gzip`);
  }
}

await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });
const cmInputs = await bundle({
  stdin: CODEMIRROR_ENTRY,
  outfile: 'codemirror.js',
  names: [
    '@codemirror/state', '@codemirror/view', '@codemirror/language', '@codemirror/commands',
    '@codemirror/search', '@codemirror/merge', '@codemirror/lang-python', '@codemirror/lang-rust',
    '@codemirror/lang-markdown', '@codemirror/legacy-modes', '@lezer/highlight',
  ],
});
const markedInputs = await bundle({
  entry: path.join(nodeModules, 'marked', 'lib', 'marked.esm.js'), outfile: 'marked.js', names: ['marked'],
});
const katexInputs = await bundle({
  entry: path.join(nodeModules, 'katex', 'dist', 'katex.mjs'), outfile: 'katex.js', names: ['katex'],
});
const fontCount = await katexCss();
const packageCount = await licenses([...cmInputs, ...markedInputs, ...katexInputs]);
console.log(`Wrote ${out}: ${packageCount} packages, ${fontCount} fonts.`);
await report();
