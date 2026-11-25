"""Subject index of one review root.

The index keeps every parsed subject file keyed by its ``mtime_ns`` and
size, and parses a file again only when one of them changes. Review status
comes from ``reviews.json``, the edit flag from the ``edits`` folder, and
line counts from ``git diff-tree --numstat``, cached on disk in
``<cache>/numstat.json`` for diffs between two full commit IDs.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import tempfile
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import gitio
from .config import Config
from .gitio import GitError, InvalidInput, Numstat

log = logging.getLogger(__name__)

ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
STATUSES = ("pending", "accepted", "rejected", "change")
VERDICT_STATUS = {"accept": "accepted", "reject": "rejected", "change": "change"}
DEFAULT_PACKAGE = "default"
NO_REPO_BRANCH = "(no repo)"
DETACHED_BRANCH = "HEAD"
NUMSTAT_FORMAT = 1

Key = tuple[str, str]

# Read once at import, while only one thread runs: os.umask can only be read
# by setting it.
_UMASK = os.umask(0)
os.umask(_UMASK)


class NotFound(LookupError):
    """No subject, file or endpoint matches the request."""


def check_name(value: Any, what: str) -> str:
    """Return `value` if it is a safe subject ID or package name.

    Parameters
    ----------
    value : Any
        Value from a subject file or a request.
    what : str
        Name of the value for the error message.

    Returns
    -------
    str
        The unchanged value.

    Raises
    ------
    InvalidInput
        If `value` is not letters, digits, ``.``, ``_`` and ``-`` that start
        with a letter or digit, or if it contains ``..``.
    """
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value) or ".." in value:
        raise InvalidInput(f"Invalid {what}: {value!r}")
    return value


def write_atomic(path: Path, data: bytes) -> None:
    """Write `data` to `path` through a temporary file and ``os.replace``.

    The file keeps its permission bits if it exists. A new file gets
    ``0o666`` minus the umask, like a file made with ``open()``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except FileNotFoundError:
            mode = 0o666 & ~_UMASK
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def natural_key(text: str) -> tuple[tuple[int, int, str], ...]:
    """Sort key that compares runs of digits as numbers (``SB-2`` before ``SB-12``)."""
    return tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part)
        for part in re.split(r"(\d+)", text)
        if part
    )


@dataclass
class Subject:
    """The fields of one subject file that the index needs.

    `branch` is the `branch` field of the file, or the branch resolved by
    the index (see `Index`).
    """

    source: Path
    package: str
    id: str
    title: str
    branch_field: str | None
    repo: str | None
    base: str | None
    head: str | None
    files: list[str]
    order: float | None
    depends_on: list[str]
    extra: dict[str, Any]
    search: str
    branch: str = ""

    @property
    def key(self) -> Key:
        """``(package, id)``, unique in one review root."""
        return (self.package, self.id)

    @property
    def has_diff(self) -> bool:
        """`True` if the subject has a repository, a base and a head."""
        return self.repo is not None and self.base is not None and self.head is not None


