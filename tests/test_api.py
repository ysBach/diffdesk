"""HTTP API v1: every endpoint, caching headers, gzip and security checks."""

from __future__ import annotations

import json
import socket
from pathlib import Path

from conftest import Client, Repo, bump_mtime

from diffdesk.server import DiffdeskServer

LIGHT_KEYS = {
    "id",
    "package",
    "branch",
    "title",
    "priority",
    "origin",
    "recommended",
    "senior_verdict",
    "status",
    "edited",
    "order",
    "files_count",
    "lines_added",
    "lines_deleted",
}
ORDER = ["A-note", "A-12", "A-3", "A-2", "DUP", "B-1", "DUP"]


def ids(res) -> list[str]:
    return [item["id"] for item in res.json()["items"]]


def test_meta(client: Client, root: Path) -> None:
    res = client.request("GET", "/api/meta")
    assert res.status == 200
    meta = res.json()
    assert meta["name"] == "diffdesk"
    assert meta["title"] == "Test review"
    assert meta["root"] == str(root.resolve())
    assert meta["packages"] == {
        "alpha": {"depends_on": ["beta"]},
        "beta": {"depends_on": []},
        "gamma": {"depends_on": []},
    }
    assert meta["ui_defaults"] == {"theme": "dark", "editor": "codemirror"}
    assert meta["counts"] == {
        "total": 7,
        "by_status": {"pending": 7, "accepted": 0, "rejected": 0, "change": 0},
    }


def test_tree_groups_packages_and_branches(client: Client, repo: Repo) -> None:
    tree = client.request("GET", "/api/tree").json()
    assert [p["package"] for p in tree] == ["alpha", "beta", "gamma"]
    alpha = tree[0]
    assert alpha["total"] == 4 and alpha["reviewed"] == 0
    assert [(b["name"], b["repo"], b["total"]) for b in alpha["branches"]] == [
        ("(no repo)", None, 1),
        ("work", str(repo.path), 3),
    ]
    assert [b["name"] for b in tree[1]["branches"]] == ["(no repo)", "feature"]


def test_subjects_light_fields_and_commit_order(client: Client) -> None:
    res = client.request("GET", "/api/subjects")
    data = res.json()
    assert data["total"] == 7
    assert [i["id"] for i in data["items"]] == ORDER
    assert all(set(item) == LIGHT_KEYS for item in data["items"])
    by_id = {(i["package"], i["id"]): i for i in data["items"]}
    # A-12 lists only a.py, so b.txt (also changed in c1) does not count.
    a12 = by_id["alpha", "A-12"]
    assert (a12["files_count"], a12["lines_added"], a12["lines_deleted"]) == (1, 1, 1)
    a2 = by_id["alpha", "A-2"]
    assert (a2["files_count"], a2["lines_added"], a2["lines_deleted"]) == (2, 0, 2)
    note = by_id["alpha", "A-note"]
    assert (note["branch"], note["order"], note["lines_added"]) == ("(no repo)", 1, 0)
    assert a12["branch"] == "work" and a12["priority"] == "P1"


def test_subjects_pagination(client: Client) -> None:
    res = client.request("GET", "/api/subjects?offset=1&limit=2")
    assert res.json()["total"] == 7
    assert ids(res) == ORDER[1:3]
    assert ids(client.request("GET", "/api/subjects?offset=6&limit=200")) == ORDER[6:]
    assert ids(client.request("GET", "/api/subjects?limit=0")) == []
    assert client.request("GET", "/api/subjects?offset=-1").status == 400
    assert client.request("GET", "/api/subjects?limit=many").status == 400


