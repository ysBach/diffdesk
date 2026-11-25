"""Fixtures: temporary git repositories, review roots and a running server."""

from __future__ import annotations

import gzip
import http.client
import json
import os
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from diffdesk.config import load_config
from diffdesk.server import DiffdeskServer


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep git away from the user's global and system configuration."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test Author")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "author@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test Author")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "author@example.invalid")


def git(repo: Path, *args: str) -> str:
    """Run git in `repo` and return its standard output."""
    res = subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def commit(repo: Path, files: dict[str, str | bytes | None], message: str) -> str:
    """Write (or delete, for `None`) files, commit them and return the commit ID."""
    for rel, content in files.items():
        path = repo / rel
        if content is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def write_subject(root: Path, package: str, data: dict[str, Any], name: str | None = None) -> Path:
    """Write one subject file to ``<root>/subjects/<package>/<name or id>.json``."""
    path = root / "subjects" / package / f"{name or data['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


@dataclass
class Repo:
    """A temporary repository with four commits on branch ``work``."""

    path: Path
    commits: list[str] = field(default_factory=list)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    """Repository: c0 base, c1 edits a.py and adds b.txt, c2 edits a.py, c3 adds a binary."""
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "work")
    r = Repo(path)
    r.commits.append(commit(path, {"a.py": "one\ntwo\nthree\n", "README.md": "# r\n"}, "c0"))
    r.commits.append(commit(path, {"a.py": "one\n2\nthree\n", "b.txt": "x\ny\n"}, "c1"))
    r.commits.append(commit(path, {"a.py": "one\n2\nthree\nfour"}, "c2"))
    r.commits.append(commit(path, {"img.bin": b"\x00\x01\x02", "b.txt": None}, "c3"))
    return r


@pytest.fixture
def root(tmp_path: Path, repo: Repo) -> Path:
    """Review root with packages ``alpha``, ``beta`` and ``gamma``.

    - alpha, branch ``work`` of `repo`: A-12 (c0..c1, lists a.py only),
      A-3 (c1..c2), A-2 (c2..c3, binary file). A-note has no diff and no repo.
    - beta: B-1 (c0..c1, lists b.txt, branch field ``feature``), DUP (no diff).
    - gamma: DUP (no diff), the same ID as in beta.
    """
    root = tmp_path / "review"
    c = repo.commits
    common = {"repo": str(repo.path), "priority": "P1", "origin": "new", "recommended": "accept"}
    write_subject(
        root,
        "alpha",
        {
            **common,
            "id": "A-3",
            "title": "Third",
            "base": c[1],
            "head": c[2],
            "files": [],
            "summary": "adds four",
        },
    )
    write_subject(
        root,
        "alpha",
        {
            **common,
            "id": "A-12",
            "title": "First",
            "base": c[0],
            "head": c[1],
            "files": ["a.py"],
            "tags": ["numbers"],
        },
    )
    write_subject(
        root,
        "alpha",
        {**common, "id": "A-2", "title": "Binary", "base": c[2], "head": c[3], "files": []},
    )
    write_subject(
        root,
        "alpha",
        {
            "id": "A-note",
            "title": "No diff",
            "base": None,
            "head": None,
            "files": [],
            "repo": None,
            "order": 1,
        },
    )
    write_subject(
        root,
        "beta",
        {
            "id": "B-1",
            "title": "Beta one",
            "repo": str(repo.path),
            "base": c[0],
            "head": c[1],
            "files": ["b.txt"],
            "branch": "feature",
            "depends_on": ["A-3"],
            "extra_field": {"x": 1},
        },
    )
    write_subject(
        root,
        "beta",
        {"id": "DUP", "title": "Dup in beta", "repo": None, "base": None, "head": None},
    )
    write_subject(
        root,
        "gamma",
        {"id": "DUP", "title": "Dup in gamma", "repo": None, "base": None, "head": None},
    )
    (root / "diffdesk.toml").write_text(
        'title = "Test review"\n[packages.alpha]\ndepends_on = ["beta"]\n[ui]\ntheme = "dark"\n',
        encoding="utf-8",
    )
    return root


@dataclass
class Response:
    """An HTTP response read in full."""

    status: int
    headers: dict[str, str]
    raw: bytes

    @property
    def body(self) -> bytes:
        """Body after gzip decoding."""
        if self.headers.get("content-encoding") == "gzip":
            return gzip.decompress(self.raw)
        return self.raw

    def json(self) -> Any:
        """Body decoded as JSON."""
        return json.loads(self.body)


@dataclass
class Client:
    """Minimal HTTP client for the test server."""

    host: str
    port: int

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """Send one request. `body` is sent as JSON unless it is `bytes`."""
        conn = http.client.HTTPConnection(self.host, self.port, timeout=30)
        hdrs = {"Host": f"127.0.0.1:{self.port}"}
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        conn.request(method, path, body=data, headers=hdrs)
        res = conn.getresponse()
        out = Response(res.status, {k.lower(): v for k, v in res.getheaders()}, res.read())
        conn.close()
        return out


@pytest.fixture
def server(root: Path) -> Iterator[DiffdeskServer]:
    """A running server for `root` on a free port."""
    srv = DiffdeskServer(load_config(root), "127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def client(server: DiffdeskServer) -> Client:
    """Client for `server`."""
    return Client("127.0.0.1", server.server_address[1])


def bump_mtime(path: Path) -> None:
    """Move the modification time of `path` forward, so caches see a change."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
