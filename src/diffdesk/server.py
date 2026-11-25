"""HTTP server for the diffdesk browser app and its JSON API (version 1).

Only the Python standard library is used. The server binds 127.0.0.1 by
default, accepts only requests whose ``Host`` header names the bound address,
checks ``Origin`` and ``Sec-Fetch-Site`` on writes, and writes only to the
reviews file and the edits folder of the review root.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import mimetypes
import socket
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import __version__, gitio
from .config import Config
from .gitio import GitError, InvalidInput, Numstat
from .index import (
    STATUSES,
    VERDICT_STATUS,
    Index,
    NotFound,
    Snapshot,
    Subject,
    read_reviews,
    write_atomic,
)

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
GZIP_MIN_BYTES = 1024
GZIP_LEVEL = 6
DEFAULT_LIMIT = 200
DEFAULT_CONTEXT = 3
IMMUTABLE = "max-age=31536000, immutable"
REVALIDATE = "no-cache"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".wasm": "application/wasm",
}

LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".pyx": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".java": "java",
    ".go": "go",
    ".json": "json",
    ".md": "markdown",
    ".markdown": "markdown",
    ".rst": "restructuredtext",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".html": "html",
    ".htm": "html",
    ".xml": "xml",
    ".css": "css",
    ".scss": "scss",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".sql": "sql",
    ".f": "fortran",
    ".f90": "fortran",
    ".jl": "julia",
    ".r": "r",
    ".tex": "latex",
    ".ini": "ini",
    ".cfg": "ini",
}
LANGUAGE_NAMES = {"Makefile": "makefile", "Dockerfile": "dockerfile", "CMakeLists.txt": "cmake"}


class Conflict(RuntimeError):
    """The request names commits that the subject no longer points at (HTTP 409)."""


def language_of(path: str) -> str | None:
    """Return the editor language name for `path`, or `None` if unknown."""
    pure = PurePosixPath(path)
    return LANGUAGE_NAMES.get(pure.name) or LANGUAGES.get(pure.suffix.lower())


def _int_param(query: dict[str, list[str]], name: str, default: int) -> int:
    raw = query.get(name, [""])[0]
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise InvalidInput(f"'{name}' must be an integer: {raw!r}") from None
    if value < 0:
        raise InvalidInput(f"'{name}' must be 0 or more: {value}")
    return value


def _param(query: dict[str, list[str]], name: str) -> str:
    return query.get(name, [""])[0]


def _counts(subjects: list[Subject], snap: Snapshot) -> dict[str, Any]:
    by_status = dict.fromkeys(STATUSES, 0)
    for s in subjects:
        by_status[snap.status[s.key]] += 1
    return {
        "total": len(subjects),
        "reviewed": len(subjects) - by_status["pending"],
        "by_status": by_status,
    }


def _lines(subject: Subject, counts: Numstat | None) -> tuple[int | None, int | None, int | None]:
    """Return ``(files_count, lines_added, lines_deleted)`` of a subject.

    Only the files listed in the subject count, if it lists any. A subject
    without a diff has 0 lines. Counts are `None` if git could not compute
    them.
    """
    if not subject.has_diff:
        return len(subject.files), 0, 0
    if counts is None:
        return (len(subject.files) or None), None, None
    paths = subject.files or list(counts)
    added = deleted = 0
    for p in paths:
        a, d = counts.get(p, (0, 0))
        added += a or 0
        deleted += d or 0
    return len(paths), added, deleted


def _subject_paths(subject: Subject, counts: Numstat | None) -> list[str]:
    if subject.files or not subject.has_diff:
        return subject.files
    if counts is None:
        raise GitError(f"Cannot list the files of subject {subject.id}")
    return sorted(counts)


class DiffdeskServer(ThreadingHTTPServer):
    """Threaded HTTP server that holds the index of one review root.

    Parameters
    ----------
    config : Config
        Settings of the review root.
    host : str
        Address to bind.
    port : int
        Port to bind; 0 picks a free port.
    """

    daemon_threads = True

    def __init__(self, config: Config, host: str, port: int) -> None:
        if ":" in host:
            self.address_family = socket.AF_INET6
        super().__init__((host, port), Handler)
        self.config = config
        self.index = Index(config)
        self.reviews_lock = threading.Lock()
        self.edits_lock = threading.Lock()
        self.gzip_cache: dict[Path, tuple[int, int, bytes]] = {}
        bound = self.server_address[1]
        names = {"127.0.0.1", "localhost", "[::1]"}
        if host not in ("", "0.0.0.0", "::"):
            names.add(f"[{host}]" if ":" in host else host)
        self.allowed_hosts = {f"{name}:{bound}" for name in names}

    # ---- read endpoints -------------------------------------------------

    def meta(self) -> dict[str, Any]:
        """Return ``GET /api/meta``."""
        snap = self.index.snapshot()
        names = sorted({*self.config.packages, *(s.package for s in snap.subjects)})
        counts = _counts(snap.subjects, snap)
        return {
            "name": "diffdesk",
            "version": __version__,
            "title": self.config.title,
            "root": str(self.config.root),
            "packages": {n: {"depends_on": self.config.packages.get(n, [])} for n in names},
            "ui_defaults": self.config.ui,
            "counts": {"total": counts["total"], "by_status": counts["by_status"]},
        }

    def tree(self) -> list[dict[str, Any]]:
        """Return ``GET /api/tree``: counts per package and per branch."""
        snap = self.index.snapshot()
        if "tree" in snap.memo:
            return snap.memo["tree"]
        groups: dict[str, dict[tuple[str, str | None], list[Subject]]] = {}
        for s in snap.subjects:
            groups.setdefault(s.package, {}).setdefault((s.branch, s.repo), []).append(s)
        out = []
        for package, branches in groups.items():
            members = [s for group in branches.values() for s in group]
            out.append(
                {
                    "package": package,
                    **_counts(members, snap),
                    "branches": [
                        {"name": name, "repo": repo, **_counts(group, snap)}
                        for (name, repo), group in branches.items()
                    ],
                }
            )
        snap.memo["tree"] = out
        return out

    def light(self, s: Subject, snap: Snapshot, counts: Numstat | None) -> dict[str, Any]:
        """Return the light form of a subject used in lists."""
        files_count, added, deleted = _lines(s, counts)
        return {
            "id": s.id,
            "package": s.package,
            "branch": s.branch,
            "title": s.title,
            **s.extra,
            "status": snap.status[s.key],
            "edited": s.key in snap.edited,
            "order": s.order,
            "files_count": files_count,
            "lines_added": added,
            "lines_deleted": deleted,
        }

    def subjects(self, query: dict[str, list[str]]) -> dict[str, Any]:
        """Return ``GET /api/subjects``: one page of filtered light subjects."""
        snap = self.index.snapshot()
        offset = _int_param(query, "offset", 0)
        limit = _int_param(query, "limit", DEFAULT_LIMIT)
        package, branch, repo = (_param(query, k) for k in ("package", "branch", "repo"))
        statuses = {v for v in _param(query, "status").split(",") if v}
        unknown = statuses - set(STATUSES)
        if unknown:
            raise InvalidInput(f"Unknown status {sorted(unknown)}; use {list(STATUSES)}")
        terms = _param(query, "q").lower().split()
        items = [
            s
            for s in snap.subjects
            if (not package or s.package == package)
            and (not branch or s.branch == branch)
            and (not repo or s.repo == repo)
            and (not statuses or snap.status[s.key] in statuses)
            and all(t in s.search for t in terms)
        ]
        page = items[offset : offset + limit]
        counts = self.index.numstat(page)
        return {
            "total": len(items),
            "items": [self.light(s, snap, counts.get(s.key)) for s in page],
        }

    def subject(self, query: dict[str, list[str]]) -> dict[str, Any]:
        """Return ``GET /api/subject``: the full subject file plus review and files."""
        snap = self.index.snapshot()
        s = snap.find(_param(query, "id"), _param(query, "package"))
        try:
            data = json.loads(s.source.read_bytes())
        except FileNotFoundError:
            raise NotFound(f"Subject file is gone: {s.source}") from None
        counts = self.index.numstat([s]).get(s.key)
        paths = _subject_paths(s, counts)
        stats = self.index.blob_stats(s, paths)
        files = []
        for path, stat in zip(paths, stats, strict=True):
            added, deleted = (counts or {}).get(path, (None, None))
            files.append(
                {
                    "path": path,
                    "lines_added": added,
                    "lines_deleted": deleted,
                    **stat,
                    "edited": self.edit_target(s, path).is_file(),
                }
            )
        rec = snap.review.get(s.key) or {}
        data.update(
            id=s.id,
            package=s.package,
            branch=s.branch,
            status=snap.status[s.key],
            edited=s.key in snap.edited,
            review={
                "verdict": rec.get("verdict"),
                "comment": rec.get("comment"),
                "at": rec.get("at", rec.get("time")),
                "history_count": snap.history_count.get(s.key, 0),
            },
            files=files,
        )
        return data

    def flow(self) -> dict[str, Any]:
        """Return ``GET /api/flow``: package nodes and ``[package, dependency]`` edges."""
        snap = self.index.snapshot()
        if "flow" in snap.memo:
            return snap.memo["flow"]
        edges: set[tuple[str, str]] = set()
        for pkg, deps in self.config.packages.items():
            edges.update((pkg, d) for d in deps if d != pkg)
        packages = {s.package for s in snap.subjects}
        for s in snap.subjects:
            for dep in s.depends_on:
                targets = [t.package for t in snap.by_id.get(dep, [])]
                if not targets and dep in packages:
                    targets = [dep]
                edges.update((s.package, t) for t in targets if t != s.package)
        names = sorted({*packages, *self.config.packages, *(n for e in edges for n in e)})
        members: dict[str, list[Subject]] = {n: [] for n in names}
        for s in snap.subjects:
            members[s.package].append(s)
        out = {
            "nodes": [{"package": n, **_counts(members[n], snap)} for n in names],
            "edges": [list(e) for e in sorted(edges)],
        }
        snap.memo["flow"] = out
        return out

    def treemap(self, query: dict[str, list[str]]) -> list[dict[str, Any]]:
        """Return ``GET /api/treemap``: every subject (of one package) with line counts."""
        snap = self.index.snapshot()
        package = _param(query, "package")
        items = [s for s in snap.subjects if not package or s.package == package]
        counts = self.index.numstat(items)
        out = []
        for s in items:
            _, added, deleted = _lines(s, counts.get(s.key))
            out.append(
                {
                    "id": s.id,
                    "package": s.package,
                    "branch": s.branch,
                    "title": s.title,
                    "status": snap.status[s.key],
                    "lines_added": added,
                    "lines_deleted": deleted,
                }
            )
        return out

    def diff_request(
        self, query: dict[str, list[str]]
    ) -> tuple[Subject, str, str, int, bool, bool, str | None]:
        """Validate ``GET /api/diff`` and return what `diff` needs.

        Returns
        -------
        tuple
            ``(subject, path, mode, context, ignore_space, immutable, etag)``.
            `etag` is `None` if the revisions are not full commit IDs, because
            then the response can change without any input change.
        """
        snap = self.index.snapshot()
        s = snap.find(_param(query, "id"), _param(query, "package"))
        if not s.has_diff:
            raise NotFound(f"Subject {s.id} has no diff")
        path = gitio.check_path(_param(query, "path"))
        mode = _param(query, "mode") or "full"
        if mode not in ("full", "hunks"):
            raise InvalidInput(f"'mode' must be 'full' or 'hunks': {mode!r}")
        context = _int_param(query, "context", DEFAULT_CONTEXT)
        ignore_space = _param(query, "ignore_ws") in ("1", "true")
        pinned = _param(query, "base"), _param(query, "head")
        if any(pinned) and pinned != (s.base, s.head):
            raise Conflict(f"Subject {s.id} now points at {s.base}..{s.head}")
        counts = None if s.files else self.index.numstat([s]).get(s.key)
        if path not in _subject_paths(s, counts):
            raise InvalidInput(f"Path {path!r} is not a file of subject {s.id}")
        shas = bool(gitio.HEX_SHA.fullmatch(s.base) and gitio.HEX_SHA.fullmatch(s.head))
        immutable = mode == "hunks" and shas and all(pinned)
        etag = None
        if shas:
            parts: list[Any] = [s.repo, s.base, s.head, path, mode]
            if mode == "full":
                try:
                    st = self.edit_target(s, path).stat()
                    parts.append([st.st_mtime_ns, st.st_size])
                except FileNotFoundError:
                    parts.append(None)
            else:
                parts += [context, ignore_space]
            etag = hashlib.sha1(json.dumps(parts).encode()).hexdigest()
        return s, path, mode, context, ignore_space, immutable, etag

    def diff(
        self, s: Subject, path: str, mode: str, context: int, ignore_space: bool
    ) -> dict[str, Any]:
        """Return the body of ``GET /api/diff``."""
        if mode == "hunks":
            hunks, binary = gitio.diff_hunks(s.repo, s.base, s.head, path, context, ignore_space)
            return {"hunks": hunks, "binary": binary}
        base, head = gitio.read_blobs(s.repo, [(s.base, path), (s.head, path)])
        target = self.edit_target(s, path)
        edit = target.read_bytes().decode("utf-8", errors="replace") if target.is_file() else None
        binary = any(b is not None and b"\0" in b for b in (base, head))
        return {
            "base": None if binary or base is None else base.decode("utf-8", errors="replace"),
            "head": None if binary or head is None else head.decode("utf-8", errors="replace"),
            "edit": edit,
            "language": language_of(path),
            "binary": binary,
        }

    # ---- write endpoints ------------------------------------------------

    def edit_target(self, s: Subject, path: str) -> Path:
        """Return ``<edits>/<package>/<id>/<path>`` and check that it stays there.

        Raises
        ------
        InvalidInput
            If the resolved path leaves the edit folder of the subject.
        """
        base = (self.config.edits / s.package / s.id).resolve()
        target = (base / path).resolve()
        if not target.is_relative_to(base) or target == base:
            raise InvalidInput(f"Path leaves the edit folder: {path!r}")
        return target

    def add_review(self, body: dict[str, Any]) -> dict[str, Any]:
        """Validate a verdict and append it to the reviews file."""
        s = self.index.snapshot().find(body.get("id"), body.get("package"))
        verdict = body.get("verdict")
        if verdict not in VERDICT_STATUS:
            raise InvalidInput(f"'verdict' must be one of {list(VERDICT_STATUS)}: {verdict!r}")
        comment = body.get("comment") or ""
        if not isinstance(comment, str):
            raise InvalidInput("'comment' must be a string")
        record = {
            "id": s.id,
            "package": s.package,
            "verdict": verdict,
            "comment": comment,
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        path = self.config.reviews
        with self.reviews_lock:
            history = read_reviews(path) if path.exists() else []
            history.append(record)
            write_atomic(path, (json.dumps(history, indent=2, ensure_ascii=False) + "\n").encode())
        log.info("review %s/%s: %s", s.package, s.id, verdict)
        return {**record, "status": VERDICT_STATUS[verdict]}

    def save_edit(self, body: dict[str, Any]) -> dict[str, Any]:
        """Write or remove the saved edit of one file of a subject."""
        s = self.index.snapshot().find(body.get("id"), body.get("package"))
        path = gitio.check_path(body.get("path"))
        if "content" not in body:
            raise InvalidInput("'content' is required (a string, or null to remove the edit)")
        content = body["content"]
        if content is not None and not isinstance(content, str):
            raise InvalidInput("'content' must be a string or null")
        if not s.has_diff:
            raise InvalidInput(f"Subject {s.id} has no diff, so it has no files to edit")
        counts = None if s.files else self.index.numstat([s]).get(s.key)
        if path not in _subject_paths(s, counts):
            raise InvalidInput(f"Path {path!r} is not a file of subject {s.id}")
        target = self.edit_target(s, path)
        root = self.config.root
        with self.edits_lock:
            if content is not None:
                write_atomic(target, content.encode("utf-8"))
            else:
                target.unlink(missing_ok=True)
                stop = self.config.edits.resolve()
                folder = target.parent
                while folder != stop and folder.is_relative_to(stop) and not any(folder.iterdir()):
                    folder.rmdir()
                    folder = folder.parent
        saved = None if content is None else target.relative_to(root.resolve()).as_posix()
        log.info("edit %s/%s: %s (%s)", s.package, s.id, path, saved or "removed")
        return {"id": s.id, "package": s.package, "path": path, "saved": saved}


class Handler(BaseHTTPRequestHandler):
    """Request handler for the static files and the JSON API."""

    server: DiffdeskServer
    server_version = f"diffdesk/{__version__}"
    protocol_version = "HTTP/1.1"

    body_pending = False

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("%s %s", self.address_string(), format % args)

    def note_body(self) -> None:
        """Record whether the request declares a body that is not read yet.

        Such a connection is closed after the response. Otherwise, on a kept
        alive HTTP/1.1 connection, an unread body (for example of a POST that
        failed the origin check) would be parsed as the next request.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        self.body_pending = length != 0 or "Transfer-Encoding" in self.headers

    # ---- responses ------------------------------------------------------

    def _accepts_gzip(self) -> bool:
        for part in self.headers.get("Accept-Encoding", "").split(","):
            name, *params = part.split(";")
            if name.strip().lower() != "gzip":
                continue
            for param in params:
                key, _, value = param.strip().partition("=")
                if key.lower() == "q":
                    try:
                        return float(value) > 0
                    except ValueError:
                        return False
            return True
        return False

    def _not_modified(self, etag: str) -> bool:
        header = self.headers.get("If-None-Match")
        if header is None:
            return False
        tags = {t.strip().removeprefix("W/") for t in header.split(",")}
        return "*" in tags or etag.removeprefix("W/") in tags

    def send_body(
        self,
        status: int,
        data: bytes,
        content_type: str,
        cache: str = REVALIDATE,
        etag: str | None = None,
        gz: bytes | None = None,
    ) -> None:
        """Send `data`, gzip-compressed when the client accepts it and it is large.

        A 304 response is sent instead when `etag` matches ``If-None-Match``.
        """
        if etag is not None and status == HTTPStatus.OK and self._not_modified(etag):
            self.send_not_modified(etag, cache)
            return
        encoding = None
        if len(data) > GZIP_MIN_BYTES and self._accepts_gzip():
            data = gz if gz is not None else gzip.compress(data, compresslevel=GZIP_LEVEL)
            encoding = "gzip"
        self.send_response(status)
        if self.body_pending:
            self.send_header("Connection", "close")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("X-Content-Type-Options", "nosniff")
        if encoding:
            self.send_header("Content-Encoding", encoding)
        if etag is not None:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(data)

    def send_not_modified(self, etag: str, cache: str) -> None:
        """Send 304 Not Modified."""
        self.send_response(HTTPStatus.NOT_MODIFIED)
        if self.body_pending:
            self.send_header("Connection", "close")
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", cache)
        self.send_header("Vary", "Accept-Encoding")
        self.end_headers()

    def send_json(
        self, status: int, payload: Any, cache: str = REVALIDATE, etag: str | None = None
    ) -> None:
        """Send `payload` as JSON. GET responses get a weak ETag of the body."""
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if etag is None and self.command == "GET" and status == HTTPStatus.OK:
            etag = f'W/"{hashlib.sha1(data).hexdigest()}"'
        if status != HTTPStatus.OK:
            cache = "no-store"
        self.send_body(status, data, "application/json; charset=utf-8", cache, etag)

    def send_error_json(self, status: int, message: str) -> None:
        """Send ``{"error": message}`` with `status`."""
        self.send_json(status, {"error": message})

    # ---- checks ---------------------------------------------------------

    def host_ok(self) -> bool:
        """Reject requests whose ``Host`` header is not the bound address (DNS rebinding)."""
        if self.headers.get("Host", "") in self.server.allowed_hosts:
            return True
        self.send_error_json(HTTPStatus.FORBIDDEN, "Host not allowed")
        return False

    def origin_ok(self) -> bool:
        """Reject writes from other origins."""
        origin = self.headers.get("Origin")
        if origin is not None and urlsplit(origin).netloc not in self.server.allowed_hosts:
            self.send_error_json(HTTPStatus.FORBIDDEN, "Origin not allowed")
            return False
        if self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            self.send_error_json(HTTPStatus.FORBIDDEN, "Cross-site request not allowed")
            return False
        return True

    # ---- methods --------------------------------------------------------

    def do_GET(self) -> None:
        self.note_body()
        if not self.host_ok():
            return
        url = urlsplit(self.path)
        query = parse_qs(url.query, keep_blank_values=True)
        srv = self.server
        try:
            if url.path == "/api/meta":
                self.send_json(HTTPStatus.OK, srv.meta())
            elif url.path == "/api/tree":
                self.send_json(HTTPStatus.OK, srv.tree())
            elif url.path == "/api/subjects":
                self.send_json(HTTPStatus.OK, srv.subjects(query))
            elif url.path == "/api/subject":
                self.send_json(HTTPStatus.OK, srv.subject(query))
            elif url.path == "/api/flow":
                self.send_json(HTTPStatus.OK, srv.flow())
            elif url.path == "/api/treemap":
                self.send_json(HTTPStatus.OK, srv.treemap(query))
            elif url.path == "/api/diff":
                s, path, mode, context, ignore_space, immutable, etag = srv.diff_request(query)
                cache = IMMUTABLE if immutable else REVALIDATE
                tag = f'"{etag}"' if etag else None
                if tag is not None and self._not_modified(tag):
                    self.send_not_modified(tag, cache)
                    return
                body = srv.diff(s, path, mode, context, ignore_space)
                self.send_json(HTTPStatus.OK, body, cache, tag)
            elif url.path.startswith("/api/"):
                raise NotFound(f"Unknown endpoint {url.path}")
            else:
                self.send_static(url.path)
        except InvalidInput as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except NotFound as exc:
            self.send_error_json(HTTPStatus.NOT_FOUND, str(exc))
        except Conflict as exc:
            self.send_error_json(HTTPStatus.CONFLICT, str(exc))
        except (GitError, OSError, ValueError) as exc:
            log.exception("GET %s failed", url.path)
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:
        self.note_body()
        if not self.host_ok() or not self.origin_ok():
            return
        if self.headers.get_content_type() != "application/json":
            self.send_error_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Send application/json")
            return
        url = urlsplit(self.path)
        try:
            length = int(self.headers["Content-Length"])
            if length < 0:
                raise ValueError(length)
        except (KeyError, TypeError, ValueError):
            self.send_error_json(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required")
            return
        data = self.rfile.read(length)
        self.body_pending = False
        try:
            body = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, f"Body is not valid JSON: {exc}")
            return
        try:
            if not isinstance(body, dict):
                raise InvalidInput("Body must be a JSON object")
            if url.path == "/api/review":
                self.send_json(HTTPStatus.OK, self.server.add_review(body))
            elif url.path == "/api/edit":
                self.send_json(HTTPStatus.OK, self.server.save_edit(body))
            else:
                raise NotFound(f"Unknown endpoint {url.path}")
        except InvalidInput as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except NotFound as exc:
            self.send_error_json(HTTPStatus.NOT_FOUND, str(exc))
        except (GitError, OSError, ValueError) as exc:
            log.exception("POST %s failed", url.path)
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def send_static(self, url_path: str) -> None:
        """Send one file of the ``static`` folder of the package."""
        rel = url_path.lstrip("/") or "index.html"
        root = STATIC_DIR.resolve()
        target = (root / rel).resolve()
        if (
            not target.is_relative_to(root)
            or any(p.startswith(".") for p in target.relative_to(root).parts)
            or not target.is_file()
        ):
            raise NotFound(f"No file {url_path}")
        st = target.stat()
        data = target.read_bytes()
        ctype = CONTENT_TYPES.get(target.suffix.lower()) or (
            mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        )
        gz = None
        if len(data) > GZIP_MIN_BYTES and self._accepts_gzip():
            cached = self.server.gzip_cache.get(target)
            if cached is not None and cached[:2] == (st.st_mtime_ns, st.st_size):
                gz = cached[2]
            else:
                gz = gzip.compress(data, compresslevel=GZIP_LEVEL)
                self.server.gzip_cache[target] = (st.st_mtime_ns, st.st_size, gz)
        etag = f'W/"{st.st_mtime_ns:x}-{st.st_size:x}"'
        self.send_body(HTTPStatus.OK, data, ctype, REVALIDATE, etag, gz)
