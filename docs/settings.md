# diffdesk settings and reference

This page holds the settings and the full reference. For a short start, read the
[README](../README.md). To connect an AI tool, read [setup.md](setup.md).

- [Design](#design)
- [Review root](#review-root)
- [Subject JSON fields](#subject-json-fields)
- [Configuration](#configuration)
- [Settings](#settings)
- [Keyboard keys](#keyboard-keys)
- [Commands](#commands)
- [HTTP API (version 1)](#http-api-version-1)

## Design

A *subject* is one JSON file that an agent writes per change: a commit range
plus the reasons for the change. You can accept, reject or ask for a change, and
you can also write a comment or edit the new side of a file (the `head` side of
the diff). Verdicts and edits are plain files in the review folder, so agents
can read them back.

- Server: Python standard library only (Python 3.11 or newer), no runtime dependencies.
- Data: plain JSON files and git. No database, no background threads, no polling.
- Network: binds 127.0.0.1 by default and accepts only requests addressed to it.

## Review root

The review root is the folder that holds the review files (`review/` in the
examples).

```
review/
  diffdesk.toml          optional settings
  subjects/<package>/<ID>.json   written by agents (diffdesk add)
  reviews.json           written by diffdesk: every verdict, oldest first
  edits/<package>/<ID>/<path>    written by diffdesk: your edited new side of a file
  .diffdesk-cache/       written by diffdesk: line counts (safe to delete)
```

Commands that take a review root (`serve`, `init`, `validate`, `add`, `reviews`
and `mcp`) find it in this order: the folder on the command line (`--root PATH`
for `mcp`), the environment variable `DIFFDESK_ROOT`, the current folder.

If `review/` is inside a git repository that has at least one commit, `init`
also writes `review/subjects/example/EXAMPLE-1.json`, which shows the first
commit of that repository. Delete it when you add real subjects.

## Subject JSON fields

Only `id` and `title` are required. `subject.schema.json` (JSON Schema draft
2020-12, inside the package) describes every field. Unknown fields are kept and
shown in the "More" table.

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | Letters, digits, `.`, `_`, `-`; starts with a letter or digit; no `..`. Unique inside its package. |
| `title` | string | One-line title. |
| `package` | string | Same rules as `id`. Default: the first folder below `subjects/` (`default` for a file directly in it). |
| `priority`, `origin`, `recommended` | string | For example `P1`; `new`; `accept`, `reject` or `user-decision`. |
| `decisions`, `refs`, `tags` | list of strings | Decision IDs, supporting documents, free labels. |
| `summary` | string | One sentence: what is wrong and what the change does. |
| `why` | string | Markdown. LaTeX in `$...$` is allowed. |
| `repo` | string or null | Absolute path of the git work tree. |
| `base`, `head` | string or null | Old and new side of the diff (commit IDs, or any revision without `:`, `..` or white space). Both null: the subject has no diff. |
| `files` | list of strings | Paths relative to `repo` to review. Empty: every file changed between `base` and `head`. |
| `tests` | string | Test command and result. |
| `senior_verdict` | string | Verdict of a second reviewer. |
| `branch` | string | Branch name for the tree view. Default: the branch checked out in `repo` (`HEAD` if detached, `(no repo)` without a repo). |
| `order` | number | Position in the branch when commit order cannot decide. |
| `depends_on` | list of strings | IDs of subjects (or package names) this subject depends on. |

Order inside a branch: subjects whose `head` is a commit come first, in
`git rev-list --reverse --topo-order` order (parents before children). Then
subjects with `order`, by `order`. Then the rest. Ties use the same rule, and at
the end the ID in natural order (`SB-2` before `SB-12`).

## Configuration

`diffdesk.toml` in the review root. Every value is optional. Paths are relative
to the root and must not contain `..`. diffdesk reads the file at start; restart
after you change it.

```toml
title = "Package review"   # default: the name of the root folder
[paths]
subjects = "subjects"
reviews = "reviews.json"
edits = "edits"
cache = ".diffdesk-cache"
[packages.webapp]          # used by the flow view
depends_on = ["core", "utils"]
[ui]                       # defaults for the settings panel
theme = "system"
editor = "codemirror"
```

## Settings

Server values (fixed in the code):

| Setting | Default | Note |
| --- | --- | --- |
| Port | 8765 | arbitrary; `--port 0` picks a free port |
| Git timeout | 60 s | arbitrary |
| Gzip threshold | 1 KB | arbitrary |
| Gzip level | 6 | zlib's own default |
| Page size (`limit`) | 200 | arbitrary |
| Diff context | 3 lines | git's own default |

Settings panel (stored in the browser; defaults from `[ui]`). A value chosen in
the browser wins over `[ui]`, and `[ui]` wins over the built-in default. A `[ui]`
value of the wrong type, or not in the list of values, is ignored. "Reset to
defaults" removes the values chosen in the browser.

| `[ui]` key | Values | Default | Note |
| --- | --- | --- | --- |
| `theme` | `system`, `light`, `dark`, `contrast` | `system` | `system` follows the light or dark mode of the operating system. `contrast` is high contrast. |
| `accent` | `""` or a CSS colour, for example `"#0a66c2"` | `""` | `""` uses the accent colour of the theme. |
| `ui_font_size` | integer, 1 or more (px) | `13` | |
| `code_font` | CSS `font-family` list | `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace` | |
| `code_font_size` | integer, 1 or more (px) | `12` | |
| `editor` | `codemirror`, `monaco` | `codemirror` | CodeMirror is bundled and works offline. Monaco loads from `cdn.jsdelivr.net` when you choose it. |
| `layout` | `side-by-side`, `inline` | `side-by-side` | |
| `word_wrap` | `true`, `false` | `false` | |
| `ignore_whitespace` | `true`, `false` | `false` | Full diffs ignore spaces and tabs at the start and end of lines. Hunk mode uses `git diff -w`. |
| `collapse_unchanged` | `true`, `false` | `false` | Full diffs only. Folds unchanged lines and keeps `context_lines` lines around each change. |
| `context_lines` | integer, 0 or more | `3` | git's own default. Used in hunk mode and when unchanged lines are folded. |
| `full_diff_limit` | integer, 1 or more (lines) | `20000` | arbitrary. A file with more lines than this (base or head, whichever is longer) opens in hunk mode. "Load full file" shows the full file. |
| `reduced_motion` | `system`, `reduce`, `allow` | `system` | `system` follows the reduced-motion setting of the operating system. |

## Keyboard keys

| Key | Where | Action |
| --- | --- | --- |
| `j` / `k` | Outside text fields and editors | Open the next / previous subject in the tree |
| Up / Down | Tree | Move one row |
| Page Up / Page Down | Tree | Move one page |
| Home / End | Tree | Move to the first / last row |
| Right / Left | Tree | Expand / collapse a row. Left on a collapsed row moves to its parent. |
| Enter or Space | Tree | Open the subject or file, or expand / collapse the row |
| Cmd+S or Ctrl+S | Editor | Save the edit |
| Cmd+F or Ctrl+F | Editor | Search in the editor |
| Escape, then Tab | CodeMirror editor | Move the focus out of the editor |

## Commands

The commands are `serve`, `init`, `validate`, `add`, `reviews`, `guide` and
`mcp`. `diffdesk <command> --help` lists the options of each one. This section
describes the commands that agents use. For `mcp`, read
[setup.md](setup.md#two-ways-to-connect).

### `diffdesk add [ROOT]`

Writes `<subjects>/<package>/<ID>.json` for one commit or a commit range. The
command gets these fields from git: `repo` (absolute path of the top folder of
the work tree), `base` and `head` (full commit IDs), and `files` (every path
changed between `base` and `head`). The diff has no rename detection, so a
renamed file is listed with its old and new path, and a deleted file with its
old path. The command checks the subject against `subject.schema.json` and
writes the file atomically.

| Option | Meaning |
| --- | --- |
| `--id ID` | Required. Subject ID (same rules as the `id` field). |
| `--title TEXT` | Required. One-line title. |
| `--repo PATH` | A folder inside the git work tree. Default: the current folder. |
| `--commit REV` | Commit to review against its first parent. Default: `HEAD`. A commit without a parent is compared with the empty tree. |
| `--base REV --head REV` | A commit range, instead of `--commit`. |
| `--package NAME` | Default: the name of the work tree folder. |
| `--summary`, `--priority`, `--recommended`, `--branch` | Subject fields of the same name. |
| `--why TEXT` or `--why-file PATH` | Reasons in Markdown. `--why-file -` reads standard input (UTF-8). |
| `--tag TEXT` | Free label. Repeatable. |
| `--force` | Replace an existing subject file. |
| `--json` | Print the written subject as JSON. |

The command refuses to write if the subject file exists (without `--force`), if
another file holds a subject with the same package and ID, if the file would be
outside the review root, or if the review root does not exist.

### `diffdesk reviews [ROOT]`

Prints a JSON list with one object for each subject, in the order of the tree:

| Field | Meaning |
| --- | --- |
| `id`, `package`, `title` | The subject. |
| `status` | `pending`, `accepted`, `rejected` or `change`. |
| `verdict`, `comment`, `at` | The last review record (`at` is read from `time` in old records). Null if there is no record. |
| `history_count` | Number of review records for the subject. |
| `repo`, `head` | Work tree and new-side commit of the subject. |
| `edits` | `[{path, edit_file}]`: the file path in the repository and the absolute path of the saved edit. |

Filters: `--status STATUS` (repeatable), `--package NAME`, `--id ID`.
`--format text` prints a short table instead of JSON.

### `diffdesk guide`

Prints the guide for agents. The text is `agent_guide.md` inside the package.

### Exit codes

Exit codes of all commands: 0 on success, 1 if the command fails (the message
goes to standard error), 2 if `diffdesk.toml` or the command line is not valid.
`diffdesk validate` also gives 1 if a subject file has a problem.

## HTTP API (version 1)

All responses are JSON. A body larger than 1 KB is gzip-compressed when the
client sends `Accept-Encoding: gzip`. GET responses carry an `ETag` and
`Cache-Control: no-cache`, and a matching `If-None-Match` gives 304. Errors are
`{"error": "..."}` with status 400 (bad input), 403 (host or origin), 404, 409,
415 or 500.

A subject is named by `id`. If two packages use the same ID, add `package`
(query parameter or body field); without it the server answers 400.
`diffdesk validate` warns about such IDs.

| Request | Response |
| --- | --- |
| `GET /api/meta` | `{name, version, title, root, packages: {name: {depends_on}}, ui_defaults, counts: {total, by_status}}` |
| `GET /api/tree` | `[{package, total, reviewed, by_status, branches: [{name, repo, total, reviewed, by_status}]}]` |
| `GET /api/subjects?package=&branch=&repo=&q=&status=&offset=0&limit=200` | `{total, items: [light subject]}` |
| `GET /api/subject?id=&package=` | the subject file, plus `package`, `branch`, `status`, `edited`, `review: {verdict, comment, at, history_count}`, and `files: [{path, lines_added, lines_deleted, size_base, size_head, lines_base, lines_head, edited}]` |
| `GET /api/flow` | `{nodes: [{package, total, reviewed, by_status}], edges: [[package, dependency], ...]}` |
| `GET /api/treemap?package=` | `[{id, package, branch, title, status, lines_added, lines_deleted}]` |
| `GET /api/diff?id=&package=&path=&mode=full` | `{base, head, edit, language, binary}` |
| `GET /api/diff?id=&package=&path=&mode=hunks&context=3&ignore_ws=0` | `{hunks: [{old_start, old_lines, new_start, new_lines, lines}], binary}` |
| `POST /api/review` `{id, package?, verdict, comment?}` | the stored record plus `status` |
| `POST /api/edit` `{id, package?, path, content}` | `{id, package, path, saved}`; `content: null` removes the edit and `saved` is null |

Details:

- **Status** of a subject: the last verdict, as `accepted`, `rejected` or
  `change`, else `pending`. `by_status` always has these 4 keys. `reviewed` =
  `total` minus `pending`. `edited` is a separate flag: at least one saved edit.
- **Light subject**: `{id, package, branch, title, priority, origin, recommended,
  senior_verdict, status, edited, order, files_count, lines_added, lines_deleted}`.
  `order` is the subject's own `order` field. Line counts cover only the listed
  `files`, if the subject lists any. A subject without a diff has 0 lines. Counts
  are null if git cannot compute them.
- **Filters**: `package`, `branch` and `repo` match exactly. `status` takes one
  or more statuses separated by commas. `q` matches subjects whose ID, title,
  summary and tags contain every word of `q` (case is ignored).
- **Sizes**: `size_base` and `size_head` are bytes. `lines_base` and
  `lines_head` are line counts. All four are null where the file is absent.
- **Diff, full mode**: `base` or `head` is null where the file is absent. For a
  binary file (a NUL byte in either side) both are null and `binary` is true.
  `edit` is the saved edit or null. `language` is an editor language name from
  the file extension, or null.
- **Diff, hunks mode**: from `git diff -U<context>`. Each line starts with `+`,
  `-`, a space, or `\` (git's "No newline at end of file" note). `ignore_ws=1`
  passes `-w`.
- **Diff caching**: add `base` and `head` (the commit IDs from `/api/subject`)
  to the query to pin the request. A pinned hunks response for full commit IDs
  is sent with `Cache-Control: max-age=31536000, immutable`. Full mode is never
  immutable, because it holds the saved edit; it is revalidated with its ETag.
  If the subject now points at other commits, a pinned request gets 409.
- A subject with no diff gives 404 on `/api/diff` and 400 on `/api/edit`.
- **Review records** in `reviews.json`: `{id, package, verdict, comment, at}`,
  with `verdict` one of `accept`, `reject`, `change` and `at` an ISO 8601 time. A
  record without `package` applies to every subject with that ID.
- **Edits** are stored at `edits/<package>/<id>/<path>`. Only files of the
  subject can be edited.

### Security

The `Host` header must be `127.0.0.1:<port>`, `localhost:<port>`,
`[::1]:<port>`, or the `--host` value with the port (except `0.0.0.0` and `::`). POST requests need
`Content-Type: application/json`, an `Origin` (if sent) of the same host, and no
cross-site `Sec-Fetch-Site`. IDs, packages, revisions and paths are validated
(no `..`, no absolute paths, only listed files), git runs with a list of
arguments and no shell, and writes go only to `reviews.json` and `edits/`
(and the cache folder).
