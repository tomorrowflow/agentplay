"""`agentplay factory-digest`: factory session maps joined with Workboard cards."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from agentplay import cli, pipeline
from agentplay import factory_digest as fd
from agentplay.store import Store

CARD_A = "aaaaaaaa-1111-4000-8000-000000000001"
CARD_B = "bbbbbbbb-2222-4000-8000-000000000002"
CARD_GONE = "cccccccc-3333-4000-8000-000000000003"
NOW = datetime(2026, 10, 17, 6, 0, tzinfo=UTC)
DAY = datetime(2026, 10, 15, tzinfo=UTC)


def _at(hh: int, mm: int, ss: int = 0) -> datetime:
    return DAY.replace(hour=hh, minute=mm, second=ss)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# One step is (when, what, context tokens). `what` is a Bash command, "edit",
# or "deny" (a Bash call the user refused).
Step = tuple[datetime, str, int]


def _write_session(home: Path, project: str, stem: str, steps: list[Step]) -> None:
    proj = home / ".claude" / "projects" / project
    proj.mkdir(parents=True, exist_ok=True)
    cwd = "/home/factory/factory-worktrees/wb"

    def rec(when: datetime, **d: Any) -> str:
        return json.dumps({"timestamp": _iso(when), "cwd": cwd, "sessionId": stem, **d}) + "\n"

    first = steps[0][0] - timedelta(seconds=5)
    lines = [rec(first, type="user", message={"role": "user", "content": "do the card"})]
    for i, (when, what, tokens) in enumerate(steps):
        tid = f"t{i}"
        if what == "edit":
            name, inp = "Edit", {"file_path": f"{cwd}/src/a{i}.py", "old_string": "a", "new_string": "b"}
        else:
            name, inp = "Bash", {"command": "rm -rf build" if what == "deny" else what}
        failed = what == "deny" or what.endswith("--fail")
        out = "Permission denied: the user doesn't want to proceed" if what == "deny" else "ok"
        lines.append(rec(when, type="assistant", message={
            "id": f"m{i}", "model": "claude-sonnet-4-5", "role": "assistant",
            "usage": {"input_tokens": tokens, "output_tokens": 5},
            "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}))
        lines.append(rec(when + timedelta(seconds=1), type="user", message={"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": out, "is_error": failed}]}))
    (proj / f"{stem}.jsonl").write_text("".join(lines))


def _wb(card: str) -> str:
    return f"-home-factory-factory-worktrees-wb-{card}"


def _stem(n: int) -> str:
    return f"55555555-0000-4000-8000-{n:012d}"


def _sid(n: int) -> str:
    return f"local:claude-code:{_stem(n)}"


def _attempt(aid: str, status: str, start: datetime, end: datetime | None) -> dict[str, Any]:
    a: dict[str, Any] = {"id": aid, "status": status, "startedAt": _ms(start)}
    if end is not None:
        a["endedAt"] = _ms(end)
    return a


def _card(cid: str, title: str, attempts: list[dict[str, Any]], *, published: datetime | None = None,
          accepted: datetime | None = None, **extra: Any) -> dict[str, Any]:
    worktree: dict[str, Any] = {"branch": f"wb/{cid[:8]}", "baseCommit": "b" * 40}
    if published is not None:
        worktree["handoff"] = {"phase": "published", "publishedAt": _ms(published), "headCommit": "h" * 40,
                               "pullRequestUrl": "https://github.com/acme/app/pull/12"}
    if accepted is not None:
        worktree["handoff"] = {"phase": "imported"}
        worktree["rework"] = {"round": 1, "pullRequestUrl": "https://github.com/acme/app/pull/13",
                              "acceptedAt": _ms(accepted)}
    return {"id": cid, "title": title, "labels": ["factory"], "status": "review", "unknownField": 1,
            "metadata": {"attempts": attempts, "automation": {"target": {"worktree": worktree}}}, **extra}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("AGENTPLAY_HOME", str(tmp_path / "sm"))
    monkeypatch.delenv("AGENTPLAY_CLAUDE_HOMES", raising=False)

    def build(sessions: dict[str, tuple[str, list[Step]]]) -> Store:
        for stem, (project, steps) in sessions.items():
            _write_session(home, project, stem, steps)
        st = Store(tmp_path / "sm")
        pipeline.build_all(agents={"claude-code"}, store=st)
        return Store(tmp_path / "sm")

    return {"build": build, "tmp": tmp_path, "sm": tmp_path / "sm"}


def _digest(st: Store, cards: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    return fd.build_digest(fd.parse_cards({"cards": cards}), fd.load_sessions(st), now_ms=_ms(NOW),
                           days=kw.get("days", 7), prefixes=kw.get("prefixes", []))


def _by_id(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["cardId"]: c for c in doc["cards"]}


# ---------------------------------------------------------------- join
def test_sessions_join_cards_by_worktree_and_the_rest_is_reported(env):
    st = env["build"]({
        _stem(1): (_wb(CARD_A), [(_at(10, 1), "edit", 1000)]),
        _stem(2): (_wb(CARD_GONE), [(_at(10, 1), "edit", 1000)]),
        _stem(3): ("-work-app", [(_at(10, 1), "edit", 1000)]),
    })
    doc = _digest(st, [
        _card(CARD_A, "Card A", [_attempt("a1", "succeeded", _at(10, 0), _at(10, 30))], published=_at(11, 0)),
        _card(CARD_B, "Card B", [_attempt("b1", "succeeded", _at(10, 0), _at(10, 30))], published=_at(11, 0)),
    ])
    cards = _by_id(doc)
    assert cards[CARD_A]["sessions"] == [_sid(1)] and cards[CARD_A]["totals"]["edits"] == 1
    assert cards[CARD_A]["flags"] == ["no-verify-after-edit"]
    assert cards[CARD_B]["sessions"] == [] and cards[CARD_B]["totals"] is None
    assert cards[CARD_B]["flags"] == ["no-transcript"]
    assert cards[CARD_B]["attempts"][0]["fidelity"] == "no_events"
    assert doc["unmatchedSessions"] == [_sid(2)]


# ---------------------------------------------------------------- attempt split
def test_one_transcript_resumed_for_rework_splits_into_its_attempts(env):
    st = env["build"]({_stem(1): (_wb(CARD_A), [
        (_at(9, 59, 10), "echo warmup", 100),
        (_at(10, 1), "edit", 5000),
        (_at(10, 2), "pytest -q", 8000),
        (_at(10, 31), "pytest -q", 20000),
        (_at(10, 32), "edit", 30000),
    ])})
    doc = _digest(st, [_card(CARD_A, "Card A", [
        _attempt("first", "succeeded", _at(10, 0), _at(10, 5)),
        _attempt("rework", "succeeded", _at(10, 30), _at(10, 40)),
    ], published=_at(11, 0))])
    card = _by_id(doc)[CARD_A]
    first, rework = (a["metrics"] for a in card["attempts"])
    assert (first["peakContext"], first["tokens"], first["requests"]) == (8000, 13010, 2)
    assert (rework["peakContext"], rework["tokens"], rework["requests"]) == (30000, 50010, 2)
    assert first["edits"] == rework["edits"] == 1
    assert first["verify"]["afterLastEdit"] is True and first["verify"]["verbsAfterLastEdit"] == ["pytest"]
    assert rework["verify"]["afterLastEdit"] is False
    assert 0 < first["costUsd"] < rework["costUsd"]
    unattributed = card["unattributed"]
    assert (unattributed["requests"], unattributed["peakContext"], unattributed["calls"]) == (1, 100, 1)
    total = card["totals"]
    assert total["costUsd"] == pytest.approx(first["costUsd"] + rework["costUsd"] + unattributed["costUsd"], abs=2e-4)
    assert total["verify"]["afterLastEdit"] is False


def test_an_event_in_the_overlap_goes_to_the_later_attempt_and_slack_covers_whole_seconds(env):
    st = env["build"]({_stem(1): (_wb(CARD_A), [
        (_at(10, 1), "edit", 1000),
        (_at(10, 10), "pytest -q", 2000),
    ])})
    doc = _digest(st, [_card(CARD_A, "Card A", [
        _attempt("outer", "failed", _at(10, 0), None),
        # the card rounds to seconds, so its end can fall just before the last call
        _attempt("inner", "succeeded", _at(10, 5), _at(10, 10) - timedelta(milliseconds=500)),
    ], published=_at(11, 0))])
    outer, inner = _by_id(doc)[CARD_A]["attempts"]
    assert (outer["metrics"]["edits"], outer["metrics"]["requests"]) == (1, 1)
    assert (inner["metrics"]["requests"], inner["metrics"]["calls"]) == (1, 1)
    assert inner["metrics"]["verify"]["lastVerifyFailed"] is False


def test_a_card_with_two_sessions_is_one_timeline(env):
    st = env["build"]({
        _stem(1): (_wb(CARD_A), [(_at(10, 1), "pytest", 1), (_at(10, 2), "pytest", 1), (_at(10, 3), "edit", 7)]),
        _stem(2): (_wb(CARD_A), [(_at(10, 21), "pytest", 9)]),
    })
    doc = _digest(st, [_card(CARD_A, "A", [
        _attempt("first", "failed", _at(10, 0), _at(10, 10)),
        _attempt("second", "succeeded", _at(10, 20), _at(10, 30)),
    ], published=_at(11, 0))])
    card = _by_id(doc)[CARD_A]
    assert card["sessions"] == [_sid(1), _sid(2)]
    assert card["totals"]["verify"]["afterLastEdit"] is True and card["totals"]["peakContext"] == 9
    assert [a["metrics"]["requests"] for a in card["attempts"]] == [3, 1]
    assert card["flags"] == []


# ---------------------------------------------------------------- verify after the last edit
def test_verify_after_last_edit_and_a_failing_last_verify(env):
    cards = {n: f"dddddddd-0000-4000-8000-00000000000{n}" for n in range(1, 5)}
    st = env["build"]({
        _stem(1): (_wb(cards[1]), [(_at(10, 1), "edit", 1), (_at(10, 2), "git diff --check", 1)]),
        _stem(2): (_wb(cards[2]), [(_at(10, 1), "pytest", 1), (_at(10, 2), "edit", 1)]),
        _stem(3): (_wb(cards[3]), [(_at(10, 1), "pytest", 1)]),
        _stem(4): (_wb(cards[4]), [(_at(10, 1), "edit", 1), (_at(10, 2), "pytest --fail", 1)]),
    })
    doc = _digest(st, [
        _card(cid, f"V{n}", [_attempt(f"a{n}", "succeeded", _at(10, 0), _at(10, 30))], published=_at(11, 0))
        for n, cid in cards.items()
    ])
    got = {c["title"]: (c["totals"]["verify"], c["flags"]) for c in doc["cards"]}
    assert got["V1"] == ({"afterLastEdit": True, "verbsAfterLastEdit": ["git diff"], "lastVerifyFailed": False}, [])
    assert got["V2"] == ({"afterLastEdit": False, "verbsAfterLastEdit": [], "lastVerifyFailed": False},
                         ["no-verify-after-edit"])
    assert got["V3"] == ({"afterLastEdit": None, "verbsAfterLastEdit": [], "lastVerifyFailed": False}, [])
    assert got["V4"] == ({"afterLastEdit": True, "verbsAfterLastEdit": ["pytest"], "lastVerifyFailed": True},
                         ["verify-failed-last"])


# ---------------------------------------------------------------- fidelity
def test_a_stopped_attempt_that_ends_on_permission_denials_is_a_mismatch(env):
    steps: list[Step] = [(_at(10, 1), "edit", 1), (_at(10, 2), "deny", 1), (_at(10, 3), "deny", 1)]
    st = env["build"]({_stem(1): (_wb(CARD_A), steps), _stem(2): (_wb(CARD_B), steps)})
    doc = _digest(st, [
        _card(CARD_A, "stopped", [_attempt("s", "stopped", _at(10, 0), _at(10, 30))], published=_at(11, 0)),
        _card(CARD_B, "failed", [_attempt("f", "failed", _at(10, 0), _at(10, 30))], published=_at(11, 0)),
    ])
    stopped, failed = (_by_id(doc)[c] for c in (CARD_A, CARD_B))
    attempt = stopped["attempts"][0]
    assert (attempt["transcriptOutcome"], attempt["fidelity"]) == ("blocked", "mismatch")
    assert attempt["metrics"]["stops"] == {"permissionDenied": 2, "blocked": 0}
    assert stopped["flags"] == ["no-verify-after-edit", "stopped-on-permission", "attempt-fidelity-mismatch"]
    assert failed["attempts"][0]["fidelity"] == "ok"
    assert "attempt-fidelity-mismatch" not in failed["flags"]


def test_stopped_on_permission_means_an_attempt_ended_on_a_denial(env):
    # classify_outcome reads the last 12 events, so the recovery has to outlast them
    recovered: list[Step] = [(_at(10, 1), "deny", 1), *((_at(10, 2, s), "edit", 1) for s in range(0, 40, 5)),
                             (_at(10, 3), "pytest", 1)]
    retried: list[Step] = [(_at(10, 1), "deny", 1), (_at(10, 31), "edit", 1), (_at(10, 32), "pytest", 1)]
    st = env["build"]({_stem(1): (_wb(CARD_A), recovered), _stem(2): (_wb(CARD_B), retried)})
    attempts = [_attempt("one", "failed", _at(10, 0), _at(10, 10)),
                _attempt("two", "succeeded", _at(10, 30), _at(10, 40))]
    doc = _digest(st, [_card(CARD_A, "recovered", attempts, published=_at(11, 0)),
                       _card(CARD_B, "stopped then retried", attempts, published=_at(11, 0))])
    assert _by_id(doc)[CARD_A]["attempts"][0]["metrics"]["stops"]["permissionDenied"] == 1
    assert "stopped-on-permission" not in _by_id(doc)[CARD_A]["flags"]
    assert "stopped-on-permission" in _by_id(doc)[CARD_B]["flags"]


@pytest.mark.parametrize(
    ("status", "outcome", "expected"),
    [("stopped", "blocked", "mismatch"), ("succeeded", "blocked", "mismatch"),
     ("succeeded", "interrupted", "mismatch"), ("succeeded", "verified", "ok"), ("failed", "blocked", "ok")],
)
def test_fidelity_table(status, outcome, expected):
    assert fd.fidelity(status, outcome, True) == expected
    assert fd.fidelity(status, outcome, False) == "no_events"


# ---------------------------------------------------------------- selection
def test_window_selects_by_publish_or_rework_acceptance_and_card_picks_by_prefix():
    raw = {"cards": [
        _card(CARD_A, "recent", [], published=NOW - timedelta(days=2)),
        _card(CARD_B, "old", [], published=NOW - timedelta(days=10)),
        _card(CARD_GONE, "in rework", [], accepted=NOW - timedelta(days=3)),
        _card("eeeeeeee-0000-4000-8000-000000000001", "unpublished", []),
    ]}
    cards = fd.parse_cards(raw)

    def pick(**kw: Any) -> list[str]:
        return [c.title for c in fd.select_cards(cards, now_ms=_ms(NOW), **kw)]

    assert pick(days=7, prefixes=[]) == ["recent", "in rework"]
    assert pick(days=11, prefixes=[]) == ["recent", "old", "in rework"]
    assert pick(days=7, prefixes=["bbbb"]) == ["old"]
    assert pick(days=7, prefixes=["eeee"]) == ["unpublished"]
    in_rework = next(c for c in cards if c.title == "in rework")
    assert in_rework.pull_request_url == "https://github.com/acme/app/pull/13" and in_rework.rework_round == 1
    with pytest.raises(fd.DigestError, match="matches 0 cards"):
        pick(days=7, prefixes=["ffff"])
    with pytest.raises(fd.DigestError, match="matches 2 cards"):
        fd.select_cards(fd.parse_cards({"cards": [_card(CARD_A, "x", []), _card(CARD_A[:-1] + "9", "y", [])]}),
                        now_ms=_ms(NOW), days=7, prefixes=[CARD_A[:8]])


@pytest.mark.parametrize(
    "raw",
    [[], {"cards": "x"}, {"cards": [{"title": "no id"}]},
     {"card": {"id": "x", "metadata": {"attempts": [{"id": "a", "status": "running", "startedAt": "soon"}]}}},
     {"card": {"id": "x", "metadata": {"attempts": [{"id": "a", "status": "weird", "startedAt": 1}]}}},
     {"card": {"id": "x", "metadata": {"attempts": [{"id": "a", "status": "running", "startedAt": True}]}}}],
)
def test_malformed_cards_raise_the_digest_error(raw):
    with pytest.raises(fd.DigestError):
        fd.parse_cards(raw)


def test_show_json_shape_is_one_card():
    [card] = fd.parse_cards({"card": _card(CARD_A, "one", [_attempt("a", "running", _at(10, 0), None)])})
    assert card.id == CARD_A and card.attempts[0].ended_at is None and card.published_at is None


# ---------------------------------------------------------------- CLI
_METRICS = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["peakContext", "compactions", "costUsd", "tokens", "requests", "calls", "errors", "retries",
                 "delays", "edits", "verify", "stops"],
    "properties": {
        **{k: {"type": "integer"} for k in ("peakContext", "compactions", "tokens", "requests", "calls",
                                            "retries", "delays", "edits")},
        "costUsd": {"type": "number"},
        "errors": {"type": "object", "additionalProperties": False, "required": ["total", "bySource", "byClass"],
                   "properties": {"total": {"type": "integer"},
                                  "bySource": {"type": "object", "additionalProperties": {"type": "integer"}},
                                  "byClass": {"type": "object", "additionalProperties": {"type": "integer"}}}},
        "verify": {"type": "object", "additionalProperties": False,
                   "required": ["afterLastEdit", "verbsAfterLastEdit", "lastVerifyFailed"],
                   "properties": {"afterLastEdit": {"type": ["boolean", "null"]},
                                  "verbsAfterLastEdit": {"type": "array", "items": {"type": "string"}},
                                  "lastVerifyFailed": {"type": ["boolean", "null"]}}},
        "stops": {"type": "object", "additionalProperties": False, "required": ["permissionDenied", "blocked"],
                  "properties": {"permissionDenied": {"type": "integer"}, "blocked": {"type": "integer"}}},
    },
}
_STR_OR_NULL = {"type": ["string", "null"]}
DIGEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "builtAt", "window", "cards", "unmatchedSessions"],
    "properties": {
        "schema": {"const": "agentplay-factory-digest/v1"},
        "builtAt": {"type": "string"},
        "window": {"type": "object", "additionalProperties": False, "required": ["from", "to", "days"],
                   "properties": {"from": {"type": "string"}, "to": {"type": "string"}, "days": {"type": "integer"}}},
        "unmatchedSessions": {"type": "array", "items": {"type": "string"}},
        "cards": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["cardId", "title", "labels", "status", "publishedAt", "pullRequestUrl", "headCommit",
                         "baseCommit", "reworkRound", "reviews", "sessions", "totals", "attempts", "unattributed",
                         "flags"],
            "properties": {
                "cardId": {"type": "string"}, "title": {"type": "string"}, "status": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "publishedAt": _STR_OR_NULL, "pullRequestUrl": _STR_OR_NULL, "headCommit": _STR_OR_NULL,
                "baseCommit": _STR_OR_NULL, "reworkRound": {"type": ["integer", "null"]},
                "reviews": {"type": "array", "items": {"type": "object"}},
                "sessions": {"type": "array", "items": {"type": "string"}},
                "totals": _METRICS, "unattributed": _METRICS,
                "flags": {"type": "array", "items": {"enum": list(fd.FLAGS)}},
                "attempts": {"type": "array", "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "status", "startedAt", "endedAt", "error", "contextTokens",
                                 "transcriptOutcome", "fidelity", "metrics"],
                    "properties": {
                        "id": {"type": "string"},
                        "status": {"enum": ["running", "succeeded", "failed", "blocked", "stopped"]},
                        "startedAt": {"type": "string"}, "endedAt": _STR_OR_NULL, "error": _STR_OR_NULL,
                        "contextTokens": {"type": ["integer", "null"]},
                        "transcriptOutcome": {"type": "string"},
                        "fidelity": {"enum": ["ok", "mismatch", "no_events"]},
                        "metrics": _METRICS,
                    },
                }},
            },
        }},
    },
}


def test_cli_writes_dated_and_latest_json_and_markdown(env, capsys):
    env["build"]({_stem(1): (_wb(CARD_A), [(_at(10, 1), "edit", 42000), (_at(10, 2), "pytest", 1)])})
    cards = env["tmp"] / "cards.json"
    cards.write_text(json.dumps({"cards": [
        _card(CARD_A, "Add | the rate limiter " + "x" * 80,
              [_attempt("a1", "succeeded", _at(10, 0), _at(10, 30))], published=_at(11, 0),
              pullRequestUrl="https://github.com/acme/app/pull/77"),
        _card(CARD_B, "Card B", [], published=_at(11, 0)),
    ]}))
    out = env["tmp"] / "out"
    assert cli.main(["factory-digest", "--cards", str(cards), "--out", str(out), "--now", _iso(NOW)]) == 0
    assert "2 cards" in capsys.readouterr().out
    names = sorted(p.name for p in out.iterdir())
    assert names == ["factory-2026-10-17.json", "factory-2026-10-17.md", "factory-latest.json", "factory-latest.md"]
    doc = json.loads((out / "factory-2026-10-17.json").read_text())
    jsonschema.validate(doc, DIGEST_SCHEMA)
    assert (out / "factory-latest.json").read_text() == (out / "factory-2026-10-17.json").read_text()
    md = (out / "factory-latest.md").read_text()
    assert "# Factory digest 2026-10-10 to 2026-10-17" in md
    row = next(line for line in md.splitlines() if "rate limiter" in line)
    assert "Add \\| the rate limiter" in row and "x" * 60 not in row
    assert "[#77](https://github.com/acme/app/pull/77)" in row
    assert "| 1 (succeeded) | 42k |" in row and "| yes |" in row
    assert "- **no-transcript**: Card B" in md

    assert cli.main(["factory-digest", "--cards", str(cards), "--out", str(out), "--now", _iso(NOW),
                     "--json", "--card", CARD_A[:8]]) == 0
    printed = json.loads(capsys.readouterr().out)
    jsonschema.validate(printed, DIGEST_SCHEMA)
    assert [c["cardId"] for c in printed["cards"]] == [CARD_A]
    assert printed["cards"][0]["totals"]["peakContext"] == 42000


def test_cli_reads_stdin_and_defaults_to_the_store(env, monkeypatch, capsys):
    env["build"]({_stem(1): (_wb(CARD_A), [(_at(10, 1), "edit", 1)])})
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"card": _card(CARD_A, "A", [], published=_at(11, 0))})))
    assert cli.main(["factory-digest", "--cards", "-", "--now", _iso(NOW), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["cards"][0]["sessions"] == [_sid(1)]
    assert (env["sm"] / "digests" / "factory-latest.md").is_file()


def test_cli_reports_a_malformed_cards_file(env, capsys):
    bad = env["tmp"] / "bad.json"
    bad.write_text('{"cards": [{"title": "no id"}]}')
    assert cli.main(["factory-digest", "--cards", str(bad), "--now", _iso(NOW)]) == 1
    assert "has no id" in capsys.readouterr().err
    assert not (env["sm"] / "digests").exists()
