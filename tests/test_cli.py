"""Command line: validate, init, the review root and the configuration file."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from conftest import Client, commit, git, write_subject

from diffdesk import cli
from diffdesk.cli import main
from diffdesk.config import Config, ConfigError, load_config
from diffdesk.server import DiffdeskServer


def test_validate_accepts_good_root(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(root)]) == 0
    out = capsys.readouterr().out
    assert "7 subject files, 0 problems, 1 warnings" in out
    assert "warning: ID DUP is used in packages beta, gamma" in out


def test_validate_reports_problems(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "r"
    write_subject(root, "p", {"id": "GOOD", "title": "t", "files": ["src/a.py"]})
    write_subject(root, "p", {"title": "no id"}, name="noid")
    write_subject(root, "p", {"id": "../up", "title": "t"}, name="badid")
    write_subject(root, "p", {"id": "B", "title": "", "base": "abc", "head": None})
    write_subject(root, "p", {"id": "F", "title": "t", "files": ["../x", "/abs", "a//b"]})
    write_subject(root, "p", {"id": "T", "title": "t", "order": "first", "tags": "x"})
    write_subject(root, "p", {"id": "GOOD", "title": "again"}, name="copy")
    (root / "subjects/p/broken.json").write_text("{", encoding="utf-8")
    assert main(["validate", str(root)]) == 1
    out = capsys.readouterr().out
    expected = [
        "subjects/p/noid.json: $: missing field 'id'",
        "subjects/p/badid.json: $.id: '../up' does not match",
        "subjects/p/B.json: $.title: must not be empty",
        "subjects/p/B.json: 'base' and 'head' must both be set or both be null",
        "subjects/p/F.json: $.files[0]: '../x' does not match",
        "subjects/p/F.json: $.files[1]: '/abs' does not match",
        "subjects/p/F.json: $.files[2]: 'a//b' does not match",
        "subjects/p/T.json: $.order: must be number",
        "subjects/p/T.json: $.tags: must be array",
        "subjects/p/copy.json: same package and ID as subjects/p/GOOD.json",
        "subjects/p/broken.json: cannot read JSON",
    ]
    for text in expected:
        assert text in out, text
    assert "GOOD.json" not in out.replace("as subjects/p/GOOD.json", "")


def test_init_without_git(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "plain"
    assert main(["init", str(root)]) == 0
    assert (root / "diffdesk.toml").is_file()
    assert (root / "subjects").is_dir() and (root / "edits").is_dir()
    assert list((root / "subjects").iterdir()) == []
    assert load_config(root).title == "plain"
    assert main(["init", str(root)]) == 0
    assert "nothing to create" in capsys.readouterr().out


def test_init_in_repo_without_commit(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q", "-b", "main")
    assert main(["init", str(tmp_path / "review")]) == 0
    assert list((tmp_path / "review/subjects").iterdir()) == []


def test_init_example_points_at_first_commit(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q", "-b", "main")
    first = commit(tmp_path, {"hello.py": "print('hi')\n"}, "first")
    commit(tmp_path, {"hello.py": "print('bye')\n"}, "second")
    root = tmp_path / "review"
    assert main(["init", str(root)]) == 0
    example = json.loads((root / "subjects/example/EXAMPLE-1.json").read_text())
    assert example["head"] == first and example["repo"] == str(tmp_path.resolve())
    assert main(["validate", str(root)]) == 0

    srv = DiffdeskServer(load_config(root), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        client = Client("127.0.0.1", srv.server_address[1])
        data = client.request("GET", "/api/subject?id=EXAMPLE-1").json()
        assert [(f["path"], f["lines_added"], f["size_base"]) for f in data["files"]] == [
            ("hello.py", 1, None)
        ]
        diff = client.request("GET", "/api/diff?id=EXAMPLE-1&path=hello.py").json()
        assert diff["base"] is None and diff["head"] == "print('hi')\n"
    finally:
        srv.shutdown()
        srv.server_close()


def test_config_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    assert config.title == tmp_path.name
    assert config.subjects == tmp_path.resolve() / "subjects"
    assert config.cache == tmp_path.resolve() / ".diffdesk-cache"
    assert config.ui == {"theme": "system", "editor": "codemirror"}
    assert config.packages == {}


def test_config_values(tmp_path: Path) -> None:
    (tmp_path / "diffdesk.toml").write_text(
        'title = "T"\n[paths]\nsubjects = "s/x"\nreviews = "r.json"\n'
        '[packages.a]\ndepends_on = ["b"]\n[ui]\neditor = "monaco"\naccent = "#123456"\n'
    )
    config = load_config(tmp_path)
    assert (config.title, config.subjects.name, config.reviews.name) == ("T", "x", "r.json")
    assert config.packages == {"a": ["b"]}
    assert config.ui == {"theme": "system", "editor": "monaco", "accent": "#123456"}


@pytest.mark.parametrize(
    "text",
    [
        '[paths]\ncache = "../outside"\n',
        '[paths]\nedits = "/tmp/edits"\n',
        '[paths]\nreviews = ""\n',
        "[packages.a]\ndepends_on = 'b'\n",
        "title = 3\n",
        "not toml = = 1\n",
    ],
)
def test_config_rejects_bad_values(tmp_path: Path, text: str) -> None:
    (tmp_path / "diffdesk.toml").write_text(text)
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_cli_reports_config_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "diffdesk.toml").write_text('[paths]\ncache = "../x"\n')
    assert main(["validate", str(tmp_path)]) == 2
    assert "must be relative" in capsys.readouterr().err


def choose_root(how: str, wanted: Path, other: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make `wanted` the review root by `how`; return the root argument (empty if none).

    `other` is the current folder for "argument" and "env", and also the value of
    ``DIFFDESK_ROOT`` for "argument", so a wrong order picks `other`.
    """
    if how == "argument":
        monkeypatch.setenv("DIFFDESK_ROOT", str(other))
        monkeypatch.chdir(other)
        return [str(wanted)]
    if how == "env":
        monkeypatch.setenv("DIFFDESK_ROOT", str(wanted))
        monkeypatch.chdir(other)
        return []
    monkeypatch.delenv("DIFFDESK_ROOT", raising=False)
    monkeypatch.chdir(wanted)
    return []


