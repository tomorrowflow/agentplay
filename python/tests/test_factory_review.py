"""`agentplay factory-review`: factory cards' findings traced to the round's edits."""

from __future__ import annotations

import io
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agentplay import cli, pipeline, review
from agentplay import factory_digest as fd
from agentplay import factory_review as fr
from agentplay.store import Store

CARD = "aaaaaaaa-1111-4000-8000-000000000001"
STEM = "66666666-0000-4000-8000-000000000001"
SID = f"local:claude-code:{STEM}"
BASE, ROUND0, ROUND1 = ("def f():\n    return 0\n", "def f():\n    return 1\n", "def f():\n    return 2\n")
EDIT0, EDIT1 = datetime(2026, 9, 10, 1, 0, 10, tzinfo=UTC), datetime(2026, 9, 10, 1, 10, 10, tzinfo=UTC)
IMPORTED0, IMPORTED1 = datetime(2026, 9, 10, 1, 5, tzinfo=UTC), datetime(2026, 9, 10, 1, 20, tzinfo=UTC)
PUBLISHED0 = datetime(2026, 9, 10, 1, 5, 30, tzinfo=UTC)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _commit(repo: Path, content: str) -> str:
    (repo / "src" / "app.py").write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "x")
    return _git(repo, "rev-parse", "HEAD")


def _write_session(home: Path, repo: Path) -> None:
    """One session resumed for rework: round 0 writes `return 1`, round 1 rewrites it to `return 2`."""
    proj = home / ".claude" / "projects" / str(repo).replace("/", "-")
    proj.mkdir(parents=True)
    app = f"{repo}/src/app.py"

    def rec(when: datetime, **d: Any) -> str:
        return json.dumps({"timestamp": _iso(when), "cwd": str(repo), "sessionId": STEM, **d}) + "\n"

    lines = [rec(EDIT0.replace(second=0), type="user", message={"role": "user", "content": "do the card"})]
    for i, (when, old, new, before) in enumerate([(EDIT0, "0", "1", BASE), (EDIT1, "1", "2", ROUND0)]):
        inp = {"file_path": app, "old_string": f"    return {old}", "new_string": f"    return {new}"}
        lines.append(rec(when, type="assistant", message={
            "id": f"m{i}", "model": "claude-sonnet-5", "role": "assistant",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": [{"type": "tool_use", "id": f"e{i}", "name": "Edit", "input": inp}]}))
        lines.append(rec(when.replace(second=11), type="user", toolUseResult={
            "filePath": app, "oldString": inp["old_string"], "newString": inp["new_string"],
            "originalFile": before, "userModified": False, "replaceAll": False,
            "structuredPatch": [{"oldStart": 1, "oldLines": 2, "newStart": 1, "newLines": 2,
                                 "lines": [" def f():", f"-    return {old}", f"+    return {new}"]}]},
            message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"e{i}", "content": "ok"}]}))
    (proj / f"{STEM}.jsonl").write_text("".join(lines))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("AGENTPLAY_HOME", str(tmp_path / "sm"))
    monkeypatch.delenv("AGENTPLAY_CLAUDE_HOMES", raising=False)
    repo = tmp_path / "work" / f"wb-{CARD}"
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "remote", "add", "origin", "git@github.com:acme/app.git")
    base = _commit(repo, BASE)
    _write_session(home, repo)
    pipeline.build_all(agents={"claude-code"}, store=Store(tmp_path / "sm"))
    # A local origin that does not exist, so a missing commit fails here instead of reaching GitHub.
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "no-such-remote"))
    return {"repo": repo, "sm": tmp_path / "sm", "base": base, "c0": _commit(repo, ROUND0),
            "c1": _commit(repo, ROUND1)}


def _finding(line: int, *, fixed: bool = False, rule: str | None = "no-magic") -> dict[str, Any]:
    return {"path": "src/app.py", "line": line, "body": "magic number", "fixed": fixed,
            **({"rule": rule} if rule else {})}


def _card(env: dict[str, Any], items0: list[dict[str, Any]], comments: list[dict[str, Any]] = (),
          *, pr: int = 7, commit0: str | None = None) -> dict[str, Any]:
    worktree = {
        "baseCommit": env["base"],
        "handoff": {"phase": "published", "headCommit": env["c1"], "publishedAt": _ms(IMPORTED1) + 1000,
                    "pullRequestUrl": f"https://github.com/acme/app/pull/{pr}"},
        "rounds": [{"round": 0, "changes": {"to": commit0 or env["c0"]}, "review": {"items": items0}},
                   {"round": 1, "changes": {"to": env["c1"]}}],
        "reviews": [{"round": 0, "verdict": "rework", "importedAt": _ms(IMPORTED0),
                     "publishedAt": _ms(PUBLISHED0)},
                    {"round": 1, "verdict": "accept", "importedAt": _ms(IMPORTED1),
                     "publishedAt": _ms(IMPORTED1) + 1000}],
    }
    return {"id": CARD, "title": "t", "metadata": {"automation": {"target": {"worktree": worktree}},
                                                  "comments": list(comments)}}


