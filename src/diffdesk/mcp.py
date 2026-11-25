"""MCP server on standard input and output (``diffdesk mcp``).

The server supports both eras of the Model Context Protocol:

- Revision 2026-07-28: every request carries its protocol version in
  ``params._meta`` and is answered without session state. ``server/discover``
  lists the supported versions.
- Revisions 2025-11-25 and 2025-06-18: the client opens with ``initialize``,
  and the negotiated version holds for the life of the process.

Each message is one line of UTF-8 JSON. Standard output carries protocol
messages only; logs go to standard error. The server exits at end of input.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import __version__
from .agent import GUIDE_PATH, collect_reviews, create_subject, schema_errors, validate_subjects
from .config import load_config
from .gitio import GitError, InvalidInput
from .index import STATUSES

log = logging.getLogger(__name__)

MODERN_VERSIONS = ("2026-07-28",)
# Newest first: an ``initialize`` that asks for another version gets the first.
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18")

META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER = "io.modelcontextprotocol/serverInfo"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNSUPPORTED_VERSION = -32022

SERVER_INFO = {"name": "diffdesk", "version": __version__}
# Caching hints of 2026-07-28 for ``server/discover`` and ``tools/list``. The
# answers change only when diffdesk is upgraded, and any positive time-to-live
# would be an arbitrary number, so the server makes no freshness claim.
CACHE_HINTS = {"ttlMs": 0, "cacheScope": "public"}

INSTRUCTIONS = (
    "diffdesk is a review desk for commits: you write one subject per commit, "
    "a person reviews it, and you read the verdicts back. Call get_agent_guide "
    "once before your first add_subject call. After each commit, call add_subject. "
    "Later, call list_reviews to read the verdicts, comments and edits. For each "
    "subject with status change or rejected, do what the comment and the edits ask, "
    "make a new commit and call add_subject for it with a new id. Name the old "
    "subject id in why."
)

_TEXT = {"type": "string", "minLength": 1}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "add_subject",
        "title": "Add a review subject",
        "description": (
            "Write a review subject for one git commit or a commit range, so that a "
            "person can review the change in diffdesk. Make the commit first. diffdesk "
            "reads the full commit IDs, the work tree path and the changed files from "
            "git, checks the subject against the schema and writes "
            "<review root>/subjects/<package>/<id>.json. Returns the file path and the "
            "subject. By default the diff is HEAD^..HEAD. An existing subject is not "
            "replaced unless force is true."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {
                    **_TEXT,
                    "description": (
                        "Absolute path of a folder inside the git work tree that holds the commit."
                    ),
                },
                "id": {
                    **_TEXT,
                    "description": (
                        "Subject ID, unique inside its package. Letters, digits, '.', "
                        "'_' and '-'; starts with a letter or digit; no '..'. "
                        "Example: FIX-12."
                    ),
                },
                "title": {**_TEXT, "description": "One-line title of the change."},
                "commit": {
                    **_TEXT,
                    "description": (
                        "Commit to review against its first parent. Default: HEAD. "
                        "Do not give it together with base and head."
                    ),
                },
                "base": {**_TEXT, "description": "Old side of a commit range. Give head too."},
                "head": {**_TEXT, "description": "New side of a commit range. Give base too."},
                "package": {
                    **_TEXT,
                    "description": (
                        "Package name, same rules as id. Default: the name of the work "
                        "tree folder."
                    ),
                },
                "summary": {
                    **_TEXT,
                    "description": "One sentence: what is wrong and what the change does.",
                },
                "why": {
                    **_TEXT,
                    "description": (
                        "Reasons for the change, in Markdown, with the headings that "
                        "get_agent_guide gives: Problem, Evidence, Fix logic, Test, "
                        "Tradeoffs."
                    ),
                },
                "priority": {**_TEXT, "description": "For example P1, P2 or P3."},
                "recommended": {
                    **_TEXT,
                    "description": "Suggested verdict: accept, reject or user-decision.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Free labels.",
                },
                "branch": {
                    **_TEXT,
                    "description": "Branch name for the review tree. Default: none.",
                },
                "force": {
                    "type": "boolean",
                    "description": (
                        "Replace an existing subject with the same package and ID, for "
                        "example after a rebase or an amend. Default: false."
                    ),
                },
            },
            "required": ["repo", "id", "title"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "openWorldHint": False},
    },
    {
        "name": "list_reviews",
        "title": "List reviews and edits",
        "description": (
            "Read the review state of the subjects, in the order of the review tree. "
            "Each item has id, package, title, status (pending, accepted, rejected or "
            "change), the last verdict, comment and time, history_count, repo, head, "
            "and edits: [{path, edit_file}]. An edit file holds the full new content of "
            "one file as the person changed it, not a patch: compare it with that file "
            "at the subject's head commit. To find the work to do, ask for the "
            "statuses change and rejected. For each request, do what the comment and "
            "the edits ask, make a new commit and call add_subject for it with a new "
            "id. Name the old subject id in why."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "statuses": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(STATUSES)},
                    "description": "Keep only subjects with one of these statuses. Default: all.",
                },
                "package": {**_TEXT, "description": "Keep only subjects of this package."},
                "id": {**_TEXT, "description": "Keep only subjects with this ID."},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_agent_guide",
        "title": "Get the agent guide",
        "description": (
            "Return the diffdesk guide for coding agents, in Markdown: the steps, how to "
            "write 'why', the fields of each review and the subject fields. Read it once "
            "before the first add_subject call. The guide shows shell commands; these "
            "tools do the same work: 'diffdesk add' is add_subject, 'diffdesk reviews' "
            "is list_reviews and 'diffdesk validate' is validate_subjects."
        ),
        "inputSchema": {"type": "object", "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "validate_subjects",
        "title": "Validate all subjects",
        "description": (
            "Check every subject file of the review root against the subject schema, "
            "and check that no two files hold the same package and ID. Returns root, "
            "files (the number of subject files), problems and warnings. Each problem "
            "starts with the file path relative to the root. Problems do not make "
            "this call fail."
        ),
        "inputSchema": {"type": "object", "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
]
TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}
MODERN_METHODS = ("server/discover", "tools/list", "tools/call")


def _add_subject(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    path, subject = create_subject(
        load_config(root),
        repo=args["repo"],
        subject_id=args["id"],
        title=args["title"],
        commit=args.get("commit"),
        base=args.get("base"),
        head=args.get("head"),
        package=args.get("package"),
        summary=args.get("summary"),
        why=args.get("why"),
        priority=args.get("priority"),
        recommended=args.get("recommended"),
        tags=args.get("tags"),
        branch=args.get("branch"),
        force=args.get("force", False),
    )
    return {"path": str(path), "subject": subject}


def _list_reviews(root: Path, args: dict[str, Any]) -> list[dict[str, Any]]:
    return collect_reviews(
        load_config(root),
        statuses=args.get("statuses"),
        package=args.get("package"),
        subject_id=args.get("id"),
    )


def _validate_subjects(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    config = load_config(root)
    return {"root": str(config.root), **validate_subjects(config)}


HANDLERS: dict[str, Callable[[Path, dict[str, Any]], Any]] = {
    "add_subject": _add_subject,
    "list_reviews": _list_reviews,
    "get_agent_guide": lambda root, args: GUIDE_PATH.read_text(encoding="utf-8"),
    "validate_subjects": _validate_subjects,
}


class ProtocolError(Exception):
    """A request that gets a JSON-RPC error response instead of a result."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _error(msg_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": error}


