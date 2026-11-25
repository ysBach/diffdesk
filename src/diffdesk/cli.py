"""Command line: ``diffdesk serve``, ``diffdesk init`` and ``diffdesk validate``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import webbrowser
from pathlib import Path
from typing import Any

from . import __version__, gitio
from .config import CONFIG_NAME, ConfigError, load_config
from .index import DEFAULT_PACKAGE, parse_subject
from .server import DiffdeskServer

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent / "subject.schema.json"

CONFIG_TEMPLATE = """\
# diffdesk review root. Every value is optional.
title = {title}

[paths]
subjects = "subjects"
reviews = "reviews.json"
edits = "edits"
cache = ".diffdesk-cache"

# [packages.mypackage]
# depends_on = ["otherpackage"]

[ui]
theme = "system"
editor = "codemirror"
"""

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


def cmd_validate(root: Path) -> int:
    """Check every subject file of a review root and print the problems.

    Returns
    -------
    int
        0 if no problem was found, else 1. Warnings do not change it.
    """
    config = load_config(root)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    problems: list[str] = []
    warnings: list[str] = []
    owners: dict[tuple[str, str], Path] = {}
    packages_of: dict[str, list[str]] = {}
    files = subject_files(config.subjects)
    for path in files:
        rel = (
            path.relative_to(config.root).as_posix() if path.is_relative_to(config.root) else path
        )
        try:
            data = json.loads(path.read_bytes())
        except (OSError, ValueError) as exc:
            problems.append(f"{rel}: cannot read JSON: {exc}")
            continue
        errors = schema_errors(data, schema, schema, "$")
        if isinstance(data, dict) and (data.get("base") is None) != (data.get("head") is None):
            errors.append("'base' and 'head' must both be set or both be null")
        problems += [f"{rel}: {e}" for e in errors]
        parts = path.relative_to(config.subjects).parts
        try:
            subject = parse_subject(path, parts[0] if len(parts) > 1 else DEFAULT_PACKAGE)
        except ValueError as exc:
            if not errors:
                problems.append(f"{rel}: {exc}")
            continue
        if subject.key in owners:
            first = owners[subject.key].relative_to(config.root).as_posix()
            problems.append(f"{rel}: same package and ID as {first}")
            continue
        owners[subject.key] = path
        packages_of.setdefault(subject.id, []).append(subject.package)
    for sid, packages in sorted(packages_of.items()):
        if len(packages) > 1:
            warnings.append(
                f"ID {sid} is used in packages {', '.join(packages)}; "
                "API calls for it need 'package'"
            )
    for line in problems:
        print(f"problem: {line}")
    for line in warnings:
        print(f"warning: {line}")
    print(f"{len(files)} subject files, {len(problems)} problems, {len(warnings)} warnings")
    return 1 if problems else 0


def cmd_init(root: Path) -> int:
    """Create the layout of a review root and, if possible, one example subject.

    The example points at the first commit of the git repository that holds
    `root` and is written only if that repository has a commit and the
    subjects folder has no subject yet.

    Returns
    -------
    int
        Always 0.
    """
    root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    config_path = root / CONFIG_NAME
    if not config_path.exists():
        config_path.write_text(
            CONFIG_TEMPLATE.format(title=json.dumps(root.resolve().name)), encoding="utf-8"
        )
        created.append(config_path)
    config = load_config(root)
    for folder in (config.subjects, config.edits):
        if not folder.exists():
            folder.mkdir(parents=True)
            created.append(folder)
    repo = gitio.toplevel(str(config.root))
    head = gitio.first_commit(repo) if repo else None
    if repo and head and not subject_files(config.subjects):
        example = config.subjects / "example" / "EXAMPLE-1.json"
        subject = {
            "id": "EXAMPLE-1",
            "package": "example",
            "title": f"First commit of {Path(repo).name}",
            "priority": "P3",
            "origin": "new",
            "recommended": "accept",
            "summary": "Example made by `diffdesk init`. Delete it when you add real subjects.",
            "why": (
                "**Problem**\nNone. This subject shows the format.\n\n"
                "**Fix logic**\n`base` is the empty tree, so the diff shows every file "
                "of the first commit."
            ),
            "repo": repo,
            "base": gitio.empty_tree(repo),
            "head": head,
            "files": [],
            "tests": "not run",
        }
        example.parent.mkdir(parents=True, exist_ok=True)
        example.write_text(json.dumps(subject, indent=2) + "\n", encoding="utf-8")
        created.append(example)
    for path in created:
        print(f"created {path}")
    if not created:
        print(f"nothing to create in {config.root}")
    return 0


def cmd_serve(root: Path, host: str, port: int, open_browser: bool) -> int:
    """Run the HTTP server until interrupted."""
    config = load_config(root)
    try:
        httpd = DiffdeskServer(config, host, port)
    except OSError as exc:
        print(f"diffdesk: cannot listen on {host}:{port}: {exc}", file=sys.stderr)
        return 1
    bound = httpd.server_address[1]
    shown = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    url = f"http://{f'[{shown}]' if ':' in shown else shown}:{bound}/"
    print(f"diffdesk {__version__} on {url} (root: {config.root})", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and run one subcommand.

    Returns
    -------
    int
        Exit code.
    """
    parser = argparse.ArgumentParser(prog="diffdesk", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"diffdesk {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every request")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="serve the review desk on a local port")
    serve.add_argument("root", nargs="?", default=".", help="review root (default: .)")
    serve.add_argument("--host", default="127.0.0.1", help="address to bind (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8765, help="port (default: 8765; 0: any)")
    serve.add_argument("--open", action="store_true", help="open the page in the browser")
    init = sub.add_parser("init", help="create a review root")
    init.add_argument("root", nargs="?", default=".", help="review root (default: .)")
    validate = sub.add_parser("validate", help="check every subject file")
    validate.add_argument("root", nargs="?", default=".", help="review root (default: .)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    root = Path(args.root)
    try:
        if args.command == "serve":
            return cmd_serve(root, args.host, args.port, args.open)
        if args.command == "init":
            return cmd_init(root)
        return cmd_validate(root)
    except ConfigError as exc:
        print(f"diffdesk: {exc}", file=sys.stderr)
        return 2