def test_subjects_filters(client: Client) -> None:
    assert ids(client.request("GET", "/api/subjects?package=beta")) == ["DUP", "B-1"]
    assert ids(client.request("GET", "/api/subjects?package=alpha&branch=work")) == [
        "A-12",
        "A-3",
        "A-2",
    ]
    assert ids(client.request("GET", "/api/subjects?q=numbers")) == ["A-12"]
    assert ids(client.request("GET", "/api/subjects?q=ADDS%20four")) == ["A-3"]
    assert ids(client.request("GET", "/api/subjects?q=adds%20five")) == []
    client.request("POST", "/api/review", {"id": "A-3", "verdict": "reject"})
    assert ids(client.request("GET", "/api/subjects?status=rejected")) == ["A-3"]
    assert len(ids(client.request("GET", "/api/subjects?status=rejected,pending"))) == 7
    assert client.request("GET", "/api/subjects?status=done").status == 400


def test_subject_full(client: Client, repo: Repo) -> None:
    data = client.request("GET", "/api/subject?id=A-12").json()
    assert data["title"] == "First" and data["tags"] == ["numbers"]
    assert data["base"] == repo.commits[0] and data["head"] == repo.commits[1]
    assert (data["package"], data["branch"], data["status"], data["edited"]) == (
        "alpha",
        "work",
        "pending",
        False,
    )
    assert data["review"] == {"verdict": None, "comment": None, "at": None, "history_count": 0}
    assert data["files"] == [
        {
            "path": "a.py",
            "lines_added": 1,
            "lines_deleted": 1,
            "size_base": 14,
            "size_head": 12,
            "lines_base": 3,
            "lines_head": 3,
            "edited": False,
        }
    ]
    b1 = client.request("GET", "/api/subject?id=B-1").json()
    assert b1["extra_field"] == {"x": 1}
    assert b1["files"][0]["size_base"] is None and b1["files"][0]["lines_head"] == 2
    a3 = client.request("GET", "/api/subject?id=A-3").json()
    assert [f["path"] for f in a3["files"]] == ["a.py"]
    assert a3["files"][0]["lines_head"] == 4


def test_subject_ids_and_duplicates(client: Client) -> None:
    res = client.request("GET", "/api/subject?id=DUP")
    assert res.status == 400 and "package" in res.json()["error"]
    gamma = client.request("GET", "/api/subject?id=DUP&package=gamma")
    assert gamma.json()["title"] == "Dup in gamma"
    assert client.request("GET", "/api/subject?id=nope").status == 404
    assert client.request("GET", "/api/subject?id=A-12&package=beta").status == 404
    for bad in ("../x", "a/b", "", "-x", "a..b"):
        assert client.request("GET", f"/api/subject?id={bad}").status == 400
    assert client.request("GET", "/api/subject?id=A-12&package=../x").status == 400


def test_flow(client: Client) -> None:
    flow = client.request("GET", "/api/flow").json()
    assert [n["package"] for n in flow["nodes"]] == ["alpha", "beta", "gamma"]
    assert flow["nodes"][0]["total"] == 4
    assert flow["edges"] == [["alpha", "beta"], ["beta", "alpha"]]


def test_treemap(client: Client) -> None:
    items = client.request("GET", "/api/treemap?package=alpha").json()
    assert [i["id"] for i in items] == ["A-note", "A-12", "A-3", "A-2"]
    assert set(items[0]) == {
        "id",
        "package",
        "branch",
        "title",
        "status",
        "lines_added",
        "lines_deleted",
    }
    assert (items[2]["lines_added"], items[2]["lines_deleted"]) == (1, 0)
    assert len(client.request("GET", "/api/treemap").json()) == 7


def test_diff_full(client: Client) -> None:
    res = client.request("GET", "/api/diff?id=A-12&path=a.py")
    assert res.status == 200
    assert res.json() == {
        "base": "one\ntwo\nthree\n",
        "head": "one\n2\nthree\n",
        "edit": None,
        "language": "python",
        "binary": False,
    }
    assert res.headers["cache-control"] == "no-cache"
    again = client.request(
        "GET", "/api/diff?id=A-12&path=a.py", headers={"If-None-Match": res.headers["etag"]}
    )
    assert again.status == 304 and again.raw == b""
    added = client.request("GET", "/api/diff?id=B-1&path=b.txt").json()
    assert added["base"] is None and added["head"] == "x\ny\n" and added["language"] is None
    binary = client.request("GET", "/api/diff?id=A-2&path=img.bin").json()
    assert binary["binary"] is True and binary["base"] is None and binary["head"] is None
    deleted = client.request("GET", "/api/diff?id=A-2&path=b.txt").json()
    assert deleted["head"] is None and deleted["base"] == "x\ny\n"


