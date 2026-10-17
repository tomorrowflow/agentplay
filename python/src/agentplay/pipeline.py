"""Build maps for every session on this machine into the local store."""

from __future__ import annotations

import logging
import os
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from . import __version__, builder, discover
from .store import Store

logger = logging.getLogger("agentplay.pipeline")


@dataclass
class BuildReport:
    found: int = 0
    built: int = 0
    unchanged: int = 0
    skipped: int = 0
    skip_reasons: Counter = field(default_factory=Counter)
    failed: int = 0
    decoded: dict[str, int] = field(default_factory=dict)
    by_agent: Counter = field(default_factory=Counter)
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    def skip(self, reason: str, n: int = 1) -> None:
        """Count ``n`` sessions or files left out of the run, and why."""
        self.skipped += n
        self.skip_reasons[reason] += n


def _row(
    doc: dict[str, Any],
    cand: discover.Candidate,
    title: str | None,
    task_titles: list[str | None] | None = None,
) -> dict[str, Any]:
    s = builder.summary(doc)
    started = s.get("startedAt") or ""
    ended = None
    try:
        t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
        ended = datetime.fromtimestamp(t0.timestamp() + (s.get("durationMs") or 0) / 1000, tz=UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        pass
    return {
        **s,
        "sessionId": doc["session"]["id"],
        "sessionDate": started[:10] or None,
        "endedAt": ended,
        "sessionTitle": title,
        "taskTitles": task_titles,
        "contentHash": builder.content_hash(doc),
        "bytesGz": None,
        "updatedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sourceMtime": cand.mtime,
        "sourcePath": cand.path,
        "builtWith": __version__,
        "pathMode": (doc.get("session") or {}).get("pathMode") or "relative",
    }


def _sync_titles(store: Store, cand: discover.Candidate, source: Any, *, titles: bool) -> bool:
    """Bring an unchanged session's stored titles in line with this run's ``titles``.

    ``--no-titles`` is a per-run switch, not a stored preference, so it has to
    work in both directions on a session the build path never reaches:

    * **off clears** what earlier runs stored. The first prompt and every task
      label are prompt text sitting in the index, and an opt-out that only
      covers sessions built from here on leaves all of it there, still served
      with every local map. The switch has to reach data already on disk or it
      is not a privacy switch.
    * **on fills** a row that has none: the shape of a row written before task
      titles existed, and the shape a ``--no-titles`` run leaves behind.
      Without this such a row keeps rendering "Task 2" until its transcript
      changes or the user runs ``--rebuild``.

    Only the unchanged path needs this. A rebuilt session gets a whole new row
    from :func:`_row`, which already honours ``titles``. Titles live only in
    the index, so neither direction touches a map document.

    "Unfilled" means ``None`` as well as absent, and that is what lets the two
    directions compose: a cleared row has to be indistinguishable from one
    written before titles existed, or the next default run would read its own
    clearing as "already done" and never put the labels back. The cost is that
    a session whose prompts clean down to nothing is re-read once per run; the
    alternative is a stored ``None`` no later run can tell apart from an
    opt-out, which is the bug this replaces.

    Args:
        store: the store holding the row (mutated in place; the caller saves).
        cand: the discovered session whose row is being synced.
        source: the shared span source, for agents read through it.
        titles: whether this run keeps local titles at all.

    Returns:
        True when the row was changed, False when it already matched this run's
        ``titles`` or there is no row for this session.
    """
    row = store.index.get(cand.session_id)
    if row is None:
        return False
    if not titles:
        if row.get("sessionTitle") is None and row.get("taskTitles") is None:
            return False
        row["sessionTitle"] = None
        row["taskTitles"] = None
        return True
    changed = False
    if row.get("sessionTitle") is None:
        row["sessionTitle"] = discover.local_title(cand, source)
        changed = True
    if row.get("taskTitles") is None:
        row["taskTitles"] = discover.local_task_titles(cand, source)
        changed = True
    return changed


def build_all(
    *,
    agents: set[str] | None = None,
    since: datetime | None = None,
    rebuild: bool = False,
    path_mode: str = "relative",
    limit: int | None = None,
    titles: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
    store: Store | None = None,
) -> BuildReport:
    """Discover sessions and (re)build their maps into the local store.

    Args:
        agents: agent names to include (default: all supported).
        since: only sessions whose transcript changed at or after this time.
        rebuild: ignore the incremental check and rebuild everything selected.
        path_mode: ``relative`` | ``dirs_only`` | ``hashed`` (see the format spec).
        limit: newest N sessions only.
        titles: keep a local title (Claude Code's own title, else the first prompt,
            truncated) and one label per task in the index for the session list.
            Never written into maps. Per run, not a stored preference: off also
            clears what earlier runs stored for the sessions this run sees, and
            on puts it back.
        progress: ``callback(done, total, session_id)``.
        store: target store (default ``~/.agentplay``).
    """
    t0 = time.monotonic()
    store = store or Store()
    agents = set(agents or discover.AGENTS)
    homes = discover.Homes.default()
    report = BuildReport()
    key = os.environ.get("AGENTPLAY_HMAC_KEY", "")
    hmac_key = key.encode("utf-8") if key else None
    # What the builder will actually emit: `hashed` without a key falls back.
    effective_mode = "dirs_only" if path_mode == "hashed" and not hmac_key else path_mode
    report.decoded = discover.materialize_all(homes, agents)
    source = discover.local_source(homes, agents)
    cands = [c for c in discover.candidates(source, since=since) if c.agent in agents or not c.agent]
    if limit:
        cands = cands[:limit]
    report.found = len(cands)
    # Files the walk set aside before they became candidates. Another agent's
    # file (picked up by that agent's own walk) and a subagent transcript (folded
    # into its parent session) are not sessions, so they are not skips. The walk
    # covers the whole disk, so a --since/--limit run has no skips of this kind
    # to report: they belong to sessions outside its scope.
    if since is None and not limit:
        for walk in source.discovery_stats().values():
            for reason, n in walk["skipped"].items():
                if reason not in ("different_agent", "subagent_component"):
                    report.skip(reason, n)
    for i, c in enumerate(cands):
        if progress:
            progress(i, len(cands), c.session_id)
        if not rebuild and store.is_fresh(c.session_id, c.mtime, built_with=__version__, path_mode=effective_mode):
            report.unchanged += 1
            report.by_agent[c.agent] += 1
            _sync_titles(store, c, source, titles=titles)
            continue
        try:
            doc, evidence = discover.build_map(
                c, source, path_mode=path_mode, hmac_key=hmac_key, version=__version__)
        except discover.SkipSession as exc:
            report.skip(str(exc))
            continue
        except Exception as exc:  # noqa: BLE001 - one bad transcript must not stop the run
            report.failed += 1
            report.errors.append(f"{c.session_id}: {type(exc).__name__}: {exc}")
            logger.debug("build failed for %s", c.session_id, exc_info=True)
            continue
        if not doc.get("events"):
            report.skip("empty_events")
            continue
        doc["session"]["id"] = c.session_id if not doc["session"].get("id") else doc["session"]["id"]
        title = discover.local_title(c, source) if titles else None
        task_titles = discover.local_task_titles(c, source) if titles else None
        store.put(doc, _row(doc, c, title, task_titles), evidence=evidence)
        report.built += 1
        report.by_agent[c.agent] += 1
    store.save_index()
    if progress:
        progress(len(cands), len(cands), "")
    report.seconds = round(time.monotonic() - t0, 2)
    return report
