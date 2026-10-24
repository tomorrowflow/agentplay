"""Files that hold no conversation are not detection failures.

`_build_index()` used to log a WARNING ("agent detection failed") for every
`.jsonl` no agent claimed, which buried real format drift under Claude
metadata-only files and Cursor subagent stubs. These tests pin the split: a
file whose records are all known non-conversation metadata is a quiet
`no_conversation` skip, subagent files are dropped by path before any
detection runs, and only a file that looks conversational yet matches no
agent still warns.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from agentplay._core.ingestion.coding_agent._sources.local import source as source_mod
from agentplay._core.ingestion.coding_agent._sources.local.source import LocalSource

DETECTION_WARNING = "agent detection failed"


def _jsonl(records: list[dict]) -> bytes:
    return b"\n".join(json.dumps(r).encode() for r in records) + b"\n"


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def _claude_home(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / ".claude"
    slug = home / "projects" / "-repo"
    slug.mkdir(parents=True)
    return home, slug


def _cursor_home(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / ".cursor"
    conv = home / "projects" / "-repo" / "agent-transcripts" / "conv1"
    (conv / "subagents").mkdir(parents=True)
    return home, conv


def test_claude_metadata_only_file_is_quietly_skipped(tmp_path: Path, caplog) -> None:
    home, slug = _claude_home(tmp_path)
    (slug / "meta.jsonl").write_bytes(
        _jsonl([{"type": "ai-title", "aiTitle": "x"}, {"type": "agent-name", "agentName": "y"}])
    )
    source = LocalSource(claude_code_homes=[home], codex_homes=[])

    with caplog.at_level(logging.DEBUG):
        source.discover_sessions()

    assert _warnings(caplog) == []
    assert source.discovery_stats()["claude-code"]["skipped"] == {"no_conversation": 1}


def test_claude_continued_session_stub_is_quietly_skipped(tmp_path: Path, caplog) -> None:
    # A session that ran only a local command, then went on in another session:
    # the shape Claude Code leaves behind, `continued-in` last.
    home, slug = _claude_home(tmp_path)
    sid = "s1"
    (slug / "stub.jsonl").write_bytes(
        _jsonl([
            {"type": "last-prompt", "leafUuid": "u1", "sessionId": sid},
            {"type": "mode", "mode": "normal", "sessionId": sid},
            {"type": "permission-mode", "permissionMode": "default", "sessionId": sid},
            {"type": "system", "subtype": "local_command", "content": "<command-name>/x</command-name>",
             "sessionId": sid, "timestamp": "2026-10-14T15:36:00Z"},
            {"type": "attachment", "attachment": {}, "sessionId": sid},
            {"type": "cost-state", "totalCostUSD": 0, "sessionId": sid},
            {"type": "continued-in", "continuedInSessionId": "s2", "sessionId": sid,
             "timestamp": "2026-10-14T15:36:01Z"},
        ])
    )
    source = LocalSource(claude_code_homes=[home], codex_homes=[])

    with caplog.at_level(logging.DEBUG):
        source.discover_sessions()

    assert _warnings(caplog) == []
    assert source.discovery_stats()["claude-code"]["skipped"] == {"no_conversation": 1}


def test_conversational_file_no_agent_claims_still_warns(tmp_path: Path, caplog) -> None:
    home, slug = _claude_home(tmp_path)
    # A user turn without the `sessionId` every known shape carries: format drift.
    (slug / "drift.jsonl").write_bytes(_jsonl([{"type": "user", "message": {"content": "hi"}}]))
    source = LocalSource(claude_code_homes=[home], codex_homes=[])

    with caplog.at_level(logging.DEBUG):
        source.discover_sessions()

    assert any(DETECTION_WARNING in m for m in _warnings(caplog))
    assert source.discovery_stats()["claude-code"]["skipped"] == {"detection_failed": 1}


def test_unknown_record_type_still_warns(tmp_path: Path, caplog) -> None:
    home, slug = _claude_home(tmp_path)
    (slug / "mystery.jsonl").write_bytes(_jsonl([{"type": "workflow_journal", "note": "x"}]))
    source = LocalSource(claude_code_homes=[home], codex_homes=[])

    with caplog.at_level(logging.DEBUG):
        source.discover_sessions()

    assert any(DETECTION_WARNING in m for m in _warnings(caplog))


def test_claude_subagent_file_is_dropped_before_detection(tmp_path: Path, caplog, monkeypatch) -> None:
    home, slug = _claude_home(tmp_path)
    sub = slug / "sid" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a1.jsonl").write_bytes(_jsonl([{"type": "ai-title", "aiTitle": "x"}]))

    def no_read(*_a, **_k):
        raise AssertionError("a subagent file must not be read")

    monkeypatch.setattr(source_mod, "_read_head", no_read)
    source = LocalSource(claude_code_homes=[home], codex_homes=[])

    with caplog.at_level(logging.DEBUG):
        source.discover_sessions()

    assert _warnings(caplog) == []
    assert source.discovery_stats()["claude-code"]["skipped"] == {"subagent_component": 1}


def test_cursor_subagent_stub_is_dropped_before_detection(tmp_path: Path, caplog) -> None:
    home, conv = _cursor_home(tmp_path)
    (conv / "subagents" / "a1.jsonl").write_bytes(_jsonl([{"type": "turn_ended", "reason": "done"}]))
    source = LocalSource(claude_code_homes=[], codex_homes=[], cursor_store_homes=[home])

    with caplog.at_level(logging.DEBUG):
        source.discover_sessions()

    assert _warnings(caplog) == []
    assert source.discovery_stats()["cursor"]["skipped"] == {"subagent_component": 1}


def test_cursor_file_with_only_a_turn_marker_is_no_conversation(tmp_path: Path, caplog) -> None:
    home, conv = _cursor_home(tmp_path)
    (conv / "conv1.jsonl").write_bytes(_jsonl([{"type": "turn_ended"}]))
    source = LocalSource(claude_code_homes=[], codex_homes=[], cursor_store_homes=[home])

    with caplog.at_level(logging.DEBUG):
        source.discover_sessions()

    assert _warnings(caplog) == []
    assert source.discovery_stats()["cursor"]["skipped"] == {"no_conversation": 1}


def test_file_deleted_before_the_warning_does_not_crash(tmp_path: Path, caplog, monkeypatch) -> None:
    home, slug = _claude_home(tmp_path)
    doomed = slug / "mystery.jsonl"
    doomed.write_bytes(_jsonl([{"type": "workflow_journal", "note": "x"}]))
    real_read_head = source_mod._read_head

    def read_then_vanish(path, **kwargs):
        head = real_read_head(path, **kwargs)
        path.unlink()
        return head

    monkeypatch.setattr(source_mod, "_read_head", read_then_vanish)
    source = LocalSource(claude_code_homes=[home], codex_homes=[])

    with caplog.at_level(logging.DEBUG):
        source.discover_sessions()

    assert any(DETECTION_WARNING in m for m in _warnings(caplog))
    assert source.discovery_stats()["claude-code"]["skipped"] == {"detection_failed": 1}


def _unreadable_session(tmp_path: Path, monkeypatch) -> tuple[LocalSource, str]:
    home, slug = _claude_home(tmp_path)
    turn = {"type": "user", "sessionId": "s1", "timestamp": "2026-09-01T00:00:00Z", "cwd": "/repo"}
    (slug / "s1.jsonl").write_bytes(_jsonl([{**turn, "message": {"role": "user", "content": "hi"}}]))
    source = LocalSource(claude_code_homes=[home], codex_homes=[])
    (session_id,) = source._build_index()

    def denied(_self):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_bytes", denied)
    return source, session_id


def test_unreadable_session_file_warns_without_a_traceback(tmp_path: Path, caplog, monkeypatch) -> None:
    source, session_id = _unreadable_session(tmp_path, monkeypatch)

    with caplog.at_level(logging.INFO):
        assert source.get_spans_by_session(session_id) == []

    (record,) = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert "Permission denied" in record.getMessage()
    assert not record.exc_info


def test_unreadable_session_file_keeps_its_traceback_at_debug(tmp_path: Path, caplog, monkeypatch) -> None:
    source, session_id = _unreadable_session(tmp_path, monkeypatch)

    with caplog.at_level(logging.DEBUG):
        source.get_spans_by_session(session_id)

    (record,) = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert record.exc_info


def test_metadata_head_cut_short_by_the_read_cap_still_warns(tmp_path: Path, caplog, monkeypatch) -> None:
    """A capped head can stop before the first message, so it proves nothing about the rest."""
    home, slug = _claude_home(tmp_path)
    records = [{"type": "ai-title", "aiTitle": "x" * 5000}] * 40  # past one 64KB read
    (slug / "big.jsonl").write_bytes(_jsonl(records))
    capped = source_mod._read_head
    monkeypatch.setattr(source_mod, "_read_head", lambda p, **k: capped(p, **{**k, "max_bytes": 512}))
    source = LocalSource(claude_code_homes=[home], codex_homes=[])

    with caplog.at_level(logging.DEBUG):
        source.discover_sessions()

    assert any(DETECTION_WARNING in m for m in _warnings(caplog))
    assert "no_conversation" not in source.discovery_stats()["claude-code"]["skipped"]


def test_unhashable_record_type_does_not_break_discovery(tmp_path: Path, caplog) -> None:
    home, slug = _claude_home(tmp_path)
    (slug / "odd.jsonl").write_bytes(_jsonl([{"type": ["x"]}, {"type": "ai-title"}]))
    source = LocalSource(claude_code_homes=[home], codex_homes=[])

    with caplog.at_level(logging.DEBUG):
        source.discover_sessions()

    assert source.discovery_stats()["claude-code"]["skipped"] == {"detection_failed": 1}
