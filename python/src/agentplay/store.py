"""The local map store: ``~/.agentplay/maps`` plus a JSON index.

Layout (override the root with ``AGENTPLAY_HOME``)::

    ~/.agentplay/
      index.json              session_id -> index row (summary, hash, source mtime)
      maps/<key>.json         one agentplay/v1 document per session
      evidence/<key>.json     local-only edit evidence for that map (Claude Code), never exported
      reviews/<pr>.json       local-only review docs written by `agentplay review`
      digests/factory-*.json  local-only digests written by `agentplay factory-digest`
      cache/<agent>/...       decoded transcripts for database-backed agents

Builds are incremental: a session is rebuilt only when its transcript's mtime
changed since the stored build (or ``--rebuild`` is passed).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

INDEX_VERSION = 1


def root() -> Path:
    return Path(os.environ.get("AGENTPLAY_HOME") or Path.home() / ".agentplay")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# What `map_key` produces. A key read back from the index is checked against it,
# so a hand-edited or planted index can't point a read outside `maps/`.
_KEY_RE = re.compile(r"[A-Za-z0-9._-]{1,80}-[0-9a-f]{10}")


def map_key(session_id: str) -> str:
    """Filesystem-safe, collision-free file stem for a session id."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", session_id)[:80]
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:10]
    return f"{safe}-{digest}"


class Store:
    """Read/write access to the local index and map files."""

    def __init__(self, base: Path | None = None) -> None:
        self.base = base or root()
        self.maps_dir = self.base / "maps"
        self.evidence_dir = self.base / "evidence"
        self.index_path = self.base / "index.json"
        self._index: dict[str, dict[str, Any]] | None = None
        self._index_stamp: tuple[int, int] | None = None

    # ------------------------------------------------------------------ index
    @property
    def index(self) -> dict[str, dict[str, Any]]:
        index = self._index
        if index is None:
            try:
                raw = json.loads(self.index_path.read_text(encoding="utf-8"))
                index = raw.get("sessions", {}) if raw.get("version") == INDEX_VERSION else {}
            except (OSError, ValueError, AttributeError):
                index = {}
            self._index = index
            self._index_stamp = self._disk_stamp()
        return index

    def _disk_stamp(self) -> tuple[int, int] | None:
        # Size as well as mtime: Windows file times tick about every 15 ms, so two
        # writes in a row can share an mtime.
        try:
            st = self.index_path.stat()
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    def reload_if_changed(self) -> bool:
        """Drop the cached index when index.json changed underneath us.

        The server holds one Store for its lifetime, and `agentplay view` now scans
        in a background thread, so the index it serves would otherwise be whatever
        existed at startup.

        Explicit rather than a check inside `index`: `build_all` does many `put`s
        before a single `save_index`, and reloading between them would discard
        every pending row.
        """
        if self._index is None:
            return False
        stamp = self._disk_stamp()
        if stamp is None or stamp == self._index_stamp:
            return False
        self._index = None
        return True

    def save_index(self) -> None:
        body = json.dumps({"version": INDEX_VERSION, "sessions": self.index}, separators=(",", ":"))
        _atomic_write(self.index_path, body.encode("utf-8"))
        self._index_stamp = self._disk_stamp()

    def is_fresh(
        self, session_id: str, source_mtime: float, built_with: str | None = None, path_mode: str | None = None
    ) -> bool:
        row = self.index.get(session_id)
        if not row:
            return False
        if row.get("imported"):
            return True  # an imported map has no local transcript to rebuild from
        if abs(float(row.get("sourceMtime") or 0) - source_mtime) > 0.5:
            return False
        # A map built by another version is rebuilt once: redaction fixes (and any
        # other change to what a map holds) must reach maps already on disk.
        if built_with is not None and row.get("builtWith") != built_with:
            return False
        # Asking for another path mode (say dirs_only after a relative build) must
        # re-redact maps already on disk, not only new sessions.
        if path_mode is not None and (row.get("pathMode") or "relative") != path_mode:
            return False
        return (self.maps_dir / f"{row['key']}.json").is_file()

    # ------------------------------------------------------------------ maps
    def put(self, doc: dict[str, Any], row: dict[str, Any], *, evidence: dict[str, Any] | None = None) -> None:
        """Store one map document, its index row and its local-only edit evidence.

        The evidence file is written or removed together with the map, so a
        rebuild or an import never leaves one behind that cites another map's seqs.
        """
        sid = doc["session"]["id"]
        key = map_key(sid)
        _atomic_write(self.maps_dir / f"{key}.json", _canonical(doc))
        evidence_path = self.evidence_dir / f"{key}.json"
        if evidence is None:
            evidence_path.unlink(missing_ok=True)
        else:
            _atomic_write(evidence_path, _canonical({**evidence, "sessionId": sid}))
        self.index[sid] = {**row, "key": key}

    def get(self, session_id: str) -> dict[str, Any] | None:
        return self._read(self.maps_dir, session_id)

    def evidence(self, session_id: str) -> dict[str, Any] | None:
        """The session's ``agentplay-evidence/v1`` document, or None when it has none."""
        return self._read(self.evidence_dir, session_id)

    def _read(self, folder: Path, session_id: str) -> dict[str, Any] | None:
        row = self.index.get(session_id)
        key = row.get("key") if row else None
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            return None
        try:
            base = folder.resolve()
            target = (base / f"{key}.json").resolve()
            if not target.is_relative_to(base):  # e.g. a symlink planted in maps/
                return None
            return json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def rows(self) -> list[dict[str, Any]]:
        """Index rows, newest first."""
        return sorted(self.index.values(), key=lambda r: r.get("startedAt") or "", reverse=True)

    def docs(self, *, since: str | None = None, main_only: bool = True) -> Iterator[dict[str, Any]]:
        """Stored map documents, newest first.

        Args:
            since: ISO date (YYYY-MM-DD); skip sessions that started earlier.
            main_only: skip subagent and helper sessions (their work is already
                inside the parent map).
        """
        for row in self.rows():
            if since and (row.get("startedAt") or "")[:10] < since:
                continue
            if main_only and row.get("role") in ("subagent", "helper"):
                continue
            doc = self.get(row["sessionId"])
            if doc is not None:
                yield doc


def _canonical(doc: dict[str, Any]) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
