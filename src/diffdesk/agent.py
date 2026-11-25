"""Functions for coding agents: write a subject for a commit and read reviews back.

The ``diffdesk add``, ``diffdesk reviews`` and ``diffdesk guide`` commands use
these functions. Each function checks its own input, so other front ends can
call it directly.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import gitio
from .config import Config
from .gitio import GitError, InvalidInput
from .index import (
    DEFAULT_PACKAGE,
    STATUSES,
    Index,
    check_name,
    parse_subject,
    write_atomic,
)

PACKAGE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = PACKAGE_DIR / "subject.schema.json"
GUIDE_PATH = PACKAGE_DIR / "agent_guide.md"

_TYPES: dict[str, Any] = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def schema_errors(
    value: Any, schema: dict[str, Any], root: dict[str, Any], where: str
) -> list[str]:
    """Check `value` against a JSON Schema and return the problems found.

    Only the keywords that ``subject.schema.json`` uses are supported:
    ``$ref`` (inside the same document), ``type``, ``enum``, ``minLength``,
    ``pattern``, ``items``, ``required``, ``properties`` and
    ``additionalProperties``. Other keywords are ignored.

    Parameters
    ----------
    value : Any
        Decoded JSON value.
    schema : dict
        Schema (or subschema) to check against.
    root : dict
        The whole schema document, for ``$ref``.
    where : str
        Location of `value`, used in the messages (for example ``$.files[0]``).

    Returns
    -------
    list of str
        One message per problem. Empty if `value` is valid.
    """
    errors: list[str] = []
    if "$ref" in schema:
        target: Any = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        errors += schema_errors(value, target, root, where)
    types = schema.get("type")
    if types is not None:
        types = [types] if isinstance(types, str) else types
        if not any(_TYPES[t](value) for t in types):
            return [
                *errors,
                f"{where}: must be {' or '.join(types)}, not {json.dumps(value)[:60]}",
            ]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: must be one of {schema['enum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{where}: must not be empty")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{where}: {value!r} does not match {schema['pattern']}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errors += schema_errors(item, schema["items"], root, f"{where}[{i}]")
    if isinstance(value, dict):
        errors += [
            f"{where}: missing field '{key}'"
            for key in schema.get("required", [])
            if key not in value
        ]
        props = schema.get("properties", {})
        for key, item in value.items():
            if key in props:
                errors += schema_errors(item, props[key], root, f"{where}.{key}")
            elif schema.get("additionalProperties", True) is False:
                errors.append(f"{where}: unknown field '{key}'")
    return errors


def subject_files(folder: Path) -> list[Path]:
    """Return every ``*.json`` file below `folder`, without hidden files and folders."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        found += [
            Path(dirpath, f) for f in filenames if f.endswith(".json") and not f.startswith(".")
        ]
    return sorted(found)


