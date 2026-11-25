/**
 * Turn `git diff` hunks into two read-only excerpts that a diff editor can show side by side.
 *
 * Each hunk starts with its `@@ -a,b +c,d @@` header on both sides, so the headers align.
 * `numbersA[k]` / `numbersB[k]` is the real line number of excerpt line `k + 1` ('' for headers).
 *
 * @param {{old_start: number, old_lines: number, new_start: number, new_lines: number, lines: string[]}[]} hunks
 */
export function hunkExcerpt(hunks) {
  const a = [];
  const b = [];
  const numbersA = [];
  const numbersB = [];
  for (const hunk of hunks) {
    const header = `@@ -${hunk.old_start},${hunk.old_lines} +${hunk.new_start},${hunk.new_lines} @@`;
    a.push(header);
    b.push(header);
    numbersA.push('');
    numbersB.push('');
    let lineA = hunk.old_start;
    let lineB = hunk.new_start;
    for (const line of hunk.lines) {
      const kind = line[0];
      const text = line.slice(1);
      if (kind === ' ') {
        a.push(text);
        b.push(text);
        numbersA.push(String(lineA++));
        numbersB.push(String(lineB++));
      } else if (kind === '-') {
        a.push(text);
        numbersA.push(String(lineA++));
      } else if (kind === '+') {
        b.push(text);
        numbersB.push(String(lineB++));
      }
    }
  }
  return { original: a.join('\n'), modified: b.join('\n'), numbersA, numbersB };
}
