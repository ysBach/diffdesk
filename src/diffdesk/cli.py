"""Command line: serve, init, validate, add, reviews, guide and mcp."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import webbrowser
from pathlib import Path

from . import __version__, gitio
from .agent import (
    GUIDE_PATH,
    collect_reviews,
    create_subject,
    subject_files,
    validate_subjects,
)
from .config import CONFIG_NAME, ConfigError, load_config
from .gitio import GitError, InvalidInput
from .index import STATUSES
from .mcp import serve_stdio
from .server import DiffdeskServer

log = logging.getLogger(__name__)

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


def cmd_validate(root: Path) -> int:
    """Check every subject file of a review root and print the problems.

    Returns
    -------
    int
        0 if no problem was found, else 1. Warnings do not change it.
    """
    result = validate_subjects(load_config(root))
    problems, warnings = result["problems"], result["warnings"]
    for line in problems:
        print(f"problem: {line}")
    for line in warnings:
        print(f"warning: {line}")
    print(f"{result['files']} subject files, {len(problems)} problems, {len(warnings)} warnings")
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


def cmd_add(root: Path, args: argparse.Namespace) -> int:
    """Write one subject file for a git commit and print where it went.

    Returns
    -------
    int
        Always 0. Errors raise.
    """
    why = args.why
    if args.why_file == "-":
        # Bytes, then UTF-8: text stdin uses the locale encoding on Windows.
        why = sys.stdin.buffer.read().decode("utf-8")
    elif args.why_file is not None:
        why = Path(args.why_file).read_text(encoding="utf-8")
    path, subject = create_subject(
        load_config(root),
        repo=Path(args.repo),
        subject_id=args.id,
        title=args.title,
        commit=args.commit,
        base=args.base,
        head=args.head,
        package=args.package,
        summary=args.summary,
        why=why,
        priority=args.priority,
        recommended=args.recommended,
        tags=args.tags,
        branch=args.branch,
        force=args.force,
    )
    if args.json:
        print(json.dumps(subject, indent=2))
    else:
        print(
            f"wrote {path} (changed paths: {len(subject['files'])}, "
            f"{subject['base'][:12]}..{subject['head'][:12]})"
        )
    return 0


def cmd_reviews(
    root: Path, statuses: list[str] | None, package: str | None, subject_id: str | None, fmt: str
) -> int:
    """Print the review state of the subjects as JSON or as a short table.

    Returns
    -------
    int
        Always 0. Errors raise.
    """
    items = collect_reviews(
        load_config(root), statuses=statuses, package=package, subject_id=subject_id
    )
    if fmt == "json":
        print(json.dumps(items, indent=2))
        return 0
    rows = [("STATUS", "SUBJECT", "EDITS", "AT", "TITLE")]
    rows += [
        (
            item["status"],
            f"{item['package']}/{item['id']}",
            str(len(item["edits"])),
            str(item["at"] or "-"),
            item["title"],
        )
        for item in items
    ]
    widths = [max(len(row[col]) for row in rows) for col in range(4)]
    for row in rows:
        cells = [cell.ljust(w) for cell, w in zip(row[:4], widths, strict=True)]
        print("  ".join([*cells, row[4]]))
    print(f"{len(items)} subjects")
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

    add = sub.add_parser("add", help="write a subject file for a git commit")
    add.add_argument("root", nargs="?", default=".", help="review root (default: .)")
    add.add_argument("--repo", default=".", help="folder inside the git work tree (default: .)")
    add.add_argument("--commit", help="commit to review against its first parent (default: HEAD)")
    add.add_argument("--base", help="old side of the diff; use with --head")
    add.add_argument("--head", help="new side of the diff; use with --base")
    add.add_argument("--id", required=True, help="subject ID, unique inside its package")
    add.add_argument("--title", required=True, help="one-line title")
    add.add_argument("--package", help="package name (default: name of the work tree folder)")
    add.add_argument("--summary", help="one sentence: what is wrong and what the change does")
    why = add.add_mutually_exclusive_group()
    why.add_argument("--why", help="reasons for the change, in Markdown")
    why.add_argument(
        "--why-file", metavar="PATH", help="read the reasons from PATH ('-': standard input)"
    )
    add.add_argument("--priority", help="for example P1, P2 or P3")
    add.add_argument("--recommended", help="suggested verdict, for example accept")
    add.add_argument("--tag", action="append", dest="tags", help="free label (repeatable)")
    add.add_argument("--branch", help="branch name for the tree (default: the checked-out one)")
    add.add_argument("--force", action="store_true", help="replace an existing subject file")
    add.add_argument("--json", action="store_true", help="print the written subject as JSON")

    reviews = sub.add_parser("reviews", help="print verdicts and edits")
    reviews.add_argument("root", nargs="?", default=".", help="review root (default: .)")
    reviews.add_argument(
        "--status",
        action="append",
        dest="statuses",
        choices=STATUSES,
        help="keep only subjects with this status (repeatable)",
    )
    reviews.add_argument("--package", help="keep only subjects of this package")
    reviews.add_argument("--id", help="keep only subjects with this ID")
    reviews.add_argument(
        "--format", choices=("json", "text"), default="json", help="output (default: json)"
    )

    sub.add_parser("guide", help="print the guide for coding agents")
    mcp = sub.add_parser("mcp", help="run an MCP server on standard input and output")
    mcp.add_argument(
        "--root", help="review root (default: $DIFFDESK_ROOT, else the current folder)"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.command == "guide":
        sys.stdout.write(GUIDE_PATH.read_text(encoding="utf-8"))
        return 0
    if args.command == "mcp":
        return serve_stdio(Path(args.root or os.environ.get("DIFFDESK_ROOT") or "."))
    root = Path(args.root)
    try:
        if args.command == "serve":
            return cmd_serve(root, args.host, args.port, args.open)
        if args.command == "init":
            return cmd_init(root)
        if args.command == "add":
            return cmd_add(root, args)
        if args.command == "reviews":
            return cmd_reviews(root, args.statuses, args.package, args.id, args.format)
        return cmd_validate(root)
    except ConfigError as exc:
        print(f"diffdesk: {exc}", file=sys.stderr)
        return 2
    except (InvalidInput, GitError, OSError, ValueError) as exc:
        print(f"diffdesk: {exc}", file=sys.stderr)
        return 1