class McpServer:
    """Answer MCP messages for one review root.

    Parameters
    ----------
    root : Path
        The review root. Its ``diffdesk.toml`` is read again on each tool
        call, so a bad file makes that call fail, not the server.

    Attributes
    ----------
    legacy_version : str or None
        Version negotiated by ``initialize``, or `None` before it.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.legacy_version: str | None = None

    def handle(self, message: Any) -> dict[str, Any] | None:
        """Return the response to one decoded message.

        Parameters
        ----------
        message : Any
            One decoded JSON value from the client.

        Returns
        -------
        dict or None
            The response, or `None` for a notification or a response from
            the client, which get no reply.
        """
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "A message must be one JSON-RPC object")
        msg_id = message.get("id")
        has_id = "id" in message
        if "method" not in message and ("result" in message or "error" in message):
            log.debug("ignored a response from the client (id %r)", msg_id)
            return None
        method = message.get("method")
        if not has_id and isinstance(method, str):
            log.debug("notification %s", method)
            return None
        valid_id = isinstance(msg_id, str | int) and not isinstance(msg_id, bool)
        if message.get("jsonrpc") != "2.0" or not isinstance(method, str) or not valid_id:
            return _error(
                msg_id if valid_id else None,
                INVALID_REQUEST,
                "Invalid request: needs jsonrpc '2.0', a string method and a string or integer id",
            )
        params = message.get("params", {})
        if not isinstance(params, dict):
            return _error(msg_id, INVALID_PARAMS, "params must be an object")
        log.debug("request %s (id %r)", method, msg_id)
        try:
            result = self.dispatch(method, params)
        except ProtocolError as exc:
            return _error(msg_id, exc.code, exc.message, exc.data)
        except Exception:
            log.exception("request %s failed", method)
            return _error(
                msg_id, INTERNAL_ERROR, f"Internal error in {method}; see the server log"
            )
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Return the result of one request.

        A request whose ``_meta`` holds a protocol version follows revision
        2026-07-28. Other requests follow the version negotiated by
        ``initialize``.

        Raises
        ------
        ProtocolError
            If the request gets a JSON-RPC error.
        """
        meta = params.get("_meta")
        if isinstance(meta, dict) and META_VERSION in meta:
            return self._modern(method, params, meta)
        if method == "initialize":
            return self._initialize(params)
        if method == "ping":
            return {}
        if self.legacy_version is None:
            if method in MODERN_METHODS:
                raise ProtocolError(
                    INVALID_PARAMS,
                    f"Missing _meta field '{META_VERSION}'. Send 'initialize' first "
                    f"(protocol {' or '.join(LEGACY_VERSIONS)}), or give the request _meta "
                    f"of protocol {' or '.join(MODERN_VERSIONS)}.",
                )
            raise ProtocolError(METHOD_NOT_FOUND, f"Method not found: {method}")
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            return self._call(params)
        raise ProtocolError(METHOD_NOT_FOUND, f"Method not found: {method}")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        if not isinstance(requested, str):
            raise ProtocolError(INVALID_PARAMS, "initialize needs 'protocolVersion' (a string)")
        # 2025-11-25 lifecycle: answer with the requested version if supported,
        # else with another supported version, which SHOULD be the latest.
        self.legacy_version = requested if requested in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        log.info("initialize: client asked for %s, using %s", requested, self.legacy_version)
        return {
            "protocolVersion": self.legacy_version,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        }

    def _modern(self, method: str, params: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
        version = meta.get(META_VERSION)
        if not isinstance(version, str) or not isinstance(meta.get(META_CAPABILITIES), dict):
            raise ProtocolError(
                INVALID_PARAMS,
                f"_meta needs '{META_VERSION}' (a string) and '{META_CAPABILITIES}' (an object)",
            )
        if version not in MODERN_VERSIONS:
            raise ProtocolError(
                UNSUPPORTED_VERSION,
                "Unsupported protocol version",
                {"supported": list(MODERN_VERSIONS), "requested": version},
            )
        if method == "server/discover":
            result = {
                "supportedVersions": list(MODERN_VERSIONS),
                "capabilities": {"tools": {}},
                "instructions": INSTRUCTIONS,
                **CACHE_HINTS,
            }
        elif method == "tools/list":
            result = {"tools": TOOLS, **CACHE_HINTS}
        elif method == "tools/call":
            result = self._call(params)
        else:
            raise ProtocolError(METHOD_NOT_FOUND, f"Method not found: {method}")
        return {"resultType": "complete", **result, "_meta": {META_SERVER: SERVER_INFO}}

    def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        args = params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(args, dict):
            raise ProtocolError(
                INVALID_PARAMS, "tools/call needs 'name' (a string) and 'arguments' (an object)"
            )
        tool = TOOLS_BY_NAME.get(name)
        if tool is None:
            raise ProtocolError(INVALID_PARAMS, f"Unknown tool: {name}")
        schema = tool["inputSchema"]
        errors = schema_errors(args, schema, schema, "arguments")
        if errors:
            return _tool_result("; ".join(errors), is_error=True)
        try:
            value = HANDLERS[name](self.root, args)
        except (InvalidInput, GitError, OSError, ValueError) as exc:
            log.info("tool %s failed: %s", name, exc)
            return _tool_result(str(exc), is_error=True)
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return _tool_result(text, is_error=False)


def _tool_result(text: str, *, is_error: bool) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def serve_stdio(root: Path) -> int:
    """Answer MCP messages on standard input until it ends.

    Parameters
    ----------
    root : Path
        The review root.

    Returns
    -------
    int
        Exit code 0.
    """
    root = root.resolve()
    server = McpServer(root)
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    log.info("diffdesk %s MCP server on stdio (root: %s)", __version__, root)
    for line in iter(stdin.readline, b""):
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except (ValueError, RecursionError) as exc:
            reply = _error(None, PARSE_ERROR, f"Parse error: {exc}")
        else:
            reply = server.handle(message)
        if reply is not None:
            # ASCII JSON without indentation has no line breaks inside.
            stdout.write(json.dumps(reply).encode("ascii") + b"\n")
            stdout.flush()
    log.info("end of input; MCP server stops")
    return 0
