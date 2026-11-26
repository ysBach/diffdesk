# diffdesk

<!-- mcp-name: io.github.ysBach/diffdesk -->

diffdesk is a local, file-based review desk for commits made by coding agents or by people.
You read the reasons for each commit next to its diff in your browser, then
accept, reject or ask for a change.

AI coding agents wrote the code and the docs. A person set the goals and made the design decisions.

## Install

```bash
uv tool install diffdesk     # install the command with uv
pipx install diffdesk        # or with pipx
uvx diffdesk serve review    # or run it without installing
```

To install the newest code from GitHub instead:

```bash
uv tool install git+https://github.com/ysBach/diffdesk
```

diffdesk needs Python 3.11 or newer. It has no runtime dependencies.

## Quick start

1. You create a review folder. The agent commits a change and writes a *subject*:
   one JSON file with the commit (or commit range) and the reasons for it.

   ```bash
   cd my-project
   diffdesk init review          # creates review/diffdesk.toml, review/subjects/, review/edits/
   diffdesk add review --id FIX-1 --title "Fix the pager"   # subject for the last commit
   diffdesk validate review      # checks every subject file
   ```

2. You review the subjects in the browser. Accept, reject or ask for a change.
   You can also write a comment or edit the new version of a file.

   ```bash
   diffdesk serve review --open  # opens http://127.0.0.1:8765/
   ```

3. The agent reads your verdicts, comments and edits back.

   ```bash
   diffdesk reviews review       # verdicts and edits as JSON
   diffdesk reviews review --status change --status rejected   # only what to fix
   ```

Verdicts and edits are plain files in the review folder. For subject fields,
settings, commands and the HTTP API, read
[docs/settings.md](https://github.com/ysBach/diffdesk/blob/main/docs/settings.md).

## Connect an AI tool

Agents that support MCP use the diffdesk MCP server (`diffdesk mcp`). Most
tools take an entry like this one:

```json
{
  "mcpServers": {
    "diffdesk": { "command": "diffdesk", "args": ["mcp"] }
  }
}
```

The file name and format differ per tool. For example, Codex CLI and Grok Build
use TOML. If diffdesk is not installed, use
`"command": "uvx", "args": ["diffdesk", "mcp"]`.

For an agent without MCP, add this line to its instruction file (`AGENTS.md`,
`CLAUDE.md` or `GEMINI.md`):

```text
For every change you commit, follow the steps that `diffdesk guide` prints.
```

Setup for each tool:

- [Claude Code](https://github.com/ysBach/diffdesk/blob/main/docs/setup.md#claude-code): tested 2026-11-26
- [Codex CLI](https://github.com/ysBach/diffdesk/blob/main/docs/setup.md#codex-cli): tested 2026-11-25
- [Gemini CLI](https://github.com/ysBach/diffdesk/blob/main/docs/setup.md#gemini-cli): set up per docs, not run
- [Cursor](https://github.com/ysBach/diffdesk/blob/main/docs/setup.md#cursor): set up per docs, not run
- [opencode](https://github.com/ysBach/diffdesk/blob/main/docs/setup.md#opencode): MCP connection tested 2026-11-25, model runs not completed
- [VS Code with GitHub Copilot](https://github.com/ysBach/diffdesk/blob/main/docs/setup.md#vs-code-with-github-copilot): set up per docs, not run
- [Devin Desktop, formerly Windsurf](https://github.com/ysBach/diffdesk/blob/main/docs/setup.md#devin-desktop-formerly-windsurf): set up per docs, not run
- [Grok Build](https://github.com/ysBach/diffdesk/blob/main/docs/setup.md#grok-build): set up per docs, not run

## Tested with

The tests on 2026-11-25 and 2026-11-26 used a small git repository with one
bug and one failing test. Round 1: the agent fixed the bug, made a commit and
wrote a subject.
Then a person asked for a change, with a comment and an edit of the fixed file.
Round 2: the agent read the review, made a new commit and wrote a new subject.

| Tool | Version | Model | MCP result | CLI result |
| --- | --- | --- | --- | --- |
| Claude Code | 2.1.308 | claude-opus-5-5 | Round 1 and round 2 passed (MCP revision 2026-07-28). | Round 1 passed (`diffdesk add`). |
| Codex CLI | 0.162.1 | gpt-6-luna | Round 1 and round 2 passed (MCP revision 2025-06-18). | Round 1 passed (`diffdesk guide`, then `diffdesk add`). |
| A Gemini model through shell commands (not Gemini CLI) | - | gemini-3.8-flash | Not run. | Round 1 and round 2 passed. |
| opencode | 1.19.4 | none | The server connected (MCP revision 2025-11-25). Model runs did not finish because of model service errors. | Not run. |

## License

MIT AND OFL-1.1: diffdesk is MIT (`LICENSE`), and the bundled KaTeX fonts are SIL Open Font License 1.1 (`src/diffdesk/static/vendor/fonts/OFL.txt`).
The bundled CodeMirror, marked and KaTeX code keep their own license texts in `src/diffdesk/static/vendor/LICENSES.txt`.
