# Changelog

## 0.1.0 (Unreleased)

First release.

### Added

- `diffdesk serve`, `diffdesk init` and `diffdesk validate` commands.
- `diffdesk add`, `diffdesk reviews` and `diffdesk guide` commands for coding agents.
- `diffdesk mcp`: MCP server on standard input and output with the tools
  `add_subject`, `list_reviews`, `get_agent_guide` and `validate_subjects`.
  It supports MCP revisions 2026-07-28, 2025-11-25 and 2025-06-18.
- Commands that take a review root find it in this order: the folder argument
  (`--root` for `diffdesk mcp`), the environment variable `DIFFDESK_ROOT`, the
  current folder.
- HTTP API version 1: `/api/meta`, `/api/tree`, `/api/subjects`, `/api/subject`,
  `/api/flow`, `/api/treemap`, `/api/diff`, `POST /api/review`, `POST /api/edit`.
- Subject index cached by file modification time, line counts cached in
  `<cache>/numstat.json`.
- `subject.schema.json` (JSON Schema draft 2020-12) for subject files.
- Browser app: tree, flow and treemap views, CodeMirror 6 (bundled) and Monaco
  diff editors, hunk mode for large files, and a settings panel with themes.
- Setup steps for AI coding tools in the README.
- Release to PyPI and to the MCP Registry (`server.json`) from GitHub Actions.