def test_diff_hunks(client: Client, repo: Repo) -> None:
    url = "/api/diff?id=A-12&path=a.py&mode=hunks"
    narrow = client.request("GET", url + "&context=0").json()
    assert narrow == {
        "hunks": [
            {
                "old_start": 2,
                "old_lines": 1,
                "new_start": 2,
                "new_lines": 1,
                "lines": ["-two", "+2"],
            }
        ],
        "binary": False,
    }
    wide = client.request("GET", url).json()["hunks"]
    assert wide[0]["lines"] == [" one", "-two", "+2", " three"]
    assert wide[0]["old_start"] == 1 and wide[0]["old_lines"] == 3
    a3 = client.request("GET", "/api/diff?id=A-3&path=a.py&mode=hunks").json()["hunks"]
    assert a3[0]["lines"][-1] == "\\ No newline at end of file"
    binary = client.request("GET", "/api/diff?id=A-2&path=img.bin&mode=hunks").json()
    assert binary == {"hunks": [], "binary": True}


def test_diff_cache_headers(client: Client, repo: Repo) -> None:
    c0, c1 = repo.commits[0], repo.commits[1]
    plain = client.request("GET", "/api/diff?id=A-12&path=a.py&mode=hunks")
    assert plain.headers["cache-control"] == "no-cache" and plain.headers["etag"]
    pinned = client.request("GET", f"/api/diff?id=A-12&path=a.py&mode=hunks&base={c0}&head={c1}")
    assert pinned.headers["cache-control"] == "max-age=31536000, immutable"
    assert pinned.headers["etag"] == plain.headers["etag"]
    full = client.request("GET", f"/api/diff?id=A-12&path=a.py&base={c0}&head={c1}")
    assert full.headers["cache-control"] == "no-cache"
    stale = client.request("GET", f"/api/diff?id=A-12&path=a.py&mode=hunks&base={c1}&head={c1}")
    assert stale.status == 409


def test_diff_validation(client: Client) -> None:
    for path in ("b.txt", "../a.py", "/etc/passwd", "a.py%0A", "./a.py", "", "~/x"):
        res = client.request("GET", f"/api/diff?id=A-12&path={path}")
        assert res.status == 400, path
    assert client.request("GET", "/api/diff?id=A-note&path=x").status == 404
    assert client.request("GET", "/api/diff?id=A-12&path=a.py&mode=split").status == 400
    assert client.request("GET", "/api/diff?id=A-12&path=a.py&context=-1").status == 400


def test_gzip_only_for_large_bodies(client: Client) -> None:
    gz = {"Accept-Encoding": "gzip, deflate"}
    big = client.request("GET", "/api/subjects", headers=gz)
    assert len(big.body) > 1024
    assert big.headers["content-encoding"] == "gzip" and len(big.raw) < len(big.body)
    assert big.headers["vary"] == "Accept-Encoding"
    assert json.loads(big.body)["total"] == 7
    small = client.request("GET", "/api/subject?id=DUP&package=beta", headers=gz)
    assert len(small.body) <= 1024 and "content-encoding" not in small.headers
    refused = client.request("GET", "/api/subjects", headers={"Accept-Encoding": "gzip;q=0"})
    assert "content-encoding" not in refused.headers


