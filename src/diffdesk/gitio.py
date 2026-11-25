"""Git calls used by diffdesk.

Every call passes a list of arguments to ``git`` (never a shell string) and
validates revisions and paths first. Calls that handle many subjects read
their input from standard input, so the number of git processes grows with
the number of repositories, not with the number of subjects.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

GIT_TIMEOUT_S = 60
HEX_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_HEX_SHA_BYTES = re.compile(rb"[0-9a-f]{40}|[0-9a-f]{64}")
_REF_FORBIDDEN = re.compile(r"[\s\x00-\x1f\x7f:]")
_PATH_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f\\]")
_TREE_PAIR_LINE = re.compile(rb"[0-9a-f]{40,64} [0-9a-f]{40,64}\n")
_HUNK_HEADER = re.compile(rb"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

Numstat = dict[str, list[int | None]]


class GitError(RuntimeError):
    """A git command failed, timed out, or could not start."""


class InvalidInput(ValueError):
    """A subject ID, package, revision or path fails validation."""


def check_ref(ref: Any) -> str:
    """Return `ref` if it is safe to pass to git as one revision.

    Parameters
    ----------
    ref : Any
        Value from a subject file or a request.

    Returns
    -------
    str
        The unchanged revision.

    Raises
    ------
    InvalidInput
        If `ref` is not a non-empty string, starts with ``-``, contains
        ``..``, ``:``, white space or a control character.
    """
    if (
        not isinstance(ref, str)
        or not ref
        or ref.startswith("-")
        or ".." in ref
        or _REF_FORBIDDEN.search(ref)
    ):
        raise InvalidInput(f"Invalid git revision: {ref!r}")
    return ref


def check_path(path: Any) -> str:
    """Return `path` if it is a relative POSIX path in normal form.

    Parameters
    ----------
    path : Any
        Value from a subject file or a request.

    Returns
    -------
    str
        The unchanged path.

    Raises
    ------
    InvalidInput
        If `path` is empty, absolute, starts with ``~``, has a ``..``
        component, a backslash or a control character, or is not in normal
        form (for example ``a//b``, ``./a`` or ``a/``).
    """
    if not isinstance(path, str) or not path or _PATH_FORBIDDEN.search(path):
        raise InvalidInput(f"Invalid path: {path!r}")
    pure = PurePosixPath(path)
    if pure.is_absolute() or path.startswith("~") or ".." in pure.parts or pure.as_posix() != path:
        raise InvalidInput(f"Path must be relative, normal and without '..': {path!r}")
    return path


def run_git(repo: str, args: list[str], stdin: bytes | None = None) -> bytes:
    """Run ``git -C <repo> <args>`` and return its standard output.

    Parameters
    ----------
    repo : str
        Repository folder.
    args : list of str
        Git subcommand and its arguments.
    stdin : bytes, optional
        Data for the standard input of git.

    Returns
    -------
    bytes
        Standard output.

    Raises
    ------
    GitError
        If `repo` is not a folder, git cannot start, git times out after
        ``GIT_TIMEOUT_S`` seconds, or git exits with a nonzero code.
    """
    if not Path(repo).is_dir():
        raise GitError(f"Repository folder not found: {repo}")
    try:
        res = subprocess.run(
            ["git", "--literal-pathspecs", "-C", repo, *args],
            input=stdin,
            capture_output=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} took more than {GIT_TIMEOUT_S} s in {repo}") from exc
    except OSError as exc:
        raise GitError(f"Cannot run git: {exc}") from exc
    if res.returncode != 0:
        msg = res.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"git {args[0]} failed in {repo}: {msg}")
    return res.stdout


def resolve(repo: str, revs: list[str], kind: str) -> dict[str, str | None]:
    """Map each revision to the ID of the object of type `kind` it names.

    Parameters
    ----------
    repo : str
        Repository folder.
    revs : list of str
        Revisions, already checked with `check_ref`.
    kind : {"commit", "tree"}
        Object type to peel each revision to.

    Returns
    -------
    dict
        Revision to object ID, or to `None` if git cannot find it.
    """
    uniq = list(dict.fromkeys(revs))
    if not uniq:
        return {}
    data = "".join(f"{rev}^{{{kind}}}\n" for rev in uniq).encode()
    lines = run_git(repo, ["cat-file", "--batch-check=%(objectname)"], data).decode().splitlines()
    if len(lines) != len(uniq):
        raise GitError(f"git cat-file gave {len(lines)} lines for {len(uniq)} revisions")
    return {
        rev: (line if HEX_SHA.fullmatch(line) else None)
        for rev, line in zip(uniq, lines, strict=True)
    }


def numstat_many(repo: str, pairs: list[tuple[str, str]]) -> dict[tuple[str, str], Numstat | None]:
    """Return the added and deleted line counts per file for many diffs.

    One ``git cat-file`` call resolves every revision to a tree, then one
    ``git diff-tree --stdin`` call compares all tree pairs.

    Parameters
    ----------
    repo : str
        Repository folder.
    pairs : list of (str, str)
        ``(base, head)`` revisions, already checked with `check_ref`.

    Returns
    -------
    dict
        ``(base, head)`` to ``{path: [added, deleted]}``, where both counts
        are `None` for a binary file. A pair maps to `None` if git cannot
        find one of its revisions.
    """
    pairs = list(dict.fromkeys(pairs))
    trees = resolve(repo, [rev for pair in pairs for rev in pair], "tree")
    result: dict[tuple[str, str], Numstat | None] = dict.fromkeys(pairs)
    todo = [(b, h) for b, h in pairs if trees[b] and trees[h]]
    if not todo:
        return result
    data = "".join(f"{trees[b]} {trees[h]}\n" for b, h in todo).encode()
    out = run_git(
        repo,
        ["diff-tree", "--stdin", "-r", "-z", "--numstat", "--no-renames", "--always"],
        data,
    )
    # Each input line is echoed as a header that ends with a newline; each
    # file line ends with NUL.
    blocks: list[Numstat] = []
    pos = 0
    while pos < len(out):
        header = _TREE_PAIR_LINE.match(out, pos)
        if header:
            blocks.append({})
            pos = header.end()
            continue
        end = out.index(b"\0", pos)
        added, deleted, path = out[pos:end].split(b"\t", 2)
        pos = end + 1
        if not blocks:
            raise GitError("git diff-tree printed a file before the first tree pair")
        blocks[-1][path.decode("utf-8", errors="replace")] = [
            None if added == b"-" else int(added),
            None if deleted == b"-" else int(deleted),
        ]
    if len(blocks) != len(todo):
        raise GitError(f"git diff-tree gave {len(blocks)} results for {len(todo)} tree pairs")
    result.update(zip(todo, blocks, strict=True))
    return result


def commit_order(repo: str, heads: list[str]) -> dict[str, int]:
    """Return the position of each head in topological commit order.

    Parameters
    ----------
    repo : str
        Repository folder.
    heads : list of str
        Revisions, already checked with `check_ref`.

    Returns
    -------
    dict
        Revision to its 0-based index in ``git rev-list --reverse
        --topo-order`` of all the heads (parents before children).
        Revisions that git cannot find are absent.
    """
    commits = resolve(repo, heads, "commit")
    shas = sorted({sha for sha in commits.values() if sha})
    if not shas:
        return {}
    out = run_git(
        repo,
        ["rev-list", "--reverse", "--topo-order", "--stdin"],
        ("\n".join(shas) + "\n").encode(),
    )
    position = {sha: i for i, sha in enumerate(out.decode().split())}
    return {rev: position[sha] for rev, sha in commits.items() if sha in position}


def read_blobs(repo: str, specs: list[tuple[str, str]]) -> list[bytes | None]:
    """Return the content of each ``(revision, path)`` with one git call.

    Parameters
    ----------
    repo : str
        Repository folder.
    specs : list of (str, str)
        Revisions checked with `check_ref` and paths checked with
        `check_path`.

    Returns
    -------
    list
        File content, or `None` if the path is absent at that revision or
        is not a regular file.
    """
    if not specs:
        return []
    data = "".join(f"{rev}:{path}\n" for rev, path in specs).encode()
    out = run_git(repo, ["cat-file", "--batch"], data)
    result: list[bytes | None] = []
    pos = 0
    for _ in specs:
        end = out.index(b"\n", pos)
        parts = out[pos:end].split(b" ")
        pos = end + 1
        if len(parts) == 3 and _HEX_SHA_BYTES.fullmatch(parts[0]) and parts[2].isdigit():
            size = int(parts[2])
            body = out[pos : pos + size]
            pos += size + 1
            result.append(body if parts[1] == b"blob" else None)
        else:
            result.append(None)
    return result


def diff_hunks(
    repo: str, base: str, head: str, path: str, context: int, ignore_space: bool
) -> tuple[list[dict[str, Any]], bool]:
    """Return the hunks of ``git diff -U<context> base head -- path``.

    Parameters
    ----------
    repo : str
        Repository folder.
    base, head : str
        Revisions, already checked with `check_ref`.
    path : str
        File path, already checked with `check_path`.
    context : int
        Number of unchanged lines around each change.
    ignore_space : bool
        Pass ``-w`` to git.

    Returns
    -------
    hunks : list of dict
        ``{old_start, old_lines, new_start, new_lines, lines}``. Each line
        starts with ``+``, ``-``, a space, or ``\\`` (git's "No newline at
        end of file" note).
    binary : bool
        `True` if git reports the file as binary.
    """
    args = [
        "diff",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        f"-U{context}",
    ]
    if ignore_space:
        args.append("-w")
    out = run_git(repo, [*args, base, head, "--", path])
    hunks: list[dict[str, Any]] = []
    binary = False
    current: dict[str, Any] | None = None
    for raw in out.split(b"\n"):
        match = _HUNK_HEADER.match(raw)
        if match:
            old_start, old_lines, new_start, new_lines = match.groups()
            current = {
                "old_start": int(old_start),
                "old_lines": 1 if old_lines is None else int(old_lines),
                "new_start": int(new_start),
                "new_lines": 1 if new_lines is None else int(new_lines),
                "lines": [],
            }
            hunks.append(current)
        elif current is None:
            binary = binary or raw.startswith(b"Binary files ")
        elif raw[:1] in (b"+", b"-", b" ", b"\\"):
            current["lines"].append(raw.decode("utf-8", errors="replace"))
    return hunks, binary


def current_branch(repo: str) -> str | None:
    """Return the short name of the branch checked out in `repo`.

    Returns
    -------
    str or None
        `None` if HEAD is detached or `repo` is not a git repository.
    """
    try:
        name = run_git(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"]).decode().strip()
    except GitError:
        return None
    return name or None


def toplevel(folder: str) -> str | None:
    """Return the top folder of the work tree that contains `folder`, or `None`."""
    try:
        return run_git(folder, ["rev-parse", "--show-toplevel"]).decode().strip() or None
    except GitError:
        return None


def first_commit(repo: str) -> str | None:
    """Return the oldest root commit reachable from HEAD, or `None` if there is no commit."""
    try:
        out = run_git(repo, ["rev-list", "--max-parents=0", "--reverse", "HEAD"]).decode()
    except GitError:
        return None
    return out.split()[0] if out.split() else None


def empty_tree(repo: str) -> str:
    """Return the ID of the empty tree in the object format of `repo`."""
    return run_git(repo, ["hash-object", "-t", "tree", "--stdin"], b"").decode().strip()
