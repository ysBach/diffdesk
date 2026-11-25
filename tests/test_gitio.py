"""Git layer: input checks and batched git calls."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import Repo

from diffdesk import gitio
from diffdesk.gitio import InvalidInput


@pytest.mark.parametrize("ref", ["HEAD", "main", "HEAD~1", "a" * 40, "review/fixes", "v1.0^"])
def test_check_ref_accepts(ref: str) -> None:
    assert gitio.check_ref(ref) == ref


@pytest.mark.parametrize(
    "ref", ["", "-x", "--output=x", "a..b", "a b", "a\nb", "a:b", "a\x00", None, 3]
)
def test_check_ref_rejects(ref: object) -> None:
    with pytest.raises(InvalidInput):
        gitio.check_ref(ref)


@pytest.mark.parametrize("path", ["a.py", "src/x/y.rs", "my file.txt", ".github/ci.yml", "a..b"])
def test_check_path_accepts(path: str) -> None:
    assert gitio.check_path(path) == path


@pytest.mark.parametrize(
    "path",
    ["", "/abs", "~/x", "../x", "a/../b", "./a", "a/", "a//b", "a\\b", "a\nb", "a\tb", None],
)
def test_check_path_rejects(path: object) -> None:
    with pytest.raises(InvalidInput):
        gitio.check_path(path)


def test_numstat_many(repo: Repo) -> None:
    c = repo.commits
    pairs = [(c[0], c[1]), (c[1], c[2]), (c[2], c[3]), (c[1], c[1]), ("f" * 40, c[1])]
    out = gitio.numstat_many(str(repo.path), pairs)
    assert out[c[0], c[1]] == {"a.py": [1, 1], "b.txt": [2, 0]}
    assert out[c[1], c[2]] == {"a.py": [1, 0]}
    assert out[c[2], c[3]] == {"b.txt": [0, 2], "img.bin": [None, None]}
    assert out[c[1], c[1]] == {}
    assert out["f" * 40, c[1]] is None


def test_commit_order(repo: Repo) -> None:
    c = repo.commits
    order = gitio.commit_order(str(repo.path), [c[3], c[1], "work~1", "e" * 40])
    assert order[c[1]] < order["work~1"] < order[c[3]]
    assert "e" * 40 not in order


def test_read_blobs(repo: Repo) -> None:
    c = repo.commits
    blobs = gitio.read_blobs(str(repo.path), [(c[0], "a.py"), (c[3], "b.txt"), (c[3], "img.bin")])
    assert blobs == [b"one\ntwo\nthree\n", None, b"\x00\x01\x02"]


def test_run_git_errors(tmp_path: Path) -> None:
    with pytest.raises(gitio.GitError, match="not found"):
        gitio.run_git(str(tmp_path / "missing"), ["status"])
    with pytest.raises(gitio.GitError, match="failed"):
        gitio.run_git(str(tmp_path), ["rev-parse", "--verify", "HEAD"])
