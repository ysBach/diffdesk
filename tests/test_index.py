"""Subject index: order inside a branch, file cache, numstat cache."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from conftest import Repo, bump_mtime, commit, write_subject

from diffdesk import gitio, index
from diffdesk.config import load_config
from diffdesk.index import Index, natural_key


def test_natural_key_compares_numbers() -> None:
    assert sorted(["SB-12", "SB-2", "SB-1a", "SB-1"], key=natural_key) == [
        "SB-1",
        "SB-1a",
        "SB-2",
        "SB-12",
    ]


def test_order_inside_branch(tmp_path: Path, repo: Repo) -> None:
    """Commit order first, then `order`, then natural ID order; ties use the same rule."""
    root = tmp_path / "r"
    c = repo.commits
    base = {"repo": str(repo.path), "branch": "x"}
    write_subject(root, "p", {**base, "id": "S-10", "title": "t", "base": c[0], "head": c[1]})
    write_subject(root, "p", {**base, "id": "S-9", "title": "t", "base": c[1], "head": c[2]})
    write_subject(
        root, "p", {**base, "id": "T-2", "title": "t", "base": c[1], "head": c[2], "order": 1}
    )
    write_subject(root, "p", {**base, "id": "N-2", "title": "t", "base": None, "head": None})
    write_subject(
        root, "p", {**base, "id": "N-1", "title": "t", "base": None, "head": None, "order": 5}
    )
    write_subject(
        root, "p", {**base, "id": "N-3", "title": "t", "base": None, "head": None, "order": 2}
    )
    snap = Index(load_config(root)).snapshot()
    assert [s.id for s in snap.subjects] == ["S-10", "T-2", "S-9", "N-3", "N-1", "N-2"]


def test_branch_defaults_to_checked_out_branch(tmp_path: Path, repo: Repo) -> None:
    root = tmp_path / "r"
    c = repo.commits
    write_subject(
        root, "p", {"id": "X", "title": "t", "repo": str(repo.path), "base": c[0], "head": c[1]}
    )
    write_subject(root, "p", {"id": "Y", "title": "t"})
    snap = Index(load_config(root)).snapshot()
    assert {s.id: s.branch for s in snap.subjects} == {"X": "work", "Y": "(no repo)"}


def test_defaults_for_package_and_id(tmp_path: Path) -> None:
    root = tmp_path / "r"
    (root / "subjects").mkdir(parents=True)
    (root / "subjects" / "LOOSE-1.json").write_text(json.dumps({"title": "t"}))
    write_subject(root, "folder", {"id": "Z", "title": "t", "package": "named"})
    snap = Index(load_config(root)).snapshot()
    assert sorted(s.key for s in snap.subjects) == [("default", "LOOSE-1"), ("named", "Z")]


def test_bad_files_are_skipped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    root = tmp_path / "r"
    write_subject(root, "p", {"id": "OK", "title": "t"})
    (root / "subjects/p/broken.json").write_text("{")
    write_subject(root, "p", {"id": "../x", "title": "t"}, name="badid")
    write_subject(
        root, "p", {"id": "F", "title": "t", "base": "a", "head": "b", "files": ["../etc"]}
    )
    write_subject(root, "p", {"id": "OK", "title": "same key"}, name="second")
    (root / "subjects/p/.hidden.json").write_text("{")
    snap = Index(load_config(root)).snapshot()
    assert [s.id for s in snap.subjects] == ["OK"]
    assert snap.subjects[0].title == "t"
    assert "same package and ID" in caplog.text


def test_files_are_parsed_again_only_when_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "r"
    paths = [write_subject(root, "p", {"id": f"S-{i}", "title": "t"}) for i in range(3)]
    calls: list[Path] = []
    parse = index.parse_subject
    monkeypatch.setattr(index, "parse_subject", lambda p, d: calls.append(p) or parse(p, d))
    idx = Index(load_config(root))
    first = idx.snapshot()
    assert len(calls) == 3
    assert idx.snapshot() is first and len(calls) == 3
    paths[1].write_text(json.dumps({"id": "S-1", "title": "changed"}))
    bump_mtime(paths[1])
    snap = idx.snapshot()
    assert calls[3:] == [paths[1]]
    assert snap.by_key["p", "S-1"].title == "changed"
    write_subject(root, "p", {"id": "S-9", "title": "t"})
    paths[0].unlink()
    assert sorted(s.id for s in idx.snapshot().subjects) == ["S-1", "S-2", "S-9"]


def test_numstat_cache_on_disk(
    tmp_path: Path, repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "r"
    c = repo.commits
    write_subject(
        root, "p", {"id": "S", "title": "t", "repo": str(repo.path), "base": c[0], "head": c[1]}
    )
    write_subject(
        root,
        "p",
        {"id": "R", "title": "t", "repo": str(repo.path), "base": "work~3", "head": "work~2"},
    )
    config = load_config(root)
    idx = Index(config)
    counts = idx.numstat(idx.snapshot().subjects)
    expected = {"a.py": [1, 1], "b.txt": [2, 0]}
    assert counts == {("p", "S"): expected, ("p", "R"): expected}
    stored = json.loads((config.cache / "numstat.json").read_text())
    assert list(stored["entries"]) == [f"{repo.path}\n{c[0]}\n{c[1]}"]

    def no_git(*args: object) -> None:
        raise AssertionError("git must not run for cached diffs")

    monkeypatch.setattr(gitio, "numstat_many", no_git)
    fresh = Index(config)
    subject = fresh.snapshot().by_key["p", "S"]
    assert fresh.numstat([subject]) == {("p", "S"): expected}


def test_numstat_batches_one_call_per_repo(
    tmp_path: Path, repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "r"
    for i in range(3):
        c = commit(repo.path, {f"n{i}.txt": "1\n"}, f"n{i}")
        write_subject(
            root,
            "p",
            {"id": f"S-{i}", "title": "t", "repo": str(repo.path), "base": f"{c}~1", "head": c},
        )
    calls: list[int] = []
    many = gitio.numstat_many
    monkeypatch.setattr(gitio, "numstat_many", lambda r, p: calls.append(len(p)) or many(r, p))
    idx = Index(load_config(root))
    counts = idx.numstat(idx.snapshot().subjects)
    assert calls == [3]
    assert counts["p", "S-2"] == {"n2.txt": [1, 0]}


def test_missing_repo_gives_unknown_counts(tmp_path: Path) -> None:
    root = tmp_path / "r"
    write_subject(
        root,
        "p",
        {
            "id": "S",
            "title": "t",
            "repo": str(tmp_path / "gone"),
            "base": "a" * 40,
            "head": "b" * 40,
        },
    )
    idx = Index(load_config(root))
    snap = idx.snapshot()
    assert snap.subjects[0].branch == "HEAD"
    assert idx.numstat(snap.subjects) == {("p", "S"): None}


def test_write_atomic_keeps_mode(tmp_path: Path) -> None:
    target = tmp_path / "f.json"
    target.write_text("old")
    target.chmod(0o640)
    index.write_atomic(target, b"new")
    assert target.read_bytes() == b"new" and stat.S_IMODE(target.stat().st_mode) == 0o640
    fresh = tmp_path / "sub" / "g.json"
    index.write_atomic(fresh, b"x")
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o666 & ~index._UMASK
    assert [p.name for p in fresh.parent.iterdir()] == ["g.json"]


def test_dotfile_edit_counts_and_temp_file_does_not(tmp_path: Path) -> None:
    root = tmp_path / "r"
    write_subject(root, "p", {"id": "S", "title": "t"})
    write_subject(root, "p", {"id": "T", "title": "t"})
    (root / "edits/p/S/.github").mkdir(parents=True)
    (root / "edits/p/S/.github/ci.yml").write_text("x")
    (root / "edits/p/T").mkdir(parents=True)
    (root / "edits/p/T/.a.py.k2j3.tmp").write_text("x")
    assert Index(load_config(root)).snapshot().edited == {("p", "S")}