def _run(env, capsys, monkeypatch, cards: list[dict[str, Any]], *extra: str) -> tuple[int, str, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cards": cards})))
    rc = cli.main(["factory-review", "--cards", "-", "--repo-path", str(env["repo"]), *extra])
    out, err = capsys.readouterr()
    return rc, out, err


def _doc(env) -> dict[str, Any]:
    return json.loads((env["sm"] / "reviews" / "acme_app_7.json").read_text())


def _seqs() -> list[int]:
    doc = Store().get(SID)
    return [e["seq"] for e in doc["events"] if e.get("tool") == "Edit"]


def test_a_round_zero_finding_traces_to_the_round_zero_edit_after_round_one_rewrote_the_line(
        env, capsys, monkeypatch):
    rework = {"id": "k1", "body": "Still wrong at `src/app.py:2`, see also src/app.py:L2-4",
              "createdAt": _ms(IMPORTED0) + 60_000, "source": "operator"}
    rc, out, _ = _run(env, capsys, monkeypatch, [_card(env, [_finding(2), _finding(1, fixed=True)], [rework])])
    assert rc == 0
    assert "acme/app#7" in out and "1 comments" not in out
    edit0, _ = _seqs()
    doc = _doc(env)
    assert doc["pr"]["head"] == env["c1"] and doc["sessions"] == [SID]
    pin = {"commit": env["c0"], "until": _iso(PUBLISHED0)}
    assert [{k: c[k] for k in ("id", "source", "author", "body", "commit", "until")} for c in doc["comments"]] == [
        {"id": "aaaaaaaa-r0-f0", "source": "factory-review", "author": "inner-reviewer",
         "body": "[no-magic] magic number", **pin},
        {"id": "aaaaaaaa-r0-k1-m0", "source": "rework", "author": "operator", "body": rework["body"], **pin},
    ]
    assert all(c["attribution"] == {"kind": "edit", "session": SID, "seq": edit0, "agent": "main", "shared": False}
               for c in doc["comments"])


def test_without_the_round_pin_the_same_line_is_lost_or_blamed_on_the_later_round(env):
    st, pr = Store(env["sm"]), review.PullRequest("acme/app", 7)
    pull = {"base": env["base"], "head": env["c1"]}
    edit0, edit1 = _seqs()
    comment = {"id": "c", "path": "src/app.py", "line": 2, "body": "x"}
    variants = [comment, {**comment, "commit": env["c0"]}, {**comment, "commit": env["c0"], "until": _iso(IMPORTED0)}]
    doc = review.build_review(st, pr, sessions=[SID], checkout=review.Checkout(env["repo"]), pull=pull,
                              comments=variants)
    got = [c["attribution"].get("seq", c["attribution"].get("reason")) for c in doc["comments"]]
    assert got == [edit1, "not_observed", edit0]


def test_a_round_with_no_import_time_is_pinned_to_its_publish_and_does_not_crash_the_run(env, capsys, monkeypatch):
    card = _card(env, [_finding(2)], [{"id": "k1", "body": "see src/app.py:2", "createdAt": _ms(PUBLISHED0) + 1000,
                                       "source": "operator"}])
    del card["metadata"]["automation"]["target"]["worktree"]["reviews"][0]["importedAt"]
    rc, _, err = _run(env, capsys, monkeypatch, [card])
    assert (rc, err) == (0, "")
    edit0, _ = _seqs()
    assert [(c["until"], c["attribution"].get("seq")) for c in _doc(env)["comments"]] == [(_iso(PUBLISHED0), edit0)] * 2


def test_an_edit_exactly_at_until_is_not_replayed(env):
    doc = Store(env["sm"]).get(SID)
    started = datetime.fromisoformat(doc["session"]["startedAt"].replace("Z", "+00:00"))
    edit0, _ = _seqs()
    at = started + timedelta(milliseconds=next(e["t"] for e in doc["events"] if e["seq"] == edit0))
    comment = {"id": "c", "path": "src/app.py", "line": 2, "body": "x", "commit": env["c0"]}
    got = review.build_review(Store(env["sm"]), review.PullRequest("acme/app", 7), sessions=[SID],
                              checkout=review.Checkout(env["repo"]), pull={"base": env["base"], "head": env["c1"]},
                              comments=[{**comment, "until": at.isoformat()},
                                        {**comment, "until": (at + timedelta(milliseconds=1)).isoformat()}])
    assert [c["attribution"].get("seq", c["attribution"].get("reason")) for c in got["comments"]] == [
        "no_evidence", edit0]


def test_a_card_with_only_fixed_findings_writes_no_review(env, capsys, monkeypatch):
    rc, out, _ = _run(env, capsys, monkeypatch, [_card(env, [_finding(2, fixed=True)])])
    assert rc == 0 and "No card" in out
    assert not (env["sm"] / "reviews").exists()


def test_a_commit_missing_from_the_clone_fails_that_card_only(env, capsys, monkeypatch):
    broken = _card(env, [_finding(2)], pr=8, commit0="f" * 40)
    rc, out, err = _run(env, capsys, monkeypatch, [broken, _card(env, [_finding(2)])])
    assert rc == 1
    assert "card aaaaaaaa (acme/app#8): commit ffffffffffff is not in" in err
    assert "acme/app#7" in out and (env["sm"] / "reviews" / "acme_app_7.json").exists()


def test_rerunning_overwrites_the_same_review(env, capsys, monkeypatch):
    cards = [_card(env, [_finding(2)])]
    _run(env, capsys, monkeypatch, cards)
    first = _doc(env)
    _run(env, capsys, monkeypatch, cards)
    assert [p.name for p in (env["sm"] / "reviews").iterdir()] == ["acme_app_7.json"]
    assert {k: v for k, v in _doc(env).items() if k != "builtAt"} == {k: v for k, v in first.items() if k != "builtAt"}


# ---------------------------------------------------------------- planning, no git
def _parsed(rounds: list[dict[str, Any]], reviews: list[dict[str, Any]], comments: list[dict[str, Any]]) -> fd.Card:
    worktree = {"rounds": rounds, "reviews": reviews}
    return fd.parse_cards({"card": {"id": CARD, "metadata": {"automation": {"target": {"worktree": worktree}},
                                                              "comments": comments}}})[0]


_ROUNDS = [{"round": 0, "changes": {"to": "c0"}}, {"round": 1, "changes": {"to": "c1"}}]
_REVIEWS = [{"round": 0, "importedAt": 1000, "publishedAt": 2000}, {"round": 1, "importedAt": 5000, "publishedAt": 6000}]


@pytest.mark.parametrize(("source", "body", "at", "expected"), [
    # Workboard's own done summary, posted before the rework round's import
    (None, "Done. Fixed a/b.py:3", 4900, None),
    ("operator", "a/b.py:3 is still wrong", 3000, "c0"),
    # at a round's publish the comment reviews it; at the next round's import it no longer does
    ("operator", "a/b.py:3 is still wrong", 2000, "c0"),
    ("operator", "a/b.py:3 is still wrong", 4999, "c0"),
    ("operator", "a/b.py:3 is still wrong", 5000, None),
    ("operator", "a/b.py:3 is still wrong", 6000, "c1"),
    ("operator", "a/b.py:3 is still wrong", 7000, "c1"),
    # answers to a needs_input question, before the round's PR is published
    ("operator", "Yes, keep a/b.py:3", 1500, None),
    ("operator", "Yes, keep a/b.py:3", 5500, None),
    ("operator", "a/b.py:3 predates every PR", 500, None),
    ("agent:dev", "Verdict: rework: a/b.py:3 is wrong", 1500, "c0"),
    ("agent:dev", "Verdict: rework: a/b.py:3 is wrong", 1000, "c0"),
    ("agent:dev", "Verdict: rework: a/b.py:3 is wrong", 999, None),
    ("agent:dev", "Verdict: rework: a/b.py:3 is wrong", 3000, "c0"),
    ("agent:dev", "**Verdict:** rework, a/b.py:3", 5500, "c1"),
    ("agent:dev", "Verdict: accept, a/b.py:3 looks fine", 1500, None),
    ("agent:dev", "Verdict: reworked a/b.py:3", 1500, None),
    ("agent:dev", "Looked at a/b.py:3", 1500, None),
    ("operator-ish", "Verdict: rework: a/b.py:3", 1500, None),
])
def test_only_operator_reviews_and_agent_rework_verdicts_are_traced(source, body, at, expected):
    comment = {"id": "k", "body": body, "createdAt": at, **({"source": source} if source else {})}
    card = _parsed(_ROUNDS, _REVIEWS, [comment])
    got = [(c["commit"], c["line"], c["author"]) for c in fr.card_comments(card, "u")]
    assert got == ([] if expected is None else [(expected, 3, source)])


def test_a_round_without_its_final_commit_traces_nothing():
    card = _parsed([{"round": 0, "review": {"items": [_finding(2)]}}],
                   [{"round": 0, "importedAt": 1000, "publishedAt": 2000}],
                   [{"id": "k", "body": "src/app.py:2", "createdAt": 3000, "source": "operator"}])
    assert fr.card_comments(card, "u") == []


@pytest.mark.parametrize(("review", "until"), [
    ({"importedAt": 1000, "publishedAt": 2000}, 2000),
    ({"importedAt": 1000}, 1000),
    ({"publishedAt": 2000}, 2000),
    ({}, None),
])
def test_until_is_the_publish_else_the_import_else_absent(review, until):
    card = _parsed([{"round": 0, "changes": {"to": "c0"}, "review": {"items": [_finding(2)]}}],
                   [{"round": 0, **review}], [])
    [c] = fr.card_comments(card, "u")
    assert c.get("until") == fd.iso(until) and ("until" in c) == (until is not None)


def test_malformed_comments_rounds_and_reviews_are_dropped_and_the_card_still_digests():
    rounds = [{"round": 0, "changes": {"to": "c0"}}, "junk", {"round": "1", "changes": {"to": "c1"}},
              {"round": 2, "changes": "x", "review": {"items": "x"}}]
    reviews = [{"round": 0, "importedAt": 1000, "publishedAt": True}, {"round": "2", "importedAt": 3000}]
    comments = [{"id": "ok", "body": "a/b.py:3", "createdAt": 1500, "source": "operator"},
                "junk", {"id": 7, "createdAt": 1}, {"id": "k", "createdAt": "soon"}, {"id": "b", "body": 3, "createdAt": 1}]
    card = _parsed(rounds, reviews, comments)
    assert card.rounds == (fd.Round(0, "c0", 1000, None, ()), fd.Round(2, None, None, None, ()))
    assert [(c.id, c.body) for c in card.comments] == [("ok", "a/b.py:3"), ("b", "")]
    assert fd.card_digest(card, [], {})["cardId"] == CARD
    assert fd.parse_cards({"card": {"id": CARD, "metadata": {"comments": "x", "automation": {"target": {
        "worktree": {"rounds": "x"}}}}}})[0].comments == ()


def test_invalid_finding_items_are_dropped():
    items = [_finding(2), _finding(0), {"path": "", "line": 1, "body": "x", "fixed": False},
             {"path": "a", "line": True, "body": "x", "fixed": False}, {"path": "a", "line": 1, "body": "x"}, "junk"]
    card = _parsed([{"round": 0, "changes": {"to": "c0"}, "review": {"items": items}}], [], [])
    assert card.rounds[0].findings == (fd.Finding("src/app.py", 2, "magic number", "no-magic", False),)


@pytest.mark.parametrize(("text", "expected"), [
    ("see `src/app.py:12`", [("src/app.py", 12)]),
    ("src/app.py:L12 and ./lib/x.ts:3-9", [("src/app.py", 12), ("lib/x.ts", 3)]),
    ("README.md:4, again README.md:4.", [("README.md", 4)]),
    ("ios/Sources/Make file:2", []),
    ("Makefile:3 ios/Makefile:3", [("ios/Makefile", 3)]),
    ("127.0.0.1:8099 v1.2:3 10:30 ghcr.io/astral-sh/ruff:0", []),
    ("https://github.com/acme/app/blob/main/src/app.py:4", []),
])
def test_path_line_mentions(text, expected):
    assert fr.mentions(text) == expected


def test_review_input_keeps_commit_and_until_and_rejects_a_bad_time(tmp_path):
    f = tmp_path / "c.json"
    comments = [{"id": "a", "path": "p", "line": 1, "body": "", "commit": "abc", "until": "2026-09-10T01:05:00Z"},
                {"id": "b", "path": "p", "line": 1, "body": ""}]
    f.write_text(json.dumps({"schema": "agentplay-review-input/v1", "comments": comments}))
    a, b = review.load_comments(f)
    assert (a["commit"], a["until"]) == ("abc", "2026-09-10T01:05:00Z") and "commit" not in b and "until" not in b
    f.write_text(json.dumps({"schema": "agentplay-review-input/v1", "comments": [{**comments[0], "until": "soon"}]}))
    with pytest.raises(review.ReviewError, match="ISO-8601"):
        review.load_comments(f)