def create_subject(
    config: Config,
    *,
    repo: str | Path,
    subject_id: str,
    title: str,
    commit: str | None = None,
    base: str | None = None,
    head: str | None = None,
    package: str | None = None,
    summary: str | None = None,
    why: str | None = None,
    priority: str | None = None,
    recommended: str | None = None,
    tags: Iterable[str] | None = None,
    branch: str | None = None,
    force: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """Write ``<subjects>/<package>/<subject_id>.json`` for a commit or a commit range.

    The full commit IDs, the absolute path of the work tree and the list of
    changed files come from git. A renamed file is listed with its old and
    its new path, and a deleted file with its old path, because the diff is
    made without rename detection.

    Parameters
    ----------
    config : Config
        Settings of the review root. The root folder must exist.
    repo : str or Path
        A folder inside the git work tree.
    subject_id : str
        Subject ID: letters, digits, ``.``, ``_`` and ``-``, starting with a
        letter or digit, without ``..``.
    title : str
        One-line title.
    commit : str, optional
        Commit to review against its first parent. Default: ``HEAD`` if
        `base` and `head` are not given. A commit without a parent is
        compared with the empty tree.
    base, head : str, optional
        Old and new side of the diff. Give both or neither, and not with
        `commit`.
    package : str, optional
        Package name. Default: the name of the work tree folder.
    summary, why, priority, recommended, branch : str, optional
        Subject fields of the same name. A field that is `None` is left out.
    tags : iterable of str, optional
        Free labels.
    force : bool, optional
        Replace the subject file if it exists. Default: `False`.

    Returns
    -------
    path : Path
        Absolute path of the written file.
    subject : dict
        The subject as written.

    Raises
    ------
    InvalidInput
        If an argument is not valid, `repo` is not inside a git work tree, a
        revision is not a commit, the file would be outside the review root,
        or the subject does not match ``subject.schema.json``.
    FileNotFoundError
        If the review root does not exist.
    FileExistsError
        If the subject file exists and `force` is `False`, or another file
        holds a subject with the same package and ID.
    GitError
        If git fails.
    """
    if not config.root.is_dir():
        raise FileNotFoundError(f"Review root not found: {config.root} (run 'diffdesk init')")
    sid = check_name(subject_id, "subject ID")
    if commit is not None and (base is not None or head is not None):
        raise InvalidInput("Give a commit, or a base and a head, not both")
    if (base is None) != (head is None):
        raise InvalidInput("Give both a base and a head")

    top = gitio.toplevel(str(Path(repo).resolve()))
    if top is None:
        raise InvalidInput(f"Not inside a git work tree: {repo}")
    repo_path = Path(top).resolve()
    work_tree = str(repo_path)
    if package is None:
        try:
            package = check_name(repo_path.name, "package")
        except InvalidInput:
            raise InvalidInput(
                f"Folder name {repo_path.name!r} is not a valid package name; give a package"
            ) from None
    else:
        package = check_name(package, "package")

    target = (config.subjects / package / f"{sid}.json").resolve()
    if not target.is_relative_to(config.root):
        raise InvalidInput(f"Subject file would be outside the review root: {target}")
    if target.exists() and not force:
        raise FileExistsError(f"Subject file exists: {target}. Use --force to replace it.")
    for path in subject_files(config.subjects):
        if path.resolve() == target:
            continue
        parts = path.relative_to(config.subjects).parts
        try:
            other = parse_subject(path, parts[0] if len(parts) > 1 else DEFAULT_PACKAGE)
        except (OSError, ValueError):
            continue
        if other.key == (package, sid):
            raise FileExistsError(f"{path} already holds subject {sid!r} of package {package!r}")

    revs = ["HEAD" if commit is None else commit] if base is None else [base, head]
    shas = gitio.resolve(work_tree, [gitio.check_ref(r) for r in revs], "commit")
    missing = [rev for rev, sha in shas.items() if sha is None]
    if missing:
        raise InvalidInput(f"Not a commit in {work_tree}: {', '.join(missing)}")
    if base is None:
        head_sha = shas[revs[0]]
        parent = gitio.resolve(work_tree, [f"{head_sha}^"], "commit")[f"{head_sha}^"]
        base_sha = parent or gitio.empty_tree(work_tree)
    else:
        base_sha, head_sha = shas[base], shas[head]
    counts = gitio.numstat_many(work_tree, [(base_sha, head_sha)])[(base_sha, head_sha)]
    if counts is None:
        raise GitError(f"Cannot list the files changed between {base_sha} and {head_sha}")
    files = [gitio.check_path(p) for p in sorted(counts)]

    subject: dict[str, Any] = {"id": sid, "package": package, "title": title}
    optional = {"priority": priority, "recommended": recommended, "summary": summary, "why": why}
    subject.update({key: value for key, value in optional.items() if value is not None})
    subject.update(repo=work_tree, base=base_sha, head=head_sha, files=files)
    if branch is not None:
        subject["branch"] = branch
    if tags:
        subject["tags"] = list(tags)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = schema_errors(subject, schema, schema, "$")
    if errors:
        raise InvalidInput("; ".join(errors))

    write_atomic(target, (json.dumps(subject, indent=2, ensure_ascii=False) + "\n").encode())
    return target, subject


def collect_reviews(
    config: Config,
    *,
    statuses: Iterable[str] | None = None,
    package: str | None = None,
    subject_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return the review state of each subject, in the order of the browser tree.

    Parameters
    ----------
    config : Config
        Settings of the review root.
    statuses : iterable of str, optional
        Keep only subjects with one of these statuses (see
        ``index.STATUSES``). Default: every status.
    package : str, optional
        Keep only subjects of this package.
    subject_id : str, optional
        Keep only subjects with this ID (one per package that uses it).

    Returns
    -------
    list of dict
        One dict per subject: ``id``, ``package``, ``title``, ``status``,
        and from the last review record ``verdict``, ``comment`` and ``at``
        (all `None` if there is no record; ``at`` falls back to the
        ``time`` field of old records). Then ``history_count`` (number of
        review records), ``repo`` and ``head`` of the subject, and ``edits``:
        ``[{path, edit_file}]``, where ``path`` is the file path in the
        repository and ``edit_file`` the absolute path of the saved edit.

    Raises
    ------
    InvalidInput
        If a status is not one of ``index.STATUSES``.
    ValueError
        If ``reviews.json`` is not a JSON list.
    """
    wanted = set(statuses or ())
    unknown = wanted - set(STATUSES)
    if unknown:
        raise InvalidInput(f"Unknown status {sorted(unknown)}; use {list(STATUSES)}")
    snap = Index(config).snapshot()
    result: list[dict[str, Any]] = []
    for s in snap.subjects:
        status = snap.status[s.key]
        if (
            (wanted and status not in wanted)
            or (package and s.package != package)
            or (subject_id and s.id != subject_id)
        ):
            continue
        edits: list[dict[str, str]] = []
        if s.key in snap.edited:
            folder = config.edits / s.package / s.id
            for dirpath, _dirnames, filenames in os.walk(folder):
                for name in filenames:
                    # Temporary files of `write_atomic`.
                    if name.startswith(".") and name.endswith(".tmp"):
                        continue
                    edit_file = Path(dirpath, name)
                    edits.append(
                        {
                            "path": edit_file.relative_to(folder).as_posix(),
                            "edit_file": str(edit_file),
                        }
                    )
        rec = snap.review.get(s.key) or {}
        result.append(
            {
                "id": s.id,
                "package": s.package,
                "title": s.title,
                "status": status,
                "verdict": rec.get("verdict"),
                "comment": rec.get("comment"),
                "at": rec.get("at", rec.get("time")),
                "history_count": snap.history_count.get(s.key, 0),
                "repo": s.repo,
                "head": s.head,
                "edits": sorted(edits, key=lambda e: e["path"]),
            }
        )
    return result
