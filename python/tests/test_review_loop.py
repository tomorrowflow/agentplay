"""The local review loop: edit evidence beside the map, `agentplay review`, and `/api/reviews`."""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from agentplay import cli, pipeline
from agentplay._core.code_authorship import line_fingerprints
from agentplay.server import serve
from agentplay.store import Store

STEM = "77777777-0000-0000-0000-000000000007"
SID = f"local:claude-code:{STEM}"
BASE_APP = "def total(items):\n    return 0\n"
HEAD_APP = "def total(items):\n    return sum(items)\n    # SECRET-LINE-TEXT\n"
NEW_FILE = "LIMIT = 10\nWINDOW = 60\n"


def _line(**d: Any) -> str:
    return json.dumps(d) + "\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _write_session(home: Path, repo: Path) -> Path:
    """Read, Edit (with Claude's recorded patch) and Write a new file, then run the tests."""
    proj = home / ".claude" / "projects" / "-work-app"
    proj.mkdir(parents=True, exist_ok=True)
    app, new = f"{repo}/src/app.py", f"{repo}/src/limits.py"
    calls = [
        ("r1", "Read", {"file_path": app}, "x", None),
        ("e1", "Edit", {"file_path": app, "old_string": "    return 0",
                        "new_string": "    return sum(items)\n    # SECRET-LINE-TEXT"}, "ok", {
            "filePath": app, "oldString": "    return 0",
            "newString": "    return sum(items)\n    # SECRET-LINE-TEXT",
            "originalFile": BASE_APP, "userModified": False, "replaceAll": False,
            "structuredPatch": [{"oldStart": 1, "oldLines": 2, "newStart": 1, "newLines": 3,
                                 "lines": [" def total(items):", "-    return 0", "+    return sum(items)",
                                           "+    # SECRET-LINE-TEXT"]}]}),
        ("w1", "Write", {"file_path": new, "content": NEW_FILE}, "ok",
         {"type": "create", "filePath": new, "content": NEW_FILE}),
        ("b1", "Bash", {"command": "pnpm test --filter SECRET-ARG"}, "passed", None),
    ]
    lines = [_line(type="user", timestamp="2026-09-10T01:00:00Z", cwd=str(repo), sessionId=STEM,
                   message={"role": "user", "content": "fix the total"})]
    for i, (tid, name, inp, out, native) in enumerate(calls):
        lines.append(_line(type="assistant", timestamp=f"2026-09-10T01:00:{10 + 2 * i:02d}Z", cwd=str(repo),
                           sessionId=STEM, message={
                               "id": f"m{i}", "model": "claude-sonnet-5", "role": "assistant",
                               "usage": {"input_tokens": 10, "output_tokens": 5},
                               "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}))
        result: dict[str, Any] = {"type": "user", "timestamp": f"2026-09-10T01:00:{11 + 2 * i:02d}Z",
                                  "cwd": str(repo), "sessionId": STEM,
                                  "message": {"role": "user", "content": [
                                      {"type": "tool_result", "tool_use_id": tid, "content": out}]}}
        if native is not None:
            result["toolUseResult"] = native
        lines.append(json.dumps(result) + "\n")
    f = proj / f"{STEM}.jsonl"
    f.write_text("".join(lines))
    return f


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("AGENTPLAY_HOME", str(tmp_path / "sm"))
    monkeypatch.delenv("AGENTPLAY_CLAUDE_HOMES", raising=False)
    repo = tmp_path / "work" / "app"
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "remote", "add", "origin", "git@github.com:acme/app.git")
    (repo / "src" / "app.py").write_text(BASE_APP)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _write_session(home, repo)
    pipeline.build_all(agents={"claude-code"}, store=Store(tmp_path / "sm"))
    (repo / "src" / "app.py").write_text(HEAD_APP)
    (repo / "src" / "limits.py").write_text(NEW_FILE)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "head")
    head = _git(repo, "rev-parse", "HEAD")
    return {"home": home, "repo": repo, "sm": tmp_path / "sm", "base": base, "head": head, "tmp": tmp_path}


def _seq(doc: dict[str, Any], tool: str) -> int:
    return next(e["seq"] for e in doc["events"] if e.get("tool") == tool)


# ---------------------------------------------------------------- evidence sidecar
def test_edit_evidence_sits_beside_the_map_with_map_seqs_and_repo_paths(env):
    st = Store(env["sm"])
    doc, evidence = st.get(SID), st.evidence(SID)
    assert evidence["schema"] == "agentplay-evidence/v1" and evidence["sessionId"] == SID
    repo_id = doc["repos"][0]["id"]
    got = [(e["seq"], e["agent"], e["repo"], e["path"], e["operation"]) for e in evidence["edits"]]
    assert got == [
        (_seq(doc, "Edit"), "main", repo_id, "src/app.py", "replace"),
        (_seq(doc, "Write"), "main", repo_id, "src/limits.py", "write"),
    ]
    edit = evidence["edits"][0]
    assert edit["before"] == line_fingerprints("def total(items):\n    return 0\n")
    assert edit["after"] == line_fingerprints(HEAD_APP)


def test_edit_evidence_never_reaches_the_map_or_an_export(env, monkeypatch, capsys):
    st = Store(env["sm"])
    digests = {h for e in st.evidence(SID)["edits"] for h in e["after"]}
    raw_map = (env["sm"] / "maps" / f"{st.index[SID]['key']}.json").read_text()
    monkeypatch.chdir(env["tmp"])
    assert cli.main(["export", STEM[-12:], "-o", "s.json.gz"]) == 0
    exported = gzip.decompress((env["tmp"] / "s.json.gz").read_bytes()).decode()
    for text in (raw_map, exported):
        assert not any(d in text for d in digests)
        assert "agentplay-evidence" not in text and "SECRET-LINE-TEXT" not in text


def test_edit_evidence_is_removed_when_its_map_is_replaced_without_one(env, monkeypatch, capsys):
    st = Store(env["sm"])
    monkeypatch.chdir(env["tmp"])
    assert cli.main(["export", STEM[-12:], "-o", "s.json"]) == 0
    assert cli.main(["import", "s.json"]) == 0
    assert Store(env["sm"]).evidence(SID) is None
    assert not (env["sm"] / "evidence" / f"{st.index[SID]['key']}.json").exists()


# ---------------------------------------------------------------- agentplay review
def _comments(tmp: Path, comments: list[dict[str, Any]]) -> Path:
    f = tmp / "comments.json"
    f.write_text(json.dumps({"schema": "agentplay-review-input/v1", "comments": comments}))
    return f


def _review(env, capsys, comments: list[dict[str, Any]], *extra: str) -> tuple[int, dict[str, Any]]:
    f = _comments(env["tmp"], comments)
    rc = cli.main(["review", "--pr", "acme/app#7", "--session", STEM[-12:], "--repo-path", str(env["repo"]),
                   "--comments", str(f), "--base", env["base"], "--head", env["head"], *extra])
    capsys.readouterr()
    path = env["sm"] / "reviews" / "acme_app_7.json"
    return rc, json.loads(path.read_text()) if path.exists() else {}


def test_review_attributes_edits_made_in_a_removed_claude_worktree(env, capsys):
    # Claude Code deletes `.claude/worktrees/<name>` once the work merges, so the
    # map is often built when only the main checkout's `.git` is left above it.
    _write_session(env["home"], env["repo"] / ".claude" / "worktrees" / "feat-x")
    pipeline.build_all(agents={"claude-code"}, rebuild=True, store=Store(env["sm"]))
    doc = Store(env["sm"]).get(SID)
    assert sorted(f["path"] for f in doc["files"]) == ["src/app.py", "src/limits.py"]
    _, review = _review(env, capsys, [{"id": "c1", "path": "src/app.py", "line": 2, "body": "Use math.fsum"}])
    assert review["comments"][0]["attribution"]["seq"] == _seq(doc, "Edit")

def test_review_attributes_edits_made_in_a_deleted_configured_worktree(env, capsys, monkeypatch):
    # A factory worktree is gone by build time and has no `.git` above it at all.
    worktrees = env["tmp"] / "factory" / "worktrees"
    monkeypatch.setenv("AGENTPLAY_REPO_ROOTS", f"{worktrees}/wb-*=acme/app")
    _write_session(env["home"], worktrees / "wb-card42")
    pipeline.build_all(agents={"claude-code"}, rebuild=True, store=Store(env["sm"]))
    st = Store(env["sm"])
    doc = st.get(SID)
    assert [(r["name"], r["remote"]) for r in doc["repos"]] == [("app", "acme/app")]
    assert [e["path"] for e in st.evidence(SID)["edits"]] == ["src/app.py", "src/limits.py"]
    _, review = _review(env, capsys, [{"id": "c1", "path": "src/app.py", "line": 2, "body": "Use math.fsum"}])
    assert review["comments"][0]["attribution"]["seq"] == _seq(doc, "Edit")


def test_review_attributes_comments_to_the_edit_that_wrote_the_line(env, capsys):
    doc = Store(env["sm"]).get(SID)
    edit, write, verify = _seq(doc, "Edit"), _seq(doc, "Write"), _seq(doc, "Bash")
    rc, review = _review(env, capsys, [
        {"id": "c1", "path": "src/app.py", "line": 2, "body": "Use math.fsum", "author": "rev"},
        {"id": "c2", "path": "src/app.py", "line": 1, "body": "Rename this"},
        {"id": "c3", "path": "src/app.py", "line": 40, "body": "Stale line"},
        {"id": "c4", "path": "src/limits.py", "line": 2, "body": "Too short"},
        {"id": "c5", "path": "src/gone.py", "line": 1, "body": "?"},
        {"id": "c6", "path": "README.md", "line": 1, "body": "?"},
    ])
    assert rc == 0
    assert review["schema"] == "agentplay-review/v1" and review["sessions"] == [SID]
    assert review["pr"] == {"repo": "acme/app", "number": 7, "url": "https://github.com/acme/app/pull/7",
                            "base": env["base"], "head": env["head"]}
    got = {c["id"]: c["attribution"] for c in review["comments"]}
    assert got == {
        "c1": {"kind": "edit", "session": SID, "seq": edit, "agent": "main", "shared": False},
        "c2": {"kind": "unknown", "reason": "base"},
        "c3": {"kind": "unknown", "reason": "line_out_of_range"},
        "c4": {"kind": "edit", "session": SID, "seq": write, "agent": "main", "shared": False},
        "c5": {"kind": "unknown", "reason": "file_not_in_head"},
        "c6": {"kind": "unknown", "reason": "file_not_in_head"},
    }
    c1 = review["comments"][0]
    assert (c1["body"], c1["author"], c1["source"]) == ("Use math.fsum", "rev", "file")
    read = _seq(doc, "Read")
    assert c1["causalPath"] == [
        {"seq": read, "kind": "read", "label": "Read src/app.py"},
        {"seq": edit, "kind": "edit", "label": "Edit src/app.py"},
        {"seq": verify, "kind": "verify", "label": "pnpm test"},
    ]
    assert all(c["causalPath"] == [] for c in review["comments"] if c["attribution"]["kind"] == "unknown")


def test_review_without_evidence_for_the_file_says_so(env, capsys):
    (env["repo"] / "README.md").write_text("hello\n")
    _git(env["repo"], "add", ".")
    _git(env["repo"], "commit", "-qm", "readme")
    env["head"] = _git(env["repo"], "rev-parse", "HEAD")
    _, review = _review(env, capsys, [{"id": "c", "path": "README.md", "line": 1, "body": "?"}])
    assert review["comments"][0]["attribution"] == {"kind": "unknown", "reason": "no_evidence"}


def test_review_is_idempotent_and_stays_out_of_the_map(env, capsys):
    comments = [{"id": "c1", "path": "src/app.py", "line": 2, "body": "SECRET-REVIEW-BODY"}]
    _, first = _review(env, capsys, comments)
    _, second = _review(env, capsys, comments)
    assert {k: v for k, v in first.items() if k != "builtAt"} == {k: v for k, v in second.items() if k != "builtAt"}
    assert [p.name for p in (env["sm"] / "reviews").iterdir()] == ["acme_app_7.json"]
    st = Store(env["sm"])
    assert "SECRET-REVIEW-BODY" not in (env["sm"] / "maps" / f"{st.index[SID]['key']}.json").read_text()


def test_review_finds_the_sessions_that_opened_the_pr(env, capsys):
    st = Store(env["sm"])
    st.index[SID]["prs"] = [{"repo": "Acme/App", "number": 7, "url": "u", "state": "open"}]
    st.save_index()
    f = _comments(env["tmp"], [{"id": "c1", "path": "src/app.py", "line": 2, "body": "x"}])
    assert cli.main(["review", "--pr", "acme/app#7", "--repo-path", str(env["repo"]), "--comments", str(f),
                     "--base", env["base"], "--head", env["head"]]) == 0
    assert "1 attributed to an edit" in capsys.readouterr().out
    assert cli.main(["review", "--pr", "acme/app#8", "--repo-path", str(env["repo"]), "--comments", str(f),
                     "--base", env["base"], "--head", env["head"]]) == 1
    assert "no built session lists acme/app#8" in capsys.readouterr().err


def test_review_reports_a_head_commit_the_checkout_lacks(env, capsys):
    # A local origin, so the one fetch attempt fails here instead of reaching GitHub.
    _git(env["repo"], "remote", "set-url", "origin", str(env["tmp"] / "no-such-remote"))
    f = _comments(env["tmp"], [])
    assert cli.main(["review", "--pr", "acme/app#7", "--session", STEM[-12:], "--repo-path", str(env["repo"]),
                     "--comments", str(f), "--base", env["base"], "--head", "0" * 40]) == 1
    err = capsys.readouterr().err
    assert "000000000000 is not in" in err and "git fetch origin pull/7/head" in err
    assert not (env["sm"] / "reviews").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in gh is a POSIX script")
def test_review_reads_github_through_gh_when_asked(env, capsys, monkeypatch):
    pull = {"base": {"sha": env["base"]}, "head": {"sha": env["head"]}, "html_url": "https://github.com/acme/app/pull/7"}
    pages = [
        [{"id": 1, "path": "src/app.py", "line": 2, "side": "RIGHT", "body": "b1", "user": {"login": "rev"},
          "html_url": "https://github.com/acme/app/pull/7#discussion_r1"},
         {"id": 2, "path": "src/app.py", "line": None, "original_line": 2, "body": "outdated"}],
        [{"id": 3, "path": "src/app.py", "line": 1, "side": "LEFT", "body": "on a removed line"}],
    ]
    bin_dir = env["tmp"] / "bin"
    bin_dir.mkdir()
    (env["tmp"] / "pull.json").write_text(json.dumps(pull))
    (env["tmp"] / "comments.json").write_text("".join(json.dumps(p) + "\n" for p in pages))
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/sh\ncase "$*" in\n  *comments*) cat "{env["tmp"]}/comments.json" ;;\n'
                  f'  "api repos/acme/app/pulls/7") cat "{env["tmp"]}/pull.json" ;;\n  *) exit 1 ;;\nesac\n')
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    assert cli.main(["review", "--pr", "acme/app#7", "--session", STEM[-12:], "--repo-path", str(env["repo"])]) == 0
    out = capsys.readouterr().out
    assert "1 comments, 1 attributed to an edit, 0 unknown" in out and "2 outdated or removed-line" in out
    review = json.loads((env["sm"] / "reviews" / "acme_app_7.json").read_text())
    (c,) = review["comments"]
    assert (c["id"], c["author"], c["source"], c["url"]) == (
        "1", "rev", "github", "https://github.com/acme/app/pull/7#discussion_r1")
    assert c["attribution"]["kind"] == "edit"


