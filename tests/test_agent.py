"""Commands for agents: add, reviews and guide."""

from __future__ import annotations

import io
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from conftest import Client, Repo, commit, git, write_subject

from diffdesk import gitio
from diffdesk.agent import GUIDE_PATH, collect_reviews, create_subject
from diffdesk.cli import main
from diffdesk.config import load_config
from diffdesk.gitio import InvalidInput
from diffdesk.server import DiffdeskServer


@pytest.fixture
def review(tmp_path: Path) -> Path:
    """An empty review root."""
    path = tmp_path / "review"
    path.mkdir()
    return path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_add_defaults_to_head(
    repo: Repo, review: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo.path)
    args = ["add", str(review), "--id", "R-1", "--title", "Binary", "--priority", "P2"]
    assert main([*args, "--tag", "x", "--tag", "y", "--summary", "adds img.bin"]) == 0
    path = review / "subjects" / "repo" / "R-1.json"
    assert "wrote" in capsys.readouterr().out
    subject = read_json(path)
    assert Path(subject.pop("repo")) == repo.path.resolve()
    assert subject == {
        "id": "R-1",
        "package": "repo",
        "title": "Binary",
        "priority": "P2",
        "summary": "adds img.bin",
        "base": repo.commits[2],
        "head": repo.commits[3],
        "files": ["b.txt", "img.bin"],
        "tags": ["x", "y"],
    }
    assert main(["validate", str(review)]) == 0


def test_add_commit_and_range(repo: Repo, review: Path) -> None:
    c = repo.commits
    common = ["add", str(review), "--repo", str(repo.path), "--title", "t"]
    assert main([*common, "--id", "ONE", "--commit", "HEAD~2", "--package", "p"]) == 0
    one = read_json(review / "subjects/p/ONE.json")
    assert (one["base"], one["head"], one["files"]) == (c[0], c[1], ["a.py", "b.txt"])
    assert main([*common, "--id", "TWO", "--base", c[1][:10], "--head", "work"]) == 0
    two = read_json(review / "subjects/repo/TWO.json")
    assert (two["base"], two["head"]) == (c[1], c[3])
    assert two["files"] == ["a.py", "b.txt", "img.bin"]


