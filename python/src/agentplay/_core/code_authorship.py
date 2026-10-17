"""Content-free edit evidence and conservative surviving-line attribution.

PR creation, commit execution and billing are deliberately absent from this
module. Fingerprints describe observed edits, not an assertion that an LLM
invented the code. Unobserved or ambiguous transformations remain unknown.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from hashlib import sha256
import json
import re
from typing import Any
from urllib.parse import urlsplit

ALGORITHM_VERSION = "edit-replay-v1"
MAX_HASHES_PER_CALL = 2048
MAX_EDITS_PER_CALL = 32


def canonical_repository(remote: str) -> str | None:
    """Return host/owner/repository without credentials or transport aliases."""
    if not isinstance(remote, str):
        return None
    candidate = remote.strip()
    ssh = re.fullmatch(r"[^/@\s]+@([^/:\s]+):(.+)", candidate)
    if ssh:
        candidate = "ssh://" + ssh[1] + "/" + ssh[2]
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https", "ssh"} or not parsed.hostname:
        return None
    path = parsed.path.strip("/").removesuffix(".git")
    if len(path.split("/")) < 2 or any(p in {"", ".", ".."} for p in path.split("/")):
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.\-/]+", path):
        return None
    return (
        parsed.hostname.lower()
        + "/"
        + (path.lower() if parsed.hostname.lower() == "github.com" else path)
    )


def line_fingerprints(content: str) -> list[str]:
    """Hash exact lines, normalizing only line-ending representation.

    Whitespace and empty lines remain significant. No raw content is returned.
    """
    return [sha256(line.encode("utf-8")).hexdigest() for line in content.splitlines()]


def _patch_edits(patch: str) -> list[dict[str, Any]]:
    """Decode the explicit apply_patch format without evaluating shell or JS."""
    lines = patch.splitlines()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ValueError("unsupported_patch")
    edits: list[dict[str, Any]] = []
    path: str | None = None
    kind = ""
    moved = False
    before: list[str] = []
    after: list[str] = []

    def flush() -> None:
        if path and (before or after):
            edits.append({"path": path, "operation": kind, "before": before[:], "after": after[:]})
        before.clear()
        after.clear()

    for line in lines[1:-1]:
        if line.startswith(("*** Add File: ", "*** Update File: ", "*** Delete File: ")):
            flush()
            path = line.split(": ", 1)[1]
            kind = "create" if line.startswith("*** Add") else "replace"
            moved = False
            if line.startswith("*** Delete"):
                edits.append({"path": path, "operation": "delete", "before": [], "after": []})
                path = None
        elif line.startswith("@@"):
            flush()
        elif line == "*** End of File":
            continue
        elif line.startswith("*** Move to:"):
            if not path or kind != "replace" or moved or before or after:
                raise ValueError("unsupported_rename")
            destination = line.removeprefix("*** Move to:").strip()
            if not destination:
                raise ValueError("missing_path")
            # The provider's verified previous_filename supplies the old base;
            # captured changes belong to the post-move path, not a guessed alias.
            path = destination
            moved = True
        elif path and line.startswith((" ", "+", "-")):
            if line[0] != "+":
                before.append(line[1:])
            if line[0] != "-":
                after.append(line[1:])
        else:
            raise ValueError("unsupported_patch")
    flush()
    return edits


def extract_edit_evidence(tool_name: str, tool_input: Any, *, successful: bool) -> dict[str, Any]:
    """Extract bounded line digests from successful, explicit edit operations.

    Paths are as recorded in the transcript here; callers must resolve each to an exact
    repository and relative path before shipping. Unsupported calls never emit
    partial evidence. Shell commands are not treated as edits by their name.
    """
    if not successful:
        return {"status": "not_successful", "edits": []}
    if not isinstance(tool_name, str):
        return {"status": "unsupported_tool", "edits": []}
    name = tool_name.lower().rsplit(".", 1)[-1]
    try:
        obj = json.loads(tool_input) if isinstance(tool_input, str) else tool_input
    except (TypeError, ValueError, RecursionError):
        obj = tool_input
    raw: list[dict[str, Any]] = []
    try:
        if name in {"edit", "str_replace", "str_replace_editor", "multiedit"} and isinstance(
            obj, dict
        ):
            if obj.get("command") not in {None, "str_replace"}:
                raise ValueError("unsupported_operation")
            path = obj.get("file_path") or obj.get("path")
            changes = obj.get("edits") if name == "multiedit" else [obj]
            if not isinstance(changes, list):
                raise ValueError("invalid_edits")
            for change in changes:
                old, new = (
                    change.get("old_string", change.get("old_str", change.get("oldString"))),
                    change.get("new_string", change.get("new_str", change.get("newString"))),
                )
                if (
                    not isinstance(old, str)
                    or not isinstance(new, str)
                    or not old
                    or change.get("replace_all", change.get("replaceAll"))
                ):
                    raise ValueError("unsupported_replacement")
                old, new = (
                    re.sub(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]", "\n", value)
                    for value in (old, new)
                )
                # Only newline-delimited interiors prove complete line boundaries.
                # A substring can also match an unrelated complete line in the PR.
                if not (
                    old.startswith("\n")
                    and old.endswith("\n")
                    and new.startswith("\n")
                    and new.endswith("\n")
                ):
                    raise ValueError("unsupported_partial_line")
                raw.append(
                    {
                        "path": path,
                        "operation": "replace",
                        "before": old.split("\n")[1:-1],
                        "after": new.split("\n")[1:-1],
                    }
                )
        elif name in {"write", "write_file", "create_file"} and isinstance(obj, dict):
            content = obj.get("content", obj.get("file_text"))
            if not isinstance(content, str):
                raise ValueError("invalid_content")
            raw.append(
                {
                    "path": obj.get("file_path") or obj.get("path"),
                    "operation": "write",
                    "before": [],
                    "after": content.splitlines(),
                }
            )
        elif name == "apply_patch":
            patch = obj.get("patch", obj.get("input")) if isinstance(obj, dict) else obj
            if not isinstance(patch, str):
                raise ValueError("invalid_patch")
            raw = _patch_edits(patch)
        else:
            return {"status": "unsupported_tool", "edits": []}
        if (
            len(raw) > MAX_EDITS_PER_CALL
            or sum(len(e["before"]) + len(e["after"]) for e in raw) > MAX_HASHES_PER_CALL
        ):
            raise ValueError("capture_limit")
        if any(not isinstance(e["path"], str) or not e["path"] for e in raw):
            raise ValueError("missing_path")
        for edit in raw:
            for side in ("before", "after"):
                edit[side] = [sha256(line.encode("utf-8")).hexdigest() for line in edit[side]]
        return {"status": "captured" if raw else "no_edit", "edits": raw}
    except UnicodeError:
        return {"status": "invalid_input", "edits": []}
    except (ValueError, TypeError, AttributeError) as exc:
        return {"status": str(exc) if isinstance(exc, ValueError) else "invalid_input", "edits": []}


def _unique_position(haystack: list[str], needle: list[str]) -> int | None:
    if not needle:
        return None
    positions = [
        i for i in range(len(haystack) - len(needle) + 1) if haystack[i : i + len(needle)] == needle
    ]
    return positions[0] if len(positions) == 1 else None


def inserted_hashes(edits: list[dict[str, Any]], added_file: bool) -> dict[str, set[str]]:
    """Return, per session, the line digests its observed edits inserted into one path.

    A replace edit inserts the after-lines of its non-equal ``SequenceMatcher``
    opcodes; unchanged context lines are not insertions. Create/write edits count
    only for a file the PR adds, the same rule the replay applies, so a whole-file
    write over existing content never claims every line it happens to repeat.

    Args:
        edits: Observed edits for one repository path, each carrying ``session_id``.
        added_file: True when the PR adds the file (no base content).

    Returns:
        ``{session_id: {line_digest, ...}}``; a session with no insertions maps to
        an empty set.
    """
    inserted: dict[str, set[str]] = {}
    for edit in edits:
        hashes = inserted.setdefault(edit["session_id"], set())
        if edit["operation"] in {"create", "write"}:
            if added_file:
                hashes.update(edit["after"])
        elif edit["operation"] != "delete":
            opcodes = SequenceMatcher(None, edit["before"], edit["after"], autojunk=False)
            for tag, _a, _b, c, d in opcodes.get_opcodes():
                if tag != "equal":
                    hashes.update(edit["after"][c:d])
    return inserted


@dataclass(frozen=True)
class HeadLine:
    """What the replay knows about one head line.

    ``writers`` are the sessions whose placed edits wrote this line or a line it
    replaced; empty for a line carried through from the base. ``owner`` is None
    for a base line and for a line only an ambiguous edit wrote.
    """

    owner: tuple[str, str] | None
    writers: frozenset[str]


@dataclass(frozen=True)
class Replay:
    lines: dict[int, HeadLine]
    removed: dict[int, tuple[str, str]]
    unresolved: list[dict[str, str]]
    unplaced: list[dict[str, Any]]


def replay_owners(base: str | None, head: str | None, edits: list[dict[str, Any]]) -> Replay:
    """Replay ordered observed edits over the PR base and map head lines to their owners.

    An edit whose exact change was observed in more than one session has no
    owner, and an edit that cannot be placed is skipped; both are reported in
    the unresolved list. When the replay does not reproduce the head exactly,
    only unique equal runs are mapped to head lines.

    Args:
        base: Immutable PR-base file content, or None for an absent file.
        head: Immutable PR-head file content, or None for a deleted file.
        edits: Chronologically ordered digests for one repository path, each
            carrying ``session_id`` and ``event_id``.

    Returns:
        ``lines``: head line index to what the replay placed there, for every
        head line it maps; ``removed``: base line index to the owner of the edit
        that removed it; ``unresolved``: ``{event_id, reason}`` for every edit
        that did not resolve to one owner; ``unplaced``: the edits it skipped.
    """
    original = line_fingerprints(base or "")
    final = line_fingerprints(head or "")
    virtual = original[:]
    owners: list[tuple[str, str] | None] = [None] * len(virtual)
    writers: list[frozenset[str]] = [frozenset()] * len(virtual)
    base_indices: list[int | None] = list(range(len(original)))
    removed_owners: dict[int, tuple[str, str]] = {}
    unresolved: list[dict[str, str]] = []
    unplaced: list[dict[str, Any]] = []
    origins: dict[str, set[str]] = {}
    for edit in edits:
        signature = json.dumps([edit["operation"], edit["before"], edit["after"]])
        origins.setdefault(signature, set()).add(edit["session_id"])
    for edit in edits:
        before, after = edit["before"], edit["after"]
        owner = (edit["session_id"], edit["event_id"])
        signature = json.dumps([edit["operation"], before, after])
        sessions = frozenset(origins[signature])
        assigned_owner = owner if len(sessions) == 1 else None
        if assigned_owner is None:
            unresolved.append({"event_id": owner[1], "reason": "ambiguous_source_session"})
        operation = edit["operation"]
        if operation in {"create", "write"}:
            if virtual or base is not None:
                unresolved.append({"event_id": owner[1], "reason": "missing_before_snapshot"})
                unplaced.append(edit)
                continue
            virtual, owners = after[:], [assigned_owner] * len(after)
            writers = [sessions] * len(after)
            base_indices = [None] * len(after)
            continue
        if operation == "delete":
            # A filename-only deletion provides no content evidence connecting
            # this checkout to the PR base. Keep that attribution unknown.
            unresolved.append({"event_id": owner[1], "reason": "missing_before_snapshot"})
            unplaced.append(edit)
            continue
        position = _unique_position(virtual, before)
        if position is None:
            unresolved.append({"event_id": owner[1], "reason": "before_not_unique_or_missing"})
            unplaced.append(edit)
            continue
        replacement: list[tuple[str, str] | None] = []
        replacement_writers: list[frozenset[str]] = []
        replacement_indices: list[int | None] = []
        for tag, a, b, c, d in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
            if tag == "equal":
                replacement.extend(owners[position + a : position + b])
                replacement_writers.extend(writers[position + a : position + b])
                replacement_indices.extend(base_indices[position + a : position + b])
                continue
            replacement.extend([assigned_owner] * (d - c))
            replacement_writers.extend([sessions.union(*writers[position + a : position + b])] * (d - c))
            replacement_indices.extend([None] * (d - c))
            if assigned_owner is not None:
                for index in base_indices[position + a : position + b]:
                    if index is not None:
                        removed_owners[index] = assigned_owner
        virtual[position : position + len(before)] = after
        owners[position : position + len(before)] = replacement
        writers[position : position + len(before)] = replacement_writers
        base_indices[position : position + len(before)] = replacement_indices

    lines: dict[int, HeadLine] = {}
    if virtual == final:
        lines = {i: HeadLine(owners[i], writers[i]) for i in range(len(final))}
    else:
        # A later manual edit may move or replace lines. Only unique equal
        # sequences are mapped; repeated boilerplate is not a tie-break.
        for block in SequenceMatcher(None, virtual, final, autojunk=False).get_matching_blocks():
            if not block.size:
                continue
            sequence = virtual[block.a : block.a + block.size]
            if (
                _unique_position(virtual, sequence) is None
                or _unique_position(final, sequence) is None
            ):
                continue
            for offset in range(block.size):
                lines[block.b + offset] = HeadLine(owners[block.a + offset], writers[block.a + offset])
    return Replay(lines, removed_owners, unresolved, unplaced)


def attribute_file(
    base: str | None,
    head: str | None,
    edits: list[dict[str, Any]],
    *,
    exact_sessions: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Replay ordered observed edits and count ownership surviving at the PR head.

    Each surviving added line is ``exclusive`` to its replay owner unless
    another session also inserted that line's digest on this path, which makes
    it ``shared`` (best-of-N attempts and parallel branches). When exactly one of
    the claiming sessions has an exact commit link to the PR and it is the owner,
    the line stays exclusive. A surviving deletion of a base line is credited to
    its owner in ``agent_deleted_lines``/``deletion_sessions`` and
    ``session_events`` but never counts as exclusive or shared: removing lines
    alone must not make a link strong.

    Args:
        base: Immutable PR-base file content, or None for an absent file.
        head: Immutable PR-head file content, or None for a deleted file.
        edits: Chronologically ordered digests for this exact repository/path,
            each carrying session_id and event_id (and ``completed_at`` when
            known). Callers enforce clocks and repository identity before
            passing evidence.
        exact_sessions: Sessions with an exact commit link to this PR; used only
            to break ties on shared lines.

    Returns:
        Added-line denominator, observed agent lines per session, unknown lines,
        matched event identities and unresolved-edit reasons, plus per session
        ``exclusive_lines``/``shared_lines`` counts and ``session_events``
        (``{session_id: {event_id: completed_at or None}}`` for every event that
        owns a credited line). No cost allocation or human-authored
        classification is inferred.
    """
    original = line_fingerprints(base or "")
    final = line_fingerprints(head or "")
    replay = replay_owners(base, head, edits)
    surviving = {i: line.owner for i, line in replay.lines.items() if line.owner is not None}
    removed_owners, unresolved = replay.removed, replay.unresolved
    added: set[int] = set()
    deleted_indices: set[int] = set()
    for tag, a, b, c, d in SequenceMatcher(None, original, final, autojunk=False).get_opcodes():
        if tag != "equal":
            added.update(range(c, d))
            deleted_indices.update(range(a, b))
    counts = Counter(surviving[i][0] for i in added if i in surviving)
    # Reappearing/moved base text is not evidence of a surviving deletion.
    final_hashes = set(final)
    surviving_deletions = {
        i: owner
        for i, owner in removed_owners.items()
        if i in deleted_indices and original[i] not in final_hashes
    }
    deletion_counts = Counter(owner[0] for owner in surviving_deletions.values())
    events = sorted({surviving[i][1] for i in added if i in surviving})
    credited = [(surviving[i], final[i]) for i in sorted(added) if i in surviving]
    inserted = inserted_hashes(edits, base is None)
    completed_at = {edit["event_id"]: edit.get("completed_at") for edit in edits}
    exclusive: Counter[str] = Counter()
    shared: Counter[str] = Counter()
    for (sid, _event_id), digest in credited:
        claimants = {sid} | {other for other, hashes in inserted.items() if digest in hashes}
        if len(claimants) == 1 or (claimants & exact_sessions) == {sid}:
            exclusive[sid] += 1
        else:
            shared[sid] += 1
    session_events: dict[str, dict[str, str | None]] = {}
    deletions = [owner for _, owner in sorted(surviving_deletions.items())]
    for sid, event_id in [owner for owner, _ in credited] + deletions:
        session_events.setdefault(sid, {})[event_id] = completed_at.get(event_id)
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "added_lines": len(added),
        "deleted_lines": len(deleted_indices),
        "agent_deleted_lines": sum(deletion_counts.values()),
        "unknown_deleted_lines": len(deleted_indices) - sum(deletion_counts.values()),
        "deletion_sessions": dict(deletion_counts),
        "agent_added_lines": sum(counts.values()),
        "unknown_added_lines": len(added) - sum(counts.values()),
        "sessions": dict(counts),
        "event_ids": events,
        "deletion_event_ids": sorted({owner[1] for owner in surviving_deletions.values()}),
        "unresolved_edits": unresolved,
        "exclusive_lines": dict(exclusive),
        "shared_lines": dict(shared),
        "session_events": session_events,
    }