def test_json_etag_changes_with_state(client: Client) -> None:
    first = client.request("GET", "/api/tree")
    tag = first.headers["etag"]
    assert tag.startswith('W/"')
    assert client.request("GET", "/api/tree", headers={"If-None-Match": tag}).status == 304
    client.request("POST", "/api/review", {"id": "A-12", "verdict": "accept"})
    after = client.request("GET", "/api/tree", headers={"If-None-Match": tag})
    assert after.status == 200 and after.headers["etag"] != tag


def test_host_and_origin_checks(client: Client, server: DiffdeskServer) -> None:
    port = server.server_address[1]
    assert client.request("GET", "/api/meta", headers={"Host": f"evil.test:{port}"}).status == 403
    assert client.request("GET", "/api/meta", headers={"Host": "127.0.0.1:1"}).status == 403
    assert client.request("GET", "/api/meta", headers={"Host": f"localhost:{port}"}).status == 200
    body = {"id": "A-12", "verdict": "accept"}
    evil = client.request("POST", "/api/review", body, headers={"Origin": "http://evil.test"})
    assert evil.status == 403
    cross = client.request("POST", "/api/review", body, headers={"Sec-Fetch-Site": "cross-site"})
    assert cross.status == 403
    good = client.request(
        "POST", "/api/review", body, headers={"Origin": f"http://localhost:{port}"}
    )
    assert good.status == 200
    text = client.request("POST", "/api/review", body, headers={"Content-Type": "text/plain"})
    assert text.status == 415
    assert client.request("POST", "/api/review", b"{not json").status == 400
    assert client.request("POST", "/api/review", [1, 2]).status == 400
    assert client.request("POST", "/api/nothing", body).status == 404


def test_review_history(client: Client, root: Path) -> None:
    first = client.request("POST", "/api/review", {"id": "A-12", "verdict": "accept"})
    assert first.json()["status"] == "accepted"
    client.request("POST", "/api/review", {"id": "A-12", "verdict": "change", "comment": "x"})
    data = client.request("GET", "/api/subject?id=A-12").json()
    assert data["status"] == "change"
    assert data["review"]["verdict"] == "change" and data["review"]["comment"] == "x"
    assert data["review"]["history_count"] == 2 and data["review"]["at"]
    records = json.loads((root / "reviews.json").read_text())
    assert [(r["id"], r["package"], r["verdict"]) for r in records] == [
        ("A-12", "alpha", "accept"),
        ("A-12", "alpha", "change"),
    ]
    assert client.request("POST", "/api/review", {"id": "A-12", "verdict": "ok"}).status == 400
    bad_comment = {"id": "A-12", "verdict": "accept", "comment": 5}
    assert client.request("POST", "/api/review", bad_comment).status == 400
    assert client.request("POST", "/api/review", {"id": "DUP", "verdict": "accept"}).status == 400
    dup = {"id": "DUP", "package": "gamma", "verdict": "reject"}
    assert client.request("POST", "/api/review", dup).status == 200
    beta = client.request("GET", "/api/subject?id=DUP&package=beta").json()
    assert beta["status"] == "pending"
    assert client.request("GET", "/api/meta").json()["counts"]["by_status"] == {
        "pending": 5,
        "accepted": 0,
        "rejected": 1,
        "change": 1,
    }


def test_review_record_without_package_matches_by_id(client: Client, root: Path) -> None:
    (root / "reviews.json").write_text(json.dumps([{"id": "A-3", "verdict": "reject"}]))
    assert client.request("GET", "/api/subject?id=A-3").json()["status"] == "rejected"