def parse_subject(path: Path, default_package: str) -> Subject:
    """Read one subject file.

    Parameters
    ----------
    path : Path
        The JSON file.
    default_package : str
        Package if the file has no ``package`` field: the first folder below
        the subjects folder, or ``DEFAULT_PACKAGE`` for a file directly in it.

    Returns
    -------
    Subject
        The parsed subject.

    Raises
    ------
    OSError
        If the file cannot be read.
    ValueError
        If the file is not a JSON object, or a field has a value that the
        index cannot use (`InvalidInput` is a subclass).
    """
    data = json.loads(path.read_bytes())
    if not isinstance(data, dict):
        raise ValueError("top-level value is not an object")
    sid = check_name(data.get("id", path.stem), "subject ID")
    package = check_name(data.get("package") or default_package, "package")
    title = data.get("title")
    branch = data.get("branch")
    repo = data.get("repo")
    if repo is not None and not isinstance(repo, str):
        raise ValueError(f"'repo' is not a string: {repo!r}")
    base, head = data.get("base"), data.get("head")
    if (base is None) != (head is None):
        raise ValueError("'base' and 'head' must both be set or both be null")
    if base is not None:
        base, head = gitio.check_ref(base), gitio.check_ref(head)
    files = data.get("files") or []
    if not isinstance(files, list):
        raise ValueError("'files' is not a list")
    files = [gitio.check_path(p) for p in files]
    order = data.get("order")
    if isinstance(order, bool) or not isinstance(order, int | float):
        order = None
    depends_on = data.get("depends_on") or []
    if not isinstance(depends_on, list):
        depends_on = []
    tags = data.get("tags") or []
    words = [sid, title, data.get("summary"), *(tags if isinstance(tags, list) else [])]
    return Subject(
        source=path,
        package=package,
        id=sid,
        title=title if isinstance(title, str) else sid,
        branch_field=branch if isinstance(branch, str) and branch else None,
        repo=repo,
        base=base,
        head=head,
        files=files,
        order=order,
        depends_on=[d for d in depends_on if isinstance(d, str)],
        extra={k: data.get(k) for k in ("priority", "origin", "recommended", "senior_verdict")},
        search=" ".join(w for w in words if isinstance(w, str)).lower(),
    )


@dataclass
class Snapshot:
    """A consistent view of the subjects, their status and edit flags.

    Attributes
    ----------
    subjects : list of Subject
        Sorted by package, branch, repository, then the order inside the
        branch (commit order, then `order`, then natural ID order).
    by_key : dict
        ``(package, id)`` to subject.
    by_id : dict
        ID to every subject with this ID (more than one if two packages use
        the same ID).
    status : dict
        ``(package, id)`` to one of `STATUSES`.
    review : dict
        ``(package, id)`` to the last review record, if any.
    history_count : dict
        ``(package, id)`` to the number of review records.
    edited : frozenset
        Keys of the subjects that have at least one saved edit.
    memo : dict
        Responses computed from this snapshot, reused until it changes.
    """

    subjects: list[Subject]
    by_key: dict[Key, Subject]
    by_id: dict[str, list[Subject]]
    status: dict[Key, str]
    review: dict[Key, dict[str, Any]]
    history_count: dict[Key, int]
    edited: frozenset[Key]
    memo: dict[str, Any] = field(default_factory=dict)

    def find(self, subject_id: Any, package: Any = None) -> Subject:
        """Return the subject with ID `subject_id` (and `package`, if given).

        Raises
        ------
        InvalidInput
            If the ID or package is not valid, or the ID is used in more than
            one package and `package` is not given.
        NotFound
            If no subject matches.
        """
        sid = check_name(subject_id, "subject ID")
        if package not in (None, ""):
            subject = self.by_key.get((check_name(package, "package"), sid))
            if subject is None:
                raise NotFound(f"No subject {sid!r} in package {package!r}")
            return subject
        matches = self.by_id.get(sid, [])
        if not matches:
            raise NotFound(f"No subject with ID {sid!r}")
        if len(matches) > 1:
            names = ", ".join(s.package for s in matches)
            raise InvalidInput(f"Subject ID {sid!r} is used in packages {names}; add 'package'")
        return matches[0]