ROOT_SOURCES = ["argument", "env", "cwd"]


@pytest.mark.parametrize("how", ROOT_SOURCES)
@pytest.mark.parametrize(
    ("command", "expected"),
    [(["validate"], "7 subject files"), (["reviews", "--format", "text"], "7 subjects")],
)
def test_root_order_for_reading_commands(
    root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    how: str,
    command: list[str],
    expected: str,
) -> None:
    other = tmp_path / "other"
    write_subject(other, "p", {"id": "X", "title": "t"})
    arg = choose_root(how, root, other, monkeypatch)
    assert main([command[0], *arg, *command[1:]]) == 0
    assert expected in capsys.readouterr().out


@pytest.mark.parametrize("how", ROOT_SOURCES)
def test_root_order_for_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str) -> None:
    wanted, other = tmp_path / "wanted", tmp_path / "other"
    wanted.mkdir()
    other.mkdir()
    assert main(["init", *choose_root(how, wanted, other, monkeypatch)]) == 0
    assert (wanted / "diffdesk.toml").is_file()
    assert not (other / "diffdesk.toml").exists()


@pytest.mark.parametrize("how", ROOT_SOURCES)
def test_root_order_for_serve_and_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    wanted, other = tmp_path / "wanted", tmp_path / "other"
    wanted.mkdir()
    other.mkdir()
    seen: list[Path] = []

    def no_server(config: Config, host: str, port: int) -> None:
        seen.append(config.root)
        raise OSError("no server in this test")

    def no_stdio(root: Path) -> int:
        seen.append(root.resolve())
        return 0

    monkeypatch.setattr(cli, "DiffdeskServer", no_server)
    monkeypatch.setattr(cli, "serve_stdio", no_stdio)
    arg = choose_root(how, wanted, other, monkeypatch)
    assert main(["serve", *arg]) == 1
    assert main(["mcp", *(["--root", *arg] if arg else [])]) == 0
    assert seen == [wanted.resolve(), wanted.resolve()]
