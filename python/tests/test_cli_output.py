"""Terminal output: log lines, the progress bar, and the end-of-run summary."""

from __future__ import annotations

import io
import logging
from collections import Counter
from datetime import UTC, datetime

import pytest

from agentplay import cli, pipeline


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _restore_logging(monkeypatch):
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    monkeypatch.setattr(cli, "_progress_live", False)
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _emit(handler: logging.Handler, msg: str = "session skipped", name: str = "agentplay.x.source",
          level: int = logging.WARNING) -> None:
    handler.handle(logging.LogRecord(name, level, __file__, 1, msg, None, None))


def test_default_format_is_user_facing_with_no_module_path() -> None:
    out = io.StringIO()
    _emit(cli._log_handler(out, verbose=False))
    assert out.getvalue() == "agentplay: warning: session skipped\n"


def test_verbose_format_names_the_logger_and_level() -> None:
    out = io.StringIO()
    _emit(cli._log_handler(out, verbose=True), name="agentplay.x.source", level=logging.DEBUG)
    assert out.getvalue() == "agentplay: debug: agentplay.x.source: session skipped\n"


def test_live_progress_line_is_cleared_before_the_message() -> None:
    out = _TTY()
    cli._progress_live = True
    _emit(cli._log_handler(out, verbose=False))
    assert out.getvalue() == "\r\x1b[2Kagentplay: warning: session skipped\n"


def test_no_clear_sequence_when_no_progress_is_live() -> None:
    out = _TTY()
    _emit(cli._log_handler(out, verbose=False))
    assert "\x1b" not in out.getvalue()


def test_progress_tracks_whether_a_bar_is_on_screen(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys, "stderr", _TTY())
    cli._progress(1, 10, "")
    assert cli._progress_live
    cli._progress(10, 10, "")
    assert not cli._progress_live


def test_progress_is_not_live_when_stderr_is_not_a_tty(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys, "stderr", io.StringIO())
    cli._progress(1, 10, "")
    assert not cli._progress_live


def _report(**kw) -> pipeline.BuildReport:
    return pipeline.BuildReport(found=563, built=557, **kw)


def test_summary_lists_skip_reasons_by_count(monkeypatch, capsys) -> None:
    rep = _report(skipped=7, skip_reasons=Counter(no_conversation=4, too_large=2, empty_or_unreadable=1))
    monkeypatch.setattr(cli.pipeline, "build_all", lambda **_kw: rep)
    assert cli.main(["build"]) == 0
    assert "7 skipped (4 no conversation, 2 too large, 1 empty or unreadable)" in capsys.readouterr().out


def test_summary_has_no_breakdown_when_nothing_was_skipped(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.pipeline, "build_all", lambda **_kw: _report())
    assert cli.main(["build"]) == 0
    assert "0 skipped in" in capsys.readouterr().out


def test_main_ignores_python_warnings_unless_the_user_opted_in(monkeypatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(cli.warnings, "simplefilter", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(cli.pipeline, "build_all", lambda **_kw: _report())
    monkeypatch.setattr(cli.sys, "warnoptions", [])
    cli.main(["build"])
    assert calls == [("ignore",)]
    calls.clear()
    monkeypatch.setattr(cli.sys, "warnoptions", ["error"])
    cli.main(["build"])
    assert calls == []


def test_build_all_tallies_discovery_skips_but_not_subagents_or_other_agents(monkeypatch, tmp_path) -> None:
    class _Source:
        def discovery_stats(self):
            skipped = {"no_conversation": 2, "subagent_component": 9, "different_agent": 5}
            return {"claude-code": {"seen": 16, "indexed": 0, "skipped": skipped}}

    monkeypatch.setattr(pipeline.discover, "materialize_all", lambda *_a: {})
    monkeypatch.setattr(pipeline.discover, "local_source", lambda *_a: _Source())
    monkeypatch.setattr(pipeline.discover, "candidates", lambda *_a, **_k: [])
    rep = pipeline.build_all(store=pipeline.Store(tmp_path))
    assert rep.skipped == 2
    assert rep.skip_reasons == {"no_conversation": 2}


@pytest.mark.parametrize("scope", [{"since": datetime(2026, 1, 1, tzinfo=UTC)}, {"limit": 5}])
def test_scoped_build_does_not_count_skips_from_the_whole_disk(monkeypatch, tmp_path, scope) -> None:
    """The walk sees every file on disk; a --since/--limit run must not report those as its skips."""
    class _Source:
        def discovery_stats(self):
            return {"claude-code": {"seen": 3, "indexed": 0, "skipped": {"no_conversation": 3}}}

    monkeypatch.setattr(pipeline.discover, "materialize_all", lambda *_a: {})
    monkeypatch.setattr(pipeline.discover, "local_source", lambda *_a: _Source())
    monkeypatch.setattr(pipeline.discover, "candidates", lambda *_a, **_k: [])
    rep = pipeline.build_all(store=pipeline.Store(tmp_path), **scope)
    assert rep.skipped == 0
    assert not rep.skip_reasons


def test_agent_breakdown_adds_up_to_found(monkeypatch, capsys) -> None:
    rep = pipeline.BuildReport(found=52, built=49, skipped=3, by_agent=Counter({"claude-code": 44, "codex": 5}))
    monkeypatch.setattr(cli.pipeline, "build_all", lambda **_kw: rep)
    assert cli.main(["build"]) == 0
    assert "claude-code 44 · codex 5 · 3 not built" in capsys.readouterr().out


def test_agent_breakdown_has_no_remainder_when_everything_was_built() -> None:
    rep = pipeline.BuildReport(found=5, built=5, by_agent=Counter({"codex": 5}))
    assert cli._agent_breakdown(rep) == "codex 5"
    assert cli._agent_breakdown(pipeline.BuildReport()) == "none"


class _FakeServer:
    """What ``serve`` returns, bound to ``port``, without opening a socket."""

    def __init__(self, port: int) -> None:
        self.server_address = ("127.0.0.1", port)

    def serve_forever(self) -> None:
        pass

    def server_close(self) -> None:
        pass


@pytest.mark.parametrize(("requested", "bound", "notice"), [(7317, 62505, True), (7317, 7317, False), (0, 50123, False)])
def test_view_says_when_the_requested_port_is_busy(monkeypatch, capsys, requested, bound, notice) -> None:
    from agentplay import server

    monkeypatch.setattr(server, "serve", lambda _port, **_kw: _FakeServer(bound))
    assert cli.main(["view", "--port", str(requested), "--no-open", "--no-build"]) == 0
    out, err = capsys.readouterr()
    assert (f"Port {requested} is busy; using {bound} instead." in err) is notice
    assert f"127.0.0.1:{bound}/" in out