class Index:
    """Cached subject index of one review root.

    A subject without a `branch` field belongs to the branch checked out in
    its repository (``DETACHED_BRANCH`` if HEAD is detached), or to
    ``NO_REPO_BRANCH`` if it has no repository. Checked-out branches are read
    again only when a subject file changes.

    Parameters
    ----------
    config : Config
        Settings of the review root.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._numstat_lock = threading.Lock()
        self._files: dict[str, tuple[int, int, Subject | None]] = {}
        self._subjects: list[Subject] = []
        self._subjects_changed = True
        self._reviews_stat: tuple[int, int] | None = None
        self._reviews: list[Any] = []
        self._edited: frozenset[Key] = frozenset()
        self._order_cache: dict[tuple[str, tuple[str, ...]], dict[str, int]] = {}
        self._numstat_disk: dict[str, Numstat] | None = None
        self._numstat_memory: dict[str, Numstat | None] = {}
        self._blob_cache: dict[tuple[str, ...], list[dict[str, int | None]]] = {}
        self._snapshot: Snapshot | None = None

    def snapshot(self) -> Snapshot:
        """Bring the index up to date with the files and return a snapshot.

        Raises
        ------
        ValueError
            If ``reviews.json`` exists but is not a JSON list.
        """
        with self._lock:
            subjects_changed = self._scan_subjects()
            reviews_changed = self._load_reviews()
            edited = self._scan_edits()
            edits_changed = edited != self._edited
            self._edited = edited
            if self._snapshot is None or subjects_changed or reviews_changed or edits_changed:
                self._snapshot = self._build_snapshot()
            return self._snapshot

    def _scan_subjects(self) -> bool:
        # Plain strings, not Path objects: hashing 10,000 Paths per request
        # costs more than the stat calls.
        root = self.config.subjects
        seen: dict[str, tuple[int, int, str]] = {}
        stack = [(str(root), DEFAULT_PACKAGE)] if root.is_dir() else []
        while stack:
            folder, package = stack.pop()
            try:
                entries = list(os.scandir(folder))
            except OSError as exc:
                log.warning("Cannot list %s: %s", folder, exc)
                continue
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    top = folder == str(root)
                    stack.append((entry.path, entry.name if top else package))
                elif entry.name.endswith(".json") and entry.is_file():
                    try:
                        st = entry.stat()
                    except OSError:
                        continue
                    seen[entry.path] = (st.st_mtime_ns, st.st_size, package)
        changed = seen.keys() != self._files.keys()
        for path, (mtime_ns, size, package) in seen.items():
            cached = self._files.get(path)
            if cached is not None and cached[:2] == (mtime_ns, size):
                continue
            changed = True
            subject: Subject | None
            try:
                subject = parse_subject(Path(path), package)
            except (OSError, ValueError) as exc:
                log.warning("Skipping %s: %s", path, exc)
                subject = None
            self._files[path] = (mtime_ns, size, subject)
        for path in self._files.keys() - seen.keys():
            del self._files[path]
        if changed:
            self._subjects_changed = True
        return changed

    def _load_reviews(self) -> bool:
        path = self.config.reviews
        try:
            st = path.stat()
        except FileNotFoundError:
            changed = self._reviews_stat is not None
            self._reviews_stat, self._reviews = None, []
            return changed
        stamp = (st.st_mtime_ns, st.st_size)
        if stamp == self._reviews_stat:
            return False
        self._reviews = read_reviews(path)
        self._reviews_stat = stamp
        return True

    def _scan_edits(self) -> frozenset[Key]:
        found: set[Key] = set()
        root = self.config.edits
        if not root.is_dir():
            return frozenset()
        for pkg in os.scandir(root):
            if pkg.name.startswith(".") or not pkg.is_dir():
                continue
            for sub in os.scandir(pkg.path):
                if sub.name.startswith(".") or not sub.is_dir():
                    continue
                if _has_file(Path(sub.path)):
                    found.add((pkg.name, sub.name))
        return frozenset(found)

    def _build_snapshot(self) -> Snapshot:
        if self._subjects_changed:
            self._subjects = self._order_subjects()
            self._subjects_changed = False
            self._numstat_memory.clear()
        subjects = self._subjects
        by_key = {s.key: s for s in subjects}
        by_id: dict[str, list[Subject]] = {}
        for s in subjects:
            by_id.setdefault(s.id, []).append(s)

        last: dict[Key, dict[str, Any]] = {}
        count: dict[Key, int] = {}
        for rec in self._reviews:
            if not isinstance(rec, dict):
                continue
            sid, pkg = rec.get("id"), rec.get("package")
            if isinstance(pkg, str):
                keys = [(pkg, sid)] if (pkg, sid) in by_key else []
            else:
                keys = [s.key for s in by_id.get(sid, [])] if isinstance(sid, str) else []
            for key in keys:
                last[key] = rec
                count[key] = count.get(key, 0) + 1
        status = {
            s.key: VERDICT_STATUS.get(last[s.key].get("verdict"), "pending")
            if s.key in last
            else "pending"
            for s in subjects
        }
        return Snapshot(
            subjects=subjects,
            by_key=by_key,
            by_id=by_id,
            status=status,
            review=last,
            history_count=count,
            edited=self._edited,
        )

    def _order_subjects(self) -> list[Subject]:
        unique: dict[Key, Subject] = {}
        for path in sorted(self._files):
            subject = self._files[path][2]
            if subject is None:
                continue
            if subject.key in unique:
                log.warning(
                    "Skipping %s: same package and ID as %s", path, unique[subject.key].source
                )
                continue
            unique[subject.key] = subject
        subjects = list(unique.values())

        branches: dict[str, str | None] = {}
        heads: dict[str, set[str]] = {}
        for s in subjects:
            if s.branch_field is None and s.repo is not None and s.repo not in branches:
                branches[s.repo] = gitio.current_branch(s.repo)
            if s.has_diff:
                heads.setdefault(s.repo, set()).add(s.head)
        for s in subjects:
            if s.branch_field is not None:
                s.branch = s.branch_field
            elif s.repo is None:
                s.branch = NO_REPO_BRANCH
            else:
                s.branch = branches[s.repo] or DETACHED_BRANCH

        positions: dict[str, dict[str, int]] = {}
        for repo, repo_heads in heads.items():
            cache_key = (repo, tuple(sorted(repo_heads)))
            if cache_key not in self._order_cache:
                try:
                    self._order_cache[cache_key] = gitio.commit_order(repo, sorted(repo_heads))
                except GitError as exc:
                    log.warning("No commit order for %s: %s", repo, exc)
                    self._order_cache[cache_key] = {}
            positions[repo] = self._order_cache[cache_key]
        live = {(repo, tuple(sorted(h))) for repo, h in heads.items()}
        for stale in self._order_cache.keys() - live:
            del self._order_cache[stale]

        def sort_key(s: Subject) -> tuple[Any, ...]:
            pos = positions.get(s.repo or "", {}).get(s.head or "") if s.has_diff else None
            return (
                s.package,
                s.branch,
                s.repo or "",
                pos is None,
                pos or 0,
                s.order is None,
                s.order or 0,
                natural_key(s.id),
            )

        return sorted(subjects, key=sort_key)

    def _load_numstat_disk(self) -> dict[str, Numstat]:
        if self._numstat_disk is None:
            path = self.config.cache / "numstat.json"
            self._numstat_disk = {}
            try:
                data = json.loads(path.read_bytes())
                if data.get("format") == NUMSTAT_FORMAT and isinstance(data.get("entries"), dict):
                    self._numstat_disk = data["entries"]
            except FileNotFoundError:
                pass
            except (OSError, ValueError, AttributeError) as exc:
                log.warning("Ignoring %s: %s", path, exc)
        return self._numstat_disk

    def numstat(self, subjects: Iterable[Subject]) -> dict[Key, Numstat | None]:
        """Return ``{path: [added, deleted]}`` for each subject with a diff.

        Diffs not in the cache are computed with one ``git diff-tree`` call
        per repository. Diffs between two full commit IDs are stored in
        ``<cache>/numstat.json``. Others are kept in memory until a subject
        file changes.

        Returns
        -------
        dict
            ``(package, id)`` to the counts, or to `None` if git cannot
            compute them. Subjects without a diff are absent.
        """
        wanted = [s for s in subjects if s.has_diff]
        with self._numstat_lock:
            disk = self._load_numstat_disk()
            memory = self._numstat_memory
            missing: dict[str, list[tuple[str, str]]] = {}
            for s in wanted:
                key = _numstat_key(s)
                if key not in disk and key not in memory:
                    missing.setdefault(s.repo, []).append((s.base, s.head))
            dirty = False
            for repo, pairs in missing.items():
                try:
                    found = gitio.numstat_many(repo, pairs)
                except GitError as exc:
                    log.warning("No line counts for %s: %s", repo, exc)
                    found = dict.fromkeys(pairs)
                for (base, head), counts in found.items():
                    key = f"{repo}\n{base}\n{head}"
                    if (
                        counts is not None
                        and gitio.HEX_SHA.fullmatch(base)
                        and (gitio.HEX_SHA.fullmatch(head))
                    ):
                        disk[key] = counts
                        dirty = True
                    else:
                        memory[key] = counts
            if dirty:
                self._save_numstat(disk)
            return {
                s.key: disk[k] if k in disk else memory.get(k)
                for s in wanted
                for k in [_numstat_key(s)]
            }

    def _save_numstat(self, disk: dict[str, Numstat]) -> None:
        path = self.config.cache / "numstat.json"
        payload = {"format": NUMSTAT_FORMAT, "entries": disk}
        try:
            write_atomic(path, json.dumps(payload, separators=(",", ":")).encode())
        except OSError as exc:
            log.warning("Cannot write %s: %s", path, exc)

    def blob_stats(self, subject: Subject, paths: list[str]) -> list[dict[str, int | None]]:
        """Return the size in bytes and the line count of each path at base and head.

        Results for two full commit IDs are cached in memory.

        Returns
        -------
        list of dict
            ``{size_base, size_head, lines_base, lines_head}`` per path, each
            `None` where the file is absent.

        Raises
        ------
        GitError
            If git fails.
        """
        if not subject.has_diff or not paths:
            return [dict.fromkeys(_BLOB_FIELDS) for _ in paths]
        key = (subject.repo, subject.base, subject.head, *paths)
        cached = self._blob_cache.get(key)
        if cached is None:
            specs = [(rev, p) for p in paths for rev in (subject.base, subject.head)]
            blobs = gitio.read_blobs(subject.repo, specs)
            cached = [
                {
                    "size_base": _size(base),
                    "size_head": _size(head),
                    "lines_base": count_lines(base),
                    "lines_head": count_lines(head),
                }
                for base, head in zip(blobs[0::2], blobs[1::2], strict=True)
            ]
            if gitio.HEX_SHA.fullmatch(subject.base) and gitio.HEX_SHA.fullmatch(subject.head):
                self._blob_cache[key] = cached
        return cached


_BLOB_FIELDS = ("size_base", "size_head", "lines_base", "lines_head")


def _size(blob: bytes | None) -> int | None:
    return None if blob is None else len(blob)


def count_lines(blob: bytes | None) -> int | None:
    """Return the number of lines in `blob` (a last line without a newline counts)."""
    if blob is None:
        return None
    return blob.count(b"\n") + (1 if blob and not blob.endswith(b"\n") else 0)


def _numstat_key(subject: Subject) -> str:
    return f"{subject.repo}\n{subject.base}\n{subject.head}"


def _has_file(folder: Path) -> bool:
    # Skip only the temporary files of `write_atomic`: an edit may be a dotfile.
    for _dirpath, _dirnames, filenames in os.walk(folder):
        if any(not (f.startswith(".") and f.endswith(".tmp")) for f in filenames):
            return True
    return False


def read_reviews(path: Path) -> list[Any]:
    """Return the review records in ``reviews.json`` (a JSON list).

    Raises
    ------
    ValueError
        If the file is not valid JSON or does not hold a list.
    """
    data = json.loads(path.read_bytes())
    if not isinstance(data, list):
        raise ValueError(f"{path} does not hold a JSON list")
    return data