# ---------------------------------------------------------------- /api/reviews
def _get(base: str, path: str, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    req = urllib.request.Request(base + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_reviews_endpoint_serves_the_docs_that_list_a_session(env, capsys):
    _review(env, capsys, [{"id": "c1", "path": "src/app.py", "line": 2, "body": "SECRET-REVIEW-BODY"}])
    httpd = serve(0, store=Store(env["sm"]))
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        status, body = _get(base, "/api/reviews?id=" + urllib.parse.quote(SID, safe=""))
        assert status == 200 and [r["pr"]["number"] for r in body["reviews"]] == [7]
        assert body["reviews"][0]["comments"][0]["body"] == "SECRET-REVIEW-BODY"
        assert _get(base, "/api/reviews?id=local%3Aclaude-code%3Aother") == (200, {"success": True, "reviews": []})
        assert _get(base, "/api/reviews")[0] == 400
        status, body = _get(base, "/api/reviews?id=" + urllib.parse.quote(SID, safe=""),
                            {"Host": "evil.example:7317"})
        assert status == 403 and "SECRET-REVIEW-BODY" not in json.dumps(body)
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------- attribution reasons
class _Files:
    """A checkout stand-in: one path's content at the base ("b") and head ("h") commits."""

    def __init__(self, base: str | None, head: str) -> None:
        self.files = {"b": base, "h": head}

    def show(self, sha: str, path: str) -> str | None:
        return self.files[sha]


def _edit(sid: str, seq: int, before: list[str], after: list[str]) -> dict[str, Any]:
    return {"session_id": sid, "event_id": seq, "agent": "main", "operation": "replace",
            "before": line_fingerprints("".join(f"{x}\n" for x in before)),
            "after": line_fingerprints("".join(f"{x}\n" for x in after))}


def _attr(base: str | None, head: str, edits: list[dict[str, Any]], line: int) -> dict[str, Any]:
    from agentplay import review

    return review._attribute({"path": "f", "line": line, "body": ""}, _Files(base, head),
                             {"base": "b", "head": "h"}, edits)


def _owned(sid: str, seq: int, shared: bool = False) -> dict[str, Any]:
    return {"kind": "edit", "session": sid, "seq": seq, "agent": "main", "shared": shared}


def test_an_untouched_base_line_is_base_even_when_an_edit_inserted_the_same_text():
    base, head = "def f():\n    pass\n}\n", "def f():\n    pass\n}\nif x:\n    y()\n}\n"
    edits = [_edit("s1", 7, ["}"], ["}", "if x:", "    y()", "}"])]
    assert _attr(base, head, edits, 3) == {"kind": "unknown", "reason": "base"}
    assert _attr(base, head, edits, 6) == _owned("s1", 7)


def test_shared_needs_another_session_placed_over_the_same_line():
    base, head = "A\n}\nB\n}\n", "A\n}\nQ\n}\nB\nR\n}\n"
    edits = [_edit("s1", 7, ["A", "}", "B"], ["A", "}", "Q", "}", "B"]), _edit("s2", 9, ["B", "}"], ["B", "R", "}"])]
    assert [_attr(base, head, edits, n) for n in (3, 4, 6, 7)] == [
        _owned("s1", 7), _owned("s1", 7), _owned("s2", 9), {"kind": "unknown", "reason": "base"}]


def test_a_line_another_session_wrote_first_is_shared():
    edits = [_edit("s2", 3, ["a", "old"], ["a", "draft"]), _edit("s1", 7, ["draft"], ["final"])]
    assert _attr("a\nold\n", "a\nfinal\n", edits, 2) == _owned("s1", 7, shared=True)


def test_the_same_edit_in_two_sessions_is_ambiguous():
    edits = [_edit("s1", 7, ["a", "old"], ["a", "new"]), _edit("s2", 9, ["a", "old"], ["a", "new"])]
    assert _attr("a\nold\n", "a\nnew\n", edits, 2) == {"kind": "unknown", "reason": "ambiguous"}


def test_a_line_from_an_edit_the_replay_could_not_place_is_unplaced():
    base, head = "x\nret\nx\nret\n", "x\nret\nNEW\nx\nret\n"
    edits = [_edit("s1", 7, ["ret"], ["ret", "NEW"])]
    assert _attr(base, head, edits, 3) == {"kind": "unknown", "reason": "unplaced"}
    assert _attr(base, head + "HUMAN\n", edits, 6) == {"kind": "unknown", "reason": "not_observed"}


def test_comment_lines_count_newlines_only_like_git():
    base, head = "a\nTAIL\n", "a\nx\x0cy\nNEW\nTAIL\n"
    edits = [_edit("s1", 7, ["a"], ["a", "x\x0cy", "NEW"])]
    assert _attr(base, head, edits, 3) == _owned("s1", 7)
    assert _attr(base, head, edits, 4) == {"kind": "unknown", "reason": "base"}
    assert _attr(base, head, edits, 2) == {"kind": "unknown", "reason": "line_split_differs"}
    assert _attr(base, head, edits, 5) == {"kind": "unknown", "reason": "line_out_of_range"}


def _repo_edits(repos: list[dict[str, Any]]) -> list[str]:
    from agentplay import review

    doc = {"session": {"startedAt": "2026-10-01T00:00:00Z"}, "events": [], "repos": repos}
    evidence = {"edits": [{**_edit("s1", i, ["a"], ["b"]), "seq": i, "repo": r["id"], "path": r["id"]}
                          for i, r in enumerate(repos)]}
    return sorted(review._observed_edits({"s1": (doc, evidence)}, review.PullRequest("acme/app", 7)))


def test_review_takes_edits_from_the_pr_remote_before_a_same_named_repo():
    assert _repo_edits([{"id": "r0", "name": "app", "remote": "acme/app"},
                        {"id": "r1", "name": "app", "remote": "other/app"}]) == ["r0"]


def test_review_falls_back_to_the_repo_name_for_a_fork_clone():
    assert _repo_edits([{"id": "r0", "name": "app", "remote": "me/app"},
                        {"id": "r1", "name": "web", "remote": "acme/web"}]) == ["r0"]


def test_a_review_goes_stale_for_a_session_whose_map_was_rebuilt(env, capsys):
    from agentplay import review

    _, doc = _review(env, capsys, [{"id": "c1", "path": "src/app.py", "line": 2, "body": "x"}])
    st = Store(env["sm"])
    assert doc["maps"] == {SID: st.index[SID]["contentHash"]}
    assert [r["stale"] for r in review.reviews_for(st, SID)] == [False]
    st.index[SID]["contentHash"] = "rebuilt"
    assert [r["stale"] for r in review.reviews_for(st, SID)] == [True]


def test_loaded_comments_get_unique_ids(tmp_path):
    from agentplay import review

    f = _comments(tmp_path, [{"id": "1", "path": "a", "line": 1, "body": ""}, {"path": "a", "line": 2, "body": ""},
                             {"id": "1", "path": "a", "line": 3, "body": ""}])
    ids = [c["id"] for c in review.load_comments(f)]
    assert len(set(ids)) == 3 and ids[0] == "1"
