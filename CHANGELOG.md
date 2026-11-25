# Changelog

## Unreleased

### Added

- `diffdesk serve`, `diffdesk init` and `diffdesk validate` commands.
- HTTP API version 1: `/api/meta`, `/api/tree`, `/api/subjects`, `/api/subject`,
  `/api/flow`, `/api/treemap`, `/api/diff`, `POST /api/review`, `POST /api/edit`.
- Subject index cached by file modification time, line counts cached in
  `<cache>/numstat.json`.
- `subject.schema.json` (JSON Schema draft 2020-12) for subject files.
- Browser app: tree, flow and treemap views, CodeMirror 6 (bundled) and Monaco
  diff editors, hunk mode for large files, and a settings panel with themes.
