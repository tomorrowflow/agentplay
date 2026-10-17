"""The local review loop: edit evidence beside the map, `agentplay review`, and `/api/reviews`."""

from __future__ import annotations

import gzip
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentplay import cli, pipeline
from agentplay._core.code_authorship import line_fingerprints
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
