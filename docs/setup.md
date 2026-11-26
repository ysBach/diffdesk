# Set up diffdesk with an AI tool

This page shows how a coding agent uses diffdesk, and how to connect diffdesk to
each AI tool.

- [How agents use it](#how-agents-use-it)
- [Two ways to connect](#two-ways-to-connect)
- Tools: [Claude Code](#claude-code), [Codex CLI](#codex-cli),
  [Gemini CLI](#gemini-cli), [Cursor](#cursor), [opencode](#opencode),
  [VS Code with GitHub Copilot](#vs-code-with-github-copilot),
  [Devin Desktop](#devin-desktop-formerly-windsurf), [Grok Build](#grok-build)
- [Agents without MCP](#agents-without-mcp)

## How agents use it

Any agent that can run shell commands can use diffdesk. `diffdesk guide` prints
a short guide for agents. Tell the agent to run it, or paste its output into the
agent's instructions. Agents that support MCP can use the MCP server instead
(see [Two ways to connect](#two-ways-to-connect)).

1. The agent commits one change and runs `diffdesk add` to write a subject for it.
2. You review the subjects in the browser (`diffdesk serve`).
3. The agent runs `diffdesk reviews --status change --status rejected` to read
   your verdicts, comments and edits.
4. An edit file holds the full new content of a file, as you changed it. The
   agent compares it with the file at the subject's `head`.

If the agent rewrites a commit (rebase or amend), it runs `diffdesk add` again
with the same `--id` and `--force`, so that the subject points at the new commit.

All options of `diffdesk add`, `diffdesk reviews` and `diffdesk guide` are in
[settings.md](settings.md#commands).

## Two ways to connect

diffdesk works with a coding agent in one of two ways:

1. **Commands.** An agent that can run shell commands uses `diffdesk add`,
   `diffdesk reviews` and `diffdesk guide` (see
   [How agents use it](#how-agents-use-it)).
2. **MCP server.** `diffdesk mcp [--root PATH]` runs an MCP server on standard
   input and output (stdio). Its tools do the work of the commands:

   | Tool | Same as |
   | --- | --- |
   | `add_subject` | `diffdesk add` |
   | `list_reviews` | `diffdesk reviews` |
   | `get_agent_guide` | `diffdesk guide` |
   | `validate_subjects` | `diffdesk validate` |

The MCP server uses the review root given by `--root`, else the environment
variable `DIFFDESK_ROOT`, else the folder in which the tool starts the server.
If the tool starts it in another folder, add `"--root", "/absolute/path/to/review"`
after `"mcp"` in the arguments. The server speaks MCP revisions 2026-07-28,
2025-11-25 and 2025-06-18.

If diffdesk is not installed, use `uvx` as the command and `diffdesk mcp` as its
arguments: `"command": "uvx", "args": ["diffdesk", "mcp"]`.

Each setup below follows the documentation of the tool (read in November 2026).
The label under each tool name says whether the setup was run with diffdesk.
The test method and results are in the README, under
[Tested with](../README.md#tested-with).

## Claude Code

Label: tested 2026-11-26, Claude Code 2.1.308, claude-opus-5-5.

```bash
claude mcp add --transport stdio --scope project diffdesk -- diffdesk mcp
```

`--scope project` stores the server in `.mcp.json` in the project. You can also
write that file yourself:

```json
{
  "mcpServers": {
    "diffdesk": { "command": "diffdesk", "args": ["mcp"] }
  }
}
```

## Codex CLI

Label: tested 2026-11-25, Codex CLI 0.162.1, gpt-6-luna.

```bash
codex mcp add diffdesk -- diffdesk mcp
```

Or add this to `~/.codex/config.toml` (or `.codex/config.toml` in a trusted
project):

```toml
[mcp_servers.diffdesk]
command = "diffdesk"
args = ["mcp"]
```

## Gemini CLI

Label: set up per docs, not run.

```bash
gemini mcp add -s project diffdesk diffdesk mcp
```

Or add this to `.gemini/settings.json` in the project (or
`~/.gemini/settings.json`):

```json
{
  "mcpServers": {
    "diffdesk": { "command": "diffdesk", "args": ["mcp"] }
  }
}
```

## Cursor

Label: set up per docs, not run.

Add this to `.cursor/mcp.json` in the project (or `~/.cursor/mcp.json` for every
project). The Cursor CLI (`agent`) reads the same file.

```json
{
  "mcpServers": {
    "diffdesk": { "type": "stdio", "command": "diffdesk", "args": ["mcp"] }
  }
}
```

## opencode

Label: MCP connection tested 2026-11-25, opencode 1.19.4, model runs not completed.

```bash
opencode mcp add diffdesk -- diffdesk mcp
```

Or add this to `opencode.json` in the project (or
`~/.config/opencode/opencode.json`):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "diffdesk": { "type": "local", "command": ["diffdesk", "mcp"] }
    }
  }
}
```

This is the opencode V2 format. In V1, the server goes directly under `"mcp"`,
without `"servers"`.

## VS Code with GitHub Copilot

Label: set up per docs, not run.

Add this to `.mcp.json` at the root of the workspace (or
`~/.copilot/mcp-config.json` for every workspace):

```json
{
  "mcpServers": {
    "diffdesk": { "type": "stdio", "command": "diffdesk", "args": ["mcp"], "tools": ["*"] }
  }
}
```

For new Copilot setups, VS Code marks the older `.vscode/mcp.json` file (with
`"servers"` in place of `"mcpServers"`) as deprecated.

## Devin Desktop, formerly Windsurf

Label: set up per docs, not run.

With the Devin CLI:

```bash
devin mcp add -s project diffdesk -- diffdesk mcp
```

Or add this to `.devin/mcp_config.json` in the project (or
`~/.config/devin/mcp_config.json`):

```json
{
  "mcpServers": {
    "diffdesk": { "command": "diffdesk", "args": ["mcp"] }
  }
}
```

## Grok Build

Label: set up per docs, not run.

```bash
grok mcp add --scope project diffdesk -- diffdesk mcp
```

Or add this to `.grok/config.toml` in the project (or `~/.grok/config.toml`):

```toml
[mcp_servers.diffdesk]
command = "diffdesk"
args = ["mcp"]
```

Grok Build also reads a project `.mcp.json` in the
[Claude Code](#claude-code) format.

## Agents without MCP

Add this line to the instruction file of the agent (`AGENTS.md`, `CLAUDE.md` or
`GEMINI.md`):

```text
For every change you commit, follow the steps that `diffdesk guide` prints.
```

Codex, Cursor, opencode, VS Code with Copilot, Devin and Grok Build read
`AGENTS.md`. Claude Code reads `CLAUDE.md`. Gemini CLI reads `GEMINI.md`.