def test_edit_write_and_remove(client: Client, root: Path) -> None:
    url = "/api/diff?id=A-12&path=a.py"
    before = client.request("GET", url)
    res = client.request("POST", "/api/edit", {"id": "A-12", "path": "a.py", "content": "new\n"})
    assert res.json() == {
        "id": "A-12",
        "package": "alpha",
        "path": "a.py",
        "saved": "edits/alpha/A-12/a.py",
    }
    assert (root / "edits/alpha/A-12/a.py").read_text() == "new\n"
    after = client.request("GET", url, headers={"If-None-Match": before.headers["etag"]})
    assert after.status == 200 and after.json()["edit"] == "new\n"
    data = client.request("GET", "/api/subject?id=A-12").json()
    assert data["edited"] is True and data["files"][0]["edited"] is True
    items = client.request("GET", "/api/subjects?package=alpha").json()["items"]
    assert [i["edited"] for i in items] == [False, True, False, False]

    removed = client.request("POST", "/api/edit", {"id": "A-12", "path": "a.py", "content": None})
    assert removed.json()["saved"] is None
    assert not (root / "edits/alpha").exists()
    assert client.request("GET", url).json()["edit"] is None
    assert client.request("GET", "/api/subject?id=A-12").json()["edited"] is False


def test_edit_keeps_crlf_line_endings(client: Client, root: Path) -> None:
    content = "one\r\ntwo\r\n"
    client.request("POST", "/api/edit", {"id": "A-12", "path": "a.py", "content": content})
    assert (root / "edits/alpha/A-12/a.py").read_bytes() == content.encode()
    assert client.request("GET", "/api/diff?id=A-12&path=a.py").json()["edit"] == content


def test_edit_validation(client: Client, root: Path) -> None:
    cases = [
        {"id": "A-12", "path": "b.txt", "content": "x"},
        {"id": "A-12", "path": "../../x.py", "content": "x"},
        {"id": "A-12", "path": "/tmp/x.py", "content": "x"},
        {"id": "A-12", "path": "a.py", "content": 3},
        {"id": "A-12", "path": "a.py"},
        {"id": "A-note", "path": "x", "content": "x"},
    ]
    for body in cases:
        assert client.request("POST", "/api/edit", body).status == 400, body
    assert not (root / "edits").exists()
    files_from_git = {"id": "A-3", "path": "a.py", "content": "y"}
    assert client.request("POST", "/api/edit", files_from_git).status == 200


def test_static_files(client: Client) -> None:
    index = client.request("GET", "/")
    assert index.status == 200 and index.headers["content-type"].startswith("text/html")
    assert (
        client.request("GET", "/", headers={"If-None-Match": index.headers["etag"]}).status == 304
    )
    assert client.request("GET", "/../pyproject.toml").status == 404
    assert client.request("GET", "/no-such-file.js").status == 404
    assert client.request("GET", "/api/unknown").status == 404


def test_subject_file_changes_are_seen(client: Client, root: Path) -> None:
    path = root / "subjects/alpha/A-12.json"
    data = json.loads(path.read_text())
    data["title"] = "Renamed"
    path.write_text(json.dumps(data))
    bump_mtime(path)
    assert client.request("GET", "/api/subject?id=A-12").json()["title"] == "Renamed"
    path.unlink()
    assert client.request("GET", "/api/subject?id=A-12").status == 404
    assert client.request("GET", "/api/meta").json()["counts"]["total"] == 6


def _raw_exchange(port: int, data: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(data)
        chunks = []
        while chunk := sock.recv(65536):
            chunks.append(chunk)
    return b"".join(chunks)


def test_rejected_request_body_is_not_parsed_as_next_request(
    server: DiffdeskServer, root: Path
) -> None:
    port = server.server_address[1]
    inner_body = json.dumps({"id": "A-12", "verdict": "accept"}).encode()
    inner = (
        f"POST /api/review HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(inner_body)}\r\n\r\n"
    ).encode() + inner_body
    outers = [
        f"POST /api/review HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: http://evil.test\r\n",
        f"POST /api/review HTTP/1.1\r\nHost: evil.test:{port}\r\n",
        f"POST /api/review HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: text/plain\r\n",
        f"GET /api/meta HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n",
    ]
    for outer in outers:
        head = f"{outer}Content-Length: {len(inner)}\r\n\r\n".encode()
        reply = _raw_exchange(port, head + inner)
        assert reply.count(b"HTTP/1.1 ") == 1, outer
    assert not (root / "reviews.json").exists()