def test_add_lists_both_sides_of_rename_and_deleted_files(repo: Repo, review: Path) -> None:
    moved = {"a.py": None, "src/moved.py": "one\n2\nthree\nfour", "README.md": None}
    head = commit(repo.path, moved, "move and delete")
    assert "R100\ta.py\tsrc/moved.py" in git(repo.path, "show", "-M", "--name-status", head)
    config = load_config(review)
    path, subject = create_subject(config, repo=repo.path, subject_id="MV", title="Move")
    assert path == (review / "subjects/repo/MV.json").resolve()
    assert subject["head"] == head
    assert subject["files"] == ["README.md", "a.py", "src/moved.py"]

    collect_reviews(config)  # the index accepts the file
    srv = DiffdeskServer(config, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        client = Client("127.0.0.1", srv.server_address[1])
        files = client.request("GET", "/api/subject?id=MV").json()["files"]
        sizes = {f["path"]: (f["size_base"] is None, f["size_head"] is None) for f in files}
        assert sizes == {
            "README.md": (False, True),
            "a.py": (False, True),
            "src/moved.py": (True, False),
        }
        body = {"id": "MV", "path": "src/moved.py", "content": "edited\n"}
        assert client.request("POST", "/api/edit", body).status == 200
        body = {"id": "MV", "verdict": "change", "comment": "keep the old name"}
        assert client.request("POST", "/api/review", body).status == 200
    finally:
        srv.shutdown()
        srv.server_close()
    [item] = collect_reviews(config, statuses=["change"])
    assert (item["id"], item["comment"], item["head"]) == ("MV", "keep the old name", head)
    [edit] = item["edits"]
    assert edit["path"] == "src/moved.py"
    assert Path(edit["edit_file"]).read_text(encoding="utf-8") == "edited\n"


def test_add_root_commit_uses_empty_tree(repo: Repo, review: Path) -> None:
    config = load_config(review)
    _, subject = create_subject(
        config, repo=repo.path, subject_id="ROOT", title="First", commit=repo.commits[0]
    )
    assert subject["base"] == gitio.empty_tree(str(repo.path))
    assert subject["head"] == repo.commits[0]
    assert subject["files"] == ["README.md", "a.py"]


def test_add_overwrites_only_with_force(
    repo: Repo, review: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    common = ["add", str(review), "--repo", str(repo.path), "--id", "S"]
    path = review / "subjects/repo/S.json"
    assert main([*common, "--title", "first"]) == 0
    assert main([*common, "--title", "second"]) == 1
    assert "Subject file exists" in capsys.readouterr().err
    assert read_json(path)["title"] == "first"
    assert main([*common, "--title", "second", "--force"]) == 0
    assert read_json(path)["title"] == "second"


def test_add_refuses_same_key_in_other_file(
    repo: Repo, review: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_subject(review, "repo", {"id": "S", "title": "by hand"}, name="other")
    args = ["add", str(review), "--repo", str(repo.path), "--id", "S", "--title", "t"]
    assert main([*args, "--force"]) == 1
    assert "already holds subject 'S'" in capsys.readouterr().err
    assert not (review / "subjects/repo/S.json").exists()


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--id", "../x", "--title", "t"], "Invalid subject ID"),
        (["--id", "S", "--title", "t", "--package", "a/b"], "Invalid package"),
        (["--id", "S", "--title", ""], "$.title: must not be empty"),
        (["--id", "S", "--title", "t", "--branch", ""], "$.branch: must not be empty"),
        (["--id", "S", "--title", "t", "--commit", "nope"], "Not a commit"),
        (["--id", "S", "--title", "t", "--commit=-x"], "Invalid git revision"),
        (["--id", "S", "--title", "t", "--commit", "HEAD", "--base", "HEAD"], "not both"),
        (["--id", "S", "--title", "t", "--head", "HEAD"], "Give both a base and a head"),
    ],
)
def test_add_rejects_bad_input(
    repo: Repo,
    review: Path,
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
    message: str,
) -> None:
    assert main(["add", str(review), "--repo", str(repo.path), *extra]) == 1
    assert message in capsys.readouterr().err
    assert not (review / "subjects").exists()


def test_add_needs_repo_and_root(
    repo: Repo, tmp_path: Path, review: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert main(["add", str(review), "--repo", str(plain), "--id", "S", "--title", "t"]) == 1
    assert "Not inside a git work tree" in capsys.readouterr().err
    missing = tmp_path / "missing"
    assert main(["add", str(missing), "--repo", str(repo.path), "--id", "S", "--title", "t"]) == 1
    assert "Review root not found" in capsys.readouterr().err
    assert not missing.exists()


def test_add_package_from_folder_name_must_be_valid(
    tmp_path: Path, review: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spaced = tmp_path / "my repo"
    spaced.mkdir()
    git(spaced, "init", "-q", "-b", "main")
    commit(spaced, {"x.txt": "x\n"}, "first")
    args = ["add", str(review), "--repo", str(spaced), "--id", "S", "--title", "t"]
    assert main(args) == 1
    assert "give a package" in capsys.readouterr().err
    assert main([*args, "--package", "mine"]) == 0
    assert read_json(review / "subjects/mine/S.json")["files"] == ["x.txt"]


def test_add_reads_why_from_stdin_and_file(
    repo: Repo,
    review: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    text = "**Problem**\nThe ratio π/2 is wrong.\n"
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(text.encode("utf-8"))))
    common = ["add", str(review), "--repo", str(repo.path), "--title", "t"]
    assert main([*common, "--id", "IN", "--why-file", "-", "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == read_json(review / "subjects/repo/IN.json")
    assert printed["why"] == text

    why = tmp_path / "why.md"
    why.write_bytes(text.encode("utf-8"))
    assert main([*common, "--id", "FILE", "--why-file", str(why)]) == 0
    assert read_json(review / "subjects/repo/FILE.json")["why"] == text


def test_add_never_writes_outside_root(repo: Repo, tmp_path: Path, review: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, review / "subjects", target_is_directory=True)
    except OSError:
        pytest.skip("cannot create a symbolic link here")
    with pytest.raises(InvalidInput, match="outside the review root"):
        create_subject(load_config(review), repo=repo.path, subject_id="S", title="t")
    assert list(outside.iterdir()) == []


@pytest.fixture
def reviewed(root: Path) -> Path:
    """The `root` fixture with three review records and one saved edit of A-3."""
    records = [
        {"id": "DUP", "verdict": "reject", "comment": "old", "time": "2025-01-01T00:00:00"},
        {"id": "A-3", "package": "alpha", "verdict": "accept", "comment": "", "at": "t1"},
        {
            "id": "A-3",
            "package": "alpha",
            "verdict": "change",
            "comment": "rename x",
            "at": "2026-02-01T10:00:00+09:00",
        },
    ]
    (root / "reviews.json").write_text(json.dumps(records), encoding="utf-8")
    edit = root / "edits/alpha/A-3/a.py"
    edit.parent.mkdir(parents=True)
    edit.write_text("new\n", encoding="utf-8")
    (edit.parent / ".a.py.k2j3.tmp").write_text("partial", encoding="utf-8")
    return root


def reviews_json(root: Path, capsys: pytest.CaptureFixture[str], *args: str) -> list[Any]:
    assert main(["reviews", str(root), *args]) == 0
    return json.loads(capsys.readouterr().out)


def test_reviews_reports_last_verdict_and_edits(
    reviewed: Path, repo: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    items = reviews_json(reviewed, capsys)
    assert len(items) == 7
    by_key = {(i["package"], i["id"]): i for i in items}
    a3 = by_key["alpha", "A-3"]
    edit = a3.pop("edits")
    assert a3 == {
        "id": "A-3",
        "package": "alpha",
        "title": "Third",
        "status": "change",
        "verdict": "change",
        "comment": "rename x",
        "at": "2026-02-01T10:00:00+09:00",
        "history_count": 2,
        "repo": str(repo.path),
        "head": repo.commits[2],
    }
    assert [e["path"] for e in edit] == ["a.py"]
    assert Path(edit[0]["edit_file"]) == (reviewed / "edits/alpha/A-3/a.py").resolve()
    for package in ("beta", "gamma"):
        dup = by_key[package, "DUP"]
        assert (dup["status"], dup["at"], dup["history_count"]) == (
            "rejected",
            "2025-01-01T00:00:00",
            1,
        )
    pending = by_key["alpha", "A-12"]
    assert (pending["status"], pending["verdict"], pending["at"], pending["edits"]) == (
        "pending",
        None,
        None,
        [],
    )


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--status", "change", "--status", "rejected"], ["A-3", "DUP", "DUP"]),
        (["--package", "beta"], ["DUP", "B-1"]),
        (["--id", "DUP"], ["DUP", "DUP"]),
        (["--status", "pending", "--package", "alpha"], ["A-note", "A-12", "A-2"]),
        (["--id", "nothing"], []),
    ],
)
def test_reviews_filters(
    reviewed: Path, capsys: pytest.CaptureFixture[str], args: list[str], expected: list[str]
) -> None:
    assert [i["id"] for i in reviews_json(reviewed, capsys, *args)] == expected


def test_reviews_text_format(reviewed: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["reviews", str(reviewed), "--format", "text", "--package", "alpha"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["STATUS", "SUBJECT", "EDITS", "AT", "TITLE"]
    assert ["change", "alpha/A-3", "1", "2026-02-01T10:00:00+09:00", "Third"] in [
        line.split() for line in lines
    ]
    assert lines[-1] == "4 subjects"


def test_reviews_errors(reviewed: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(InvalidInput, match="Unknown status"):
        collect_reviews(load_config(reviewed), statuses=["done"])
    (reviewed / "reviews.json").write_text("{}", encoding="utf-8")
    assert main(["reviews", str(reviewed)]) == 1
    assert "does not hold a JSON list" in capsys.readouterr().err


def test_guide_prints_packaged_file(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["guide"]) == 0
    out = capsys.readouterr().out
    assert out == GUIDE_PATH.read_text(encoding="utf-8")
    assert "diffdesk add" in out
    assert "diffdesk reviews REVIEW_ROOT --status change --status rejected" in out
