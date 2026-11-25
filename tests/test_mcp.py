"""MCP server: run ``diffdesk mcp`` and talk to it over standard input and output."""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import sysconfig
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import Repo

from diffdesk.agent import GUIDE_PATH

MODERN = "2026-07-28"
META = {
    "io.modelcontextprotocol/protocolVersion": MODERN,
    "io.modelcontextprotocol/clientInfo": {"name": "test-client", "version": "1.0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}
TOOL_NAMES = ["add_subject", "list_reviews", "get_agent_guide", "validate_subjects"]
# Arbitrary limit, so that a server that does not answer fails the test
# instead of blocking it.
TIMEOUT_S = 30


class Mcp:
    """A running ``diffdesk mcp`` process with its pipes."""

    def __init__(self, args: list[str], cwd: Path | None, stderr: Path) -> None:
        exe = shutil.which("diffdesk", path=sysconfig.get_path("scripts"))
        assert exe is not None, "the diffdesk command is not installed; run 'uv sync'"
        self.stderr_path = stderr
        self._stderr = stderr.open("wb")
        self.proc = subprocess.Popen(
            [exe, "mcp", *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            cwd=cwd,
        )
        self.lines: queue.Queue[bytes] = queue.Queue()
        self.last_id = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in iter(self.proc.stdout.readline, b""):
            self.lines.put(line)
        self.lines.put(b"")

    def send_raw(self, data: bytes) -> None:
        """Write bytes to the standard input of the server."""
        assert self.proc.stdin is not None
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send(self, message: Any) -> None:
        """Write one message as one line."""
        self.send_raw(json.dumps(message).encode() + b"\n")

    def receive(self) -> Any:
        """Read one line from the standard output of the server."""
        line = self.lines.get(timeout=TIMEOUT_S)
        assert line, "the server closed its standard output"
        assert line.endswith(b"\n")
        return json.loads(line)

    def request(
        self, method: str, params: dict[str, Any] | None = None, meta: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send one request and return the response, which must carry its ID."""
        self.last_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": self.last_id, "method": method}
        params = dict(params or {})
        if meta is not None:
            params["_meta"] = meta
        if params:
            message["params"] = params
        self.send(message)
        reply = self.receive()
        assert reply["jsonrpc"] == "2.0" and reply["id"] == self.last_id
        return reply

    def call(
        self, name: str, arguments: dict[str, Any], meta: dict[str, Any] | None = META
    ) -> dict[str, Any]:
        """Call a tool and return the result."""
        return self.request("tools/call", {"name": name, "arguments": arguments}, meta)["result"]

    def initialize(self, version: str = "2025-11-25") -> dict[str, Any]:
        """Run the handshake of the older protocol versions and return its result."""
        params = {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        }
        result = self.request("initialize", params)["result"]
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def close(self) -> int:
        """Close standard input and return the exit code."""
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        code = self.proc.wait(timeout=TIMEOUT_S)
        assert self.lines.get(timeout=TIMEOUT_S) == b"", "unread output"
        return code


@pytest.fixture
def start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., Mcp]]:
    """Start ``diffdesk mcp`` with the given arguments; stop it after the test."""
    monkeypatch.delenv("DIFFDESK_ROOT", raising=False)
    started: list[Mcp] = []

    def _start(*args: str, cwd: Path | None = None) -> Mcp:
        server = Mcp(list(args), cwd, tmp_path / f"mcp-stderr-{len(started)}.log")
        started.append(server)
        return server

    yield _start
    for server in started:
        if server.proc.poll() is None:
            server.proc.kill()
            server.proc.wait(timeout=TIMEOUT_S)
        server._stderr.close()


def text_of(result: dict[str, Any]) -> str:
    """The text of a tool result with one text block."""
    [block] = result["content"]
    assert block["type"] == "text"
    return block["text"]


def test_modern_discover_and_tools_list(start: Callable[..., Mcp], root: Path) -> None:
    server = start("--root", str(root))
    discover = server.request("server/discover", meta=META)["result"]
    assert discover["resultType"] == "complete"
    assert discover["supportedVersions"] == [MODERN]
    assert discover["capabilities"] == {"tools": {}}
    assert "get_agent_guide" in discover["instructions"]
    assert (discover["ttlMs"], discover["cacheScope"]) == (0, "public")
    assert discover["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "diffdesk"

    listed = server.request("tools/list", meta=META)["result"]
    assert listed["resultType"] == "complete" and listed["ttlMs"] == 0
    assert [tool["name"] for tool in listed["tools"]] == TOOL_NAMES
    for tool in listed["tools"]:
        assert tool["description"] and tool["inputSchema"]["type"] == "object"

    result = server.call("list_reviews", {})
    assert result["resultType"] == "complete" and result["isError"] is False
    assert "io.modelcontextprotocol/serverInfo" in result["_meta"]
    assert len(json.loads(text_of(result))) == 7
    assert server.close() == 0


def test_modern_request_errors(start: Callable[..., Mcp], root: Path) -> None:
    server = start("--root", str(root))
    old = {**META, "io.modelcontextprotocol/protocolVersion": "1900-01-01"}
    error = server.request("tools/list", meta=old)["error"]
    assert error["code"] == -32022
    assert error["data"] == {"supported": [MODERN], "requested": "1900-01-01"}

    no_caps = {"io.modelcontextprotocol/protocolVersion": MODERN}
    assert server.request("tools/list", meta=no_caps)["error"]["code"] == -32602
    assert server.request("ping", meta=META)["error"]["code"] == -32601
    assert server.request("initialize", meta=META)["error"]["code"] == -32601
    assert server.request("resources/list", meta=META)["error"]["code"] == -32601
    unknown = server.request("tools/call", {"name": "nope", "arguments": {}}, META)["error"]
    assert (unknown["code"], unknown["message"]) == (-32602, "Unknown tool: nope")
    assert server.request("server/discover", meta=META)["result"]["resultType"] == "complete"
    assert server.close() == 0


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("2025-11-25", "2025-11-25"),
        ("2025-06-18", "2025-06-18"),
        ("2024-11-05", "2025-11-25"),
        (MODERN, "2025-11-25"),
    ],
)
def test_legacy_flow(start: Callable[..., Mcp], root: Path, requested: str, expected: str) -> None:
    server = start("--root", str(root))
    assert server.request("ping")["result"] == {}
    init = server.initialize(requested)
    assert init["protocolVersion"] == expected
    assert init["capabilities"] == {"tools": {}}
    assert init["serverInfo"]["name"] == "diffdesk"
    assert "get_agent_guide" in init["instructions"]

    listed = server.request("tools/list")["result"]
    assert list(listed) == ["tools"]
    assert [tool["name"] for tool in listed["tools"]] == TOOL_NAMES
    params = {"name": "list_reviews", "arguments": {}, "_meta": {"progressToken": 1}}
    result = server.request("tools/call", params)["result"]
    assert sorted(result) == ["content", "isError"] and result["isError"] is False
    assert len(json.loads(text_of(result))) == 7
    assert server.request("ping")["result"] == {}
    assert server.request("server/discover")["error"]["code"] == -32601
    assert server.close() == 0


def test_requests_before_initialize(start: Callable[..., Mcp], root: Path) -> None:
    server = start("--root", str(root))
    error = server.request("tools/list")["error"]
    assert error["code"] == -32602 and "initialize" in error["message"]
    assert server.request("no/such/method")["error"]["code"] == -32601
    assert server.request("initialize", {"capabilities": {}})["error"]["code"] == -32602
    assert server.initialize()["protocolVersion"] == "2025-11-25"
    assert server.close() == 0


def test_tools(start: Callable[..., Mcp], root: Path, repo: Repo) -> None:
    server = start("--root", str(root))
    args = {
        "repo": str(repo.path),
        "id": "MCP-1",
        "title": "Binary ✓",
        "package": "alpha",
        "why": "**Problem**\nNone.",
        "tags": ["x"],
    }
    added = server.call("add_subject", args)
    assert added["isError"] is False
    data = json.loads(text_of(added))
    path = (root / "subjects" / "alpha" / "MCP-1.json").resolve()
    assert Path(data["path"]) == path
    assert data["subject"]["head"] == repo.commits[3]
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written == data["subject"] and written["title"] == "Binary ✓"

    found = server.call(
        "list_reviews", {"statuses": ["pending"], "package": "alpha", "id": "MCP-1"}
    )
    [item] = json.loads(text_of(found))
    assert (item["id"], item["status"], item["edits"]) == ("MCP-1", "pending", [])

    guide = server.call("get_agent_guide", {})
    assert text_of(guide) == GUIDE_PATH.read_text(encoding="utf-8")

    report = json.loads(text_of(server.call("validate_subjects", {})))
    assert Path(report["root"]) == root.resolve()
    assert (report["files"], report["problems"]) == (8, [])
    assert report["warnings"] == [
        "ID DUP is used in packages beta, gamma; API calls for it need 'package'"
    ]
    assert server.close() == 0


def test_tool_errors(start: Callable[..., Mcp], root: Path, repo: Repo, tmp_path: Path) -> None:
    server = start("--root", str(root))
    plain = tmp_path / "plain"
    plain.mkdir()
    base = {"repo": str(repo.path), "package": "alpha", "title": "t"}
    cases = [
        ("add_subject", {**base, "id": "A-12"}, "Subject file exists"),
        ("add_subject", {**base, "id": "../up"}, "Invalid subject ID"),
        ("add_subject", {**base, "id": "X", "repo": str(plain)}, "Not inside a git work tree"),
        ("add_subject", {**base, "id": "X", "commit": "nope"}, "Not a commit"),
        ("add_subject", {"repo": str(repo.path), "id": "X"}, "missing field 'title'"),
        ("add_subject", {**base, "id": "X", "force": "yes"}, "arguments.force: must be boolean"),
        ("list_reviews", {"statuses": ["done"]}, "must be one of"),
        ("get_agent_guide", {"x": 1}, "unknown field 'x'"),
    ]
    for name, arguments, message in cases:
        result = server.call(name, arguments)
        assert result["isError"] is True, (name, arguments)
        assert message in text_of(result), text_of(result)
    assert not (root / "subjects" / "up.json").exists()
    assert not (root / "subjects" / "alpha" / "X.json").exists()

    (root / "diffdesk.toml").write_text('[paths]\nsubjects = "../outside"\n', encoding="utf-8")
    broken = server.call("validate_subjects", {})
    assert broken["isError"] is True and "must be relative" in text_of(broken)
    assert server.call("get_agent_guide", {})["isError"] is False
    assert server.close() == 0


def test_messages_without_reply_and_bad_input(start: Callable[..., Mcp], root: Path) -> None:
    server = start("--root", str(root))
    server.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}})
    server.send({"jsonrpc": "2.0", "method": "notifications/unknown"})
    server.send({"jsonrpc": "2.0", "id": 99, "result": {}})
    server.send_raw(b"\n")
    assert server.request("server/discover", meta=META)["result"]["resultType"] == "complete"

    for raw in (b"not json\n", b"\xff\xfe\n"):
        server.send_raw(raw)
        reply = server.receive()
        assert (reply["id"], reply["error"]["code"]) == (None, -32700)
    server.send([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
    assert server.receive() == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32600, "message": "A message must be one JSON-RPC object"},
    }
    server.send({"jsonrpc": "2.0", "id": None, "method": "tools/list"})
    assert server.receive()["error"]["code"] == -32600
    server.send({"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": [1]})
    assert server.receive()["error"]["code"] == -32602
    assert server.request("tools/list", meta=META)["result"]["tools"]
    assert server.close() == 0


def test_end_of_input_stops_the_server(start: Callable[..., Mcp], root: Path) -> None:
    server = start("--root", str(root))
    assert server.close() == 0
    assert "MCP server on stdio" in server.stderr_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("how", ["option", "env", "cwd"])
def test_root_selection(
    start: Callable[..., Mcp],
    root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    how: str,
) -> None:
    if how == "option":
        monkeypatch.setenv("DIFFDESK_ROOT", str(tmp_path / "other"))
        server = start("--root", str(root))
    elif how == "env":
        monkeypatch.setenv("DIFFDESK_ROOT", str(root))
        server = start()
    else:
        server = start(cwd=root)
    report = json.loads(text_of(server.call("validate_subjects", {})))
    assert Path(report["root"]) == root.resolve() and report["files"] == 7
    assert server.close() == 0
