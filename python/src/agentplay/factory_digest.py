"""A nightly per-card digest for an OpenClaw software factory.

Factory cards run ``claude -p`` in a worktree named ``…/wb-<cardId>``, so the
store's maps of those transcripts can be joined with the Workboard's card JSON
(``openclaw workboard list --json``). ``agentplay factory-digest`` reports, per
published card and per attempt, peak context, cost, errors, whether a check ran
after the last edit, and stop signals. It reads only; it proposes nothing.

The result is a local-only ``agentplay-factory-digest/v1`` document plus a
Markdown table under ``$AGENTPLAY_HOME/digests/``. It holds card titles, which
are private text, so it is never written into a map or an export.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TextIO, TypedDict

from .builder import _NON_CALL_KINDS, _error_source, classify_outcome
from .skill_inventory import installed_skills, pulled_at, repo_skills
from .store import Store, _atomic_write

DIGEST_SCHEMA = "agentplay-factory-digest/v1"
DAY_MS = 86_400_000
# A map's session.startedAt drops its fraction of a second, so an event placed at
# startedAt + t can sit up to a second before its real time. Workboard attempt
# times are Date.now() milliseconds.
SLACK_MS = 1000

AttemptStatus = Literal["running", "succeeded", "failed", "blocked", "stopped"]
Fidelity = Literal["ok", "mismatch", "no_events"]
Flag = Literal[
    "no-verify-after-edit",
    "verify-failed-last",
    "stopped-on-permission",
    "attempt-fidelity-mismatch",
    "no-transcript",
    "skill-load-failed",
]
FLAGS: tuple[Flag, ...] = (
    "no-verify-after-edit",
    "verify-failed-last",
    "stopped-on-permission",
    "attempt-fidelity-mismatch",
    "no-transcript",
    "skill-load-failed",
)
_ATTEMPT_STATUSES = ("running", "succeeded", "failed", "blocked", "stopped")
_MS = (int, float)  # Workboard timestamps are epoch milliseconds
_DENIALS = ("permission_denied", "blocked")


class DigestError(Exception):
    """A problem the user can act on; the message says what to do."""


# --------------------------------------------------------------------------- cards
@dataclass(frozen=True)
class Attempt:
    id: str
    status: AttemptStatus
    started_at: int
    ended_at: int | None
    error: str | None
    context_tokens: int | None


@dataclass(frozen=True)
class Finding:
    """One inner-reviewer finding; ``line`` is in the round's final commit unless ``fixed``."""

    path: str
    line: int
    body: str
    rule: str | None
    fixed: bool


@dataclass(frozen=True)
class Round:
    """A review round: ``worktree.rounds[]`` joined with ``worktree.reviews[]`` by round number."""

    number: int
    commit: str | None  # changes.to, the round's final commit
    imported_at: int | None  # when the round's work came back to the board
    published_at: int | None  # when the round's PR was published for review
    findings: tuple[Finding, ...]


@dataclass(frozen=True)
class CardComment:
    id: str
    body: str
    created_at: int
    source: str | None  # "operator", "agent:dev", …; absent on older comments


@dataclass(frozen=True)
class Card:
    id: str
    title: str
    labels: tuple[str, ...]
    status: str
    attempts: tuple[Attempt, ...]
    published_at: int | None
    pull_request_url: str | None
    head_commit: str | None
    base_commit: str | None
    rework_round: int | None
    reviews: tuple[dict[str, Any], ...]
    rounds: tuple[Round, ...]
    comments: tuple[CardComment, ...]


def _field(d: dict[str, Any], key: str, kind: type | tuple[type, ...], where: str, *, required: bool = False) -> Any:
    v = d.get(key)
    if v is None:
        if required:
            raise DigestError(f"{where} has no {key}")
        return None
    # bool is an int subclass; a timestamp of `true` is malformed
    if not isinstance(v, kind) or isinstance(v, bool):
        raise DigestError(f"{where}: {key} has the wrong type")
    return v


def _obj(d: dict[str, Any], key: str, where: str) -> dict[str, Any]:
    return _field(d, key, dict, where) or {}


def _parse_attempt(raw: Any, where: str) -> Attempt:
    if not isinstance(raw, dict):
        raise DigestError(f"{where} is not an object")
    status = _field(raw, "status", str, where, required=True)
    if status not in _ATTEMPT_STATUSES:
        raise DigestError(f"{where}: unknown status {status!r}")
    ended = _field(raw, "endedAt", _MS, where)
    tokens = _field(raw, "contextTokens", _MS, where)
    return Attempt(
        id=_field(raw, "id", str, where, required=True),
        status=status,
        started_at=int(_field(raw, "startedAt", _MS, where, required=True)),
        ended_at=None if ended is None else int(ended),
        error=_field(raw, "error", str, where),
        context_tokens=None if tokens is None else int(tokens),
    )


def _parse_finding(raw: Any) -> Finding | None:
    """None for an item the factory should have dropped: no path, no 1-based line, no body."""
    if not isinstance(raw, dict):
        return None
    path, line, body, rule, fixed = (raw.get(k) for k in ("path", "line", "body", "rule", "fixed"))
    if not (isinstance(path, str) and path and type(line) is int and line >= 1
            and isinstance(body, str) and body and isinstance(fixed, bool)):
        return None
    return Finding(path, line, body, rule if isinstance(rule, str) and rule else None, fixed)


def _lenient(d: Any, key: str, kind: type | tuple[type, ...]) -> Any:
    """``d[key]`` when it has the right type, else None: review data the digest never needed must not abort it."""
    v = d.get(key) if isinstance(d, dict) else None
    return v if isinstance(v, kind) and not isinstance(v, bool) else None


def _parse_rounds(worktree: dict[str, Any], reviews: list[dict[str, Any]]) -> tuple[Round, ...]:
    """Malformed rounds, reviews entries and items are dropped, as ``_parse_finding`` drops items."""
    commits: dict[int, str | None] = {}
    findings: dict[int, tuple[Finding, ...]] = {}
    for raw in _lenient(worktree, "rounds", list) or []:
        n = _lenient(raw, "round", int)
        if n is None:
            continue
        commits[n] = _lenient(_lenient(raw, "changes", dict), "to", str)
        items = _lenient(_lenient(raw, "review", dict), "items", list) or []
        findings[n] = tuple(f for f in map(_parse_finding, items) if f)
    imported: dict[int, int] = {}
    published: dict[int, int] = {}
    for r in reviews:
        n = _lenient(r, "round", int)
        for key, into in (("importedAt", imported), ("publishedAt", published)):
            at = _lenient(r, key, _MS)
            if n is not None and at is not None:
                into[n] = int(at)
    return tuple(
        Round(n, commits.get(n), imported.get(n), published.get(n), findings.get(n, ()))
        for n in sorted(commits.keys() | imported.keys())
    )


def _parse_comment(raw: Any) -> CardComment | None:
    """None for a comment without an id or a timestamp."""
    cid, at = _lenient(raw, "id", str), _lenient(raw, "createdAt", _MS)
    if cid is None or at is None:
        return None
    return CardComment(cid, _lenient(raw, "body", str) or "", int(at), _lenient(raw, "source", str))


def _parse_card(raw: Any, where: str) -> Card:
    if not isinstance(raw, dict):
        raise DigestError(f"{where} is not an object")
    cid = _field(raw, "id", str, where, required=True)
    where = f"card {cid}"
    meta = _obj(raw, "metadata", where)
    worktree = _obj(_obj(_obj(meta, "automation", where), "target", where), "worktree", where)
    handoff = _obj(worktree, "handoff", where)
    rework = _obj(worktree, "rework", where)
    published = (
        _field(handoff, "publishedAt", _MS, where) if handoff.get("phase") == "published" else None
    )
    if published is None:
        published = _field(rework, "acceptedAt", _MS, where)
    labels = _field(raw, "labels", list, where) or []
    reviews = _field(worktree, "reviews", list, where) or []
    if not all(isinstance(x, str) for x in labels) or not all(isinstance(x, dict) for x in reviews):
        raise DigestError(f"{where}: labels must be strings and reviews objects")
    attempts = _field(meta, "attempts", list, where) or []
    return Card(
        id=cid,
        title=_field(raw, "title", str, where) or "",
        labels=tuple(labels),
        status=_field(raw, "status", str, where) or "",
        attempts=tuple(_parse_attempt(a, f"{where} attempt {i}") for i, a in enumerate(attempts)),
        published_at=None if published is None else int(published),
        pull_request_url=(
            _field(raw, "pullRequestUrl", str, where)
            or _field(handoff, "pullRequestUrl", str, where)
            or _field(rework, "pullRequestUrl", str, where)
        ),
        head_commit=_field(handoff, "headCommit", str, where),
        base_commit=_field(worktree, "baseCommit", str, where),
        rework_round=_field(rework, "round", int, where),
        reviews=tuple(
            {k: r.get(k) for k in ("round", "verdict", "decidedBy", "sampled") if k in r} for r in reviews
        ),
        rounds=_parse_rounds(worktree, reviews),
        comments=tuple(c for c in map(_parse_comment, _lenient(meta, "comments", list) or []) if c),
    )


def parse_cards(raw: Any) -> list[Card]:
    """Cards from ``workboard list --json`` (``{"cards": [...]}``) or ``show --json`` (``{"card": {...}}``)."""
    if isinstance(raw, dict) and isinstance(raw.get("cards"), list):
        items = raw["cards"]
    elif isinstance(raw, dict) and isinstance(raw.get("card"), dict):
        items = [raw["card"]]
    else:
        raise DigestError('cards input must be {"cards": [...]} or {"card": {...}}')
    return [_parse_card(c, f"card {i}") for i, c in enumerate(items)]


def load_cards(path: str, stdin: TextIO) -> list[Card]:
    try:
        text = stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        raw = json.loads(text)
    except (OSError, ValueError) as exc:
        raise DigestError(f"can't read cards from {path}: {exc}") from None
    return parse_cards(raw)


# --------------------------------------------------------------------------- sessions
@dataclass(frozen=True)
class Session:
    id: str
    card_id: str
    started_ms: int
    events: tuple[dict[str, Any], ...]
    home: Path  # the node's ~/.claude, or its folder in the transcript archive


def home_of(source_path: str) -> Path:
    """The folder above ``projects/`` in ``<home>/projects/<project>/<session>.jsonl``."""
    return Path(source_path).parent.parent.parent


def card_id_of(source_path: str) -> str | None:
    """The card id in a factory transcript's project dir, ``…-wb-<cardId>``."""
    _, sep, card = Path(source_path).parent.name.rpartition("-wb-")
    return card if sep and card else None


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def iso(ms: int | None) -> str | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_sessions(store: Store) -> list[Session]:
    """Main factory sessions in the store; subagents are already inside their parent's map."""
    out = []
    for row in store.rows():
        source = str(row.get("sourcePath") or "")
        card = card_id_of(source)
        if card is None or row.get("role") in ("subagent", "helper"):
            continue
        doc = store.get(row["sessionId"])
        started = ((doc or {}).get("session") or {}).get("startedAt")
        if doc is None or not started:
            continue
        out.append(Session(row["sessionId"], card, _ms(started), tuple(doc.get("events", [])), home_of(source)))
    return out


# --------------------------------------------------------------------------- digest shape
class Errors(TypedDict):
    total: int
    bySource: dict[str, int]
    byClass: dict[str, int]


class Verify(TypedDict):
    afterLastEdit: bool | None
    verbsAfterLastEdit: list[str]
    lastVerifyFailed: bool | None


class Stops(TypedDict):
    permissionDenied: int
    blocked: int


class SkillUse(TypedDict):
    calls: int
    failed: int


class Metrics(TypedDict):
    peakContext: int
    compactions: int
    costUsd: float
    tokens: int
    requests: int
    calls: int
    errors: Errors
    retries: int
    delays: int
    edits: int
    verify: Verify
    stops: Stops
    skills: dict[str, SkillUse]


class SkillLoadFailure(TypedDict):
    name: str
    node: str
    installed: bool | None  # None when the node has no skill inventory
    installedAs: list[str]
    inRepo: bool | None  # None without --repo-path, a base commit, or that commit in the clone


class NodeSkills(TypedDict):
    pulledAt: str | None
    skills: list[str]


class AttemptDigest(TypedDict):
    id: str
    status: AttemptStatus
    startedAt: str
    endedAt: str | None
    error: str | None
    contextTokens: int | None
    transcriptOutcome: str
    stoppedOnDenial: bool
    fidelity: Fidelity
    metrics: Metrics | None


class CardDigest(TypedDict):
    cardId: str
    title: str
    labels: list[str]
    status: str
    publishedAt: str | None
    pullRequestUrl: str | None
    headCommit: str | None
    baseCommit: str | None
    reworkRound: int | None
    reviews: list[dict[str, Any]]
    sessions: list[str]
    totals: Metrics | None
    attempts: list[AttemptDigest]
    unattributed: Metrics | None
    flags: list[Flag]
    skillLoadFailures: list[SkillLoadFailure]


Window = TypedDict("Window", {"from": str, "to": str, "days": int})  # `from` is a keyword


class FactoryDigest(TypedDict):
    schema: str
    builtAt: str
    window: Window
    cards: list[CardDigest]
    unmatchedSessions: list[str]
    nodes: dict[str, NodeSkills]


# --------------------------------------------------------------------------- metrics
@dataclass(frozen=True)
class Timed:
    """One event placed in wall-clock time; ``event["seq"]`` is renumbered along the card's timeline."""

    at: int
    event: dict[str, Any]


def timeline(sessions: list[Session]) -> list[Timed]:
    """Every event of a card's sessions in wall-clock order.

    seqs restart in each session, so they are renumbered here: ``metrics`` and
    ``classify_outcome`` compare seqs to tell what came after the last edit.
    """
    placed = sorted(
        ((s.started_ms + int(e.get("t") or 0), n, e["seq"], e) for n, s in enumerate(sessions) for e in s.events),
        key=lambda r: r[:3],
    )
    return [Timed(at, {**e, "seq": i}) for i, (at, _, _, e) in enumerate(placed)]


def metrics(events: list[dict[str, Any]]) -> Metrics:
    llm = [e for e in events if e["kind"] == "llm"]
    failed = [e for e in events if e.get("err")]
    by_source: dict[str, int] = {}
    by_class: dict[str, int] = {}
    for e in failed:
        src, cls = _error_source(e), str(e.get("errClass") or "other")
        by_source[src] = by_source.get(src, 0) + 1
        by_class[cls] = by_class.get(cls, 0) + 1
    edits = [e for e in events if e["kind"] == "edit"]
    verifies = [e for e in events if e["kind"] == "verify" and e.get("errClass") not in _DENIALS]
    after = [e for e in verifies if edits and e["seq"] > edits[-1]["seq"]]
    skills: dict[str, SkillUse] = {}
    for e in events:
        if e["kind"] == "skill":
            use = skills.setdefault(str(e.get("skill") or ""), {"calls": 0, "failed": 0})
            use["calls"] += 1
            use["failed"] += 1 if e.get("err") else 0
    return {
        "peakContext": max((e["in"] + e["cw"] + e["cr"] for e in llm if e["agent"] == "main"), default=0),
        "compactions": sum(1 for e in events if e["kind"] == "compaction"),
        "costUsd": round(sum(e.get("cost", 0.0) for e in llm), 4),
        "tokens": sum(e["in"] + e["cw"] + e["cr"] + e["out"] for e in llm),
        "requests": len(llm),
        "calls": sum(1 for e in events if e["kind"] not in _NON_CALL_KINDS),
        "errors": {"total": len(failed), "bySource": by_source, "byClass": by_class},
        "retries": sum(1 for e in events if e.get("retryOf") is not None),
        "delays": sum(1 for e in events if e["kind"] == "delay"),
        "edits": len(edits),
        "verify": {
            "afterLastEdit": bool(after) if edits else None,
            "verbsAfterLastEdit": list(dict.fromkeys(str(e.get("verb") or e.get("tool") or "") for e in after)),
            "lastVerifyFailed": bool(verifies[-1].get("err")) if verifies else None,
        },
        "stops": {
            "permissionDenied": by_class.get("permission_denied", 0),
            "blocked": by_class.get("blocked", 0),
        },
        "skills": dict(sorted(skills.items())),
    }


def split_attempts(events: list[Timed], attempts: tuple[Attempt, ...]) -> tuple[dict[str, list[Timed]], list[Timed]]:
    """Each event goes to the latest-started attempt whose span holds it; the rest are unattributed."""
    by_start = sorted(attempts, key=lambda a: a.started_at, reverse=True)
    slices: dict[str, list[Timed]] = {a.id: [] for a in attempts}
    rest: list[Timed] = []
    for t in events:
        owner = next(
            (a for a in by_start
             if a.started_at - SLACK_MS <= t.at
             and (a.ended_at is None or t.at <= a.ended_at + SLACK_MS)),
            None,
        )
        (slices[owner.id] if owner else rest).append(t)
    return slices, rest


def stopped_on_denial(events: list[dict[str, Any]]) -> bool:
    """Whether the last call was refused; a denial the agent worked past is not a stop."""
    last = next((e for e in reversed(events) if e["kind"] not in _NON_CALL_KINDS), None)
    return bool(last and last.get("err") and last.get("errClass") in _DENIALS)


def fidelity(status: str, outcome: str, denied_last: bool, has_events: bool) -> Fidelity:
    """``mismatch`` when the card's status contradicts how the attempt's events end."""
    if not has_events:
        return "no_events"
    contradicts = (status in ("stopped", "succeeded") and denied_last) or (
        status == "succeeded" and outcome == "interrupted"
    )
    return "mismatch" if contradicts else "ok"


def _attempt_digest(a: Attempt, slice_: list[Timed]) -> AttemptDigest:
    evs = [t.event for t in slice_]
    outcome = classify_outcome(evs, "")
    denied_last = stopped_on_denial(evs)
    return {
        "id": a.id,
        "status": a.status,
        "startedAt": iso(a.started_at) or "",
        "endedAt": iso(a.ended_at),
        "error": a.error,
        "contextTokens": a.context_tokens,
        "transcriptOutcome": outcome,
        "stoppedOnDenial": denied_last,
        "fidelity": fidelity(a.status, outcome, denied_last, bool(evs)),
        "metrics": metrics(evs) if evs else None,
    }


def _failed_skills(totals: Metrics | None) -> list[str]:
    return [name for name, use in totals["skills"].items() if use["failed"]] if totals else []


def card_flags(totals: Metrics | None, attempts: list[AttemptDigest]) -> list[Flag]:
    if totals is None:
        return ["no-transcript"]
    raised: dict[Flag, bool] = {
        "no-verify-after-edit": totals["verify"]["afterLastEdit"] is False,
        "verify-failed-last": totals["verify"]["lastVerifyFailed"] is True,
        "stopped-on-permission": any(a["stoppedOnDenial"] for a in attempts),
        "attempt-fidelity-mismatch": any(a["fidelity"] == "mismatch" for a in attempts),
        "no-transcript": False,
        "skill-load-failed": bool(_failed_skills(totals)),
    }
    return [f for f in FLAGS if raised[f]]


def _leaf(skill: str) -> str:
    return skill.rpartition(":")[2]


def skill_load_failures(
    sessions: list[Session], nodes: dict[str, NodeSkills], repo: frozenset[str] | None
) -> list[SkillLoadFailure]:
    """One entry per failed skill and the node of the session that called it.

    ``repo`` holds the project skills of the card's repository at its base commit.
    """
    pairs = sorted({
        (str(e.get("skill") or ""), s.home.name)
        for s in sessions for e in s.events if e["kind"] == "skill" and e.get("err")
    })
    out: list[SkillLoadFailure] = []
    for name, node in pairs:
        inv = nodes.get(node)
        skills = inv["skills"] if inv and inv["pulledAt"] is not None else None
        out.append({
            "name": name,
            "node": node,
            "installed": None if skills is None else name in skills,
            "installedAs": [k for k in skills or [] if k != name and _leaf(k) == _leaf(name)],
            "inRepo": None if repo is None else _leaf(name) in repo,
        })
    return out


def node_skills(sessions: list[Session]) -> dict[str, NodeSkills]:
    homes = {s.home.name: s.home for s in sessions}
    return {
        node: {"pulledAt": pulled_at(home), "skills": sorted(installed_skills(home) or ())}
        for node, home in sorted(homes.items())
    }


def card_digest(card: Card, sessions: list[Session], nodes: dict[str, NodeSkills], repo_path: Path | None) -> CardDigest:
    events = timeline(sessions)
    slices, rest = split_attempts(events, card.attempts)
    attempts = [_attempt_digest(a, slices[a.id]) for a in card.attempts]
    totals = metrics([t.event for t in events]) if sessions else None
    repo = repo_skills(repo_path, card.base_commit) if repo_path and card.base_commit and _failed_skills(totals) else None
    return {
        "cardId": card.id,
        "title": card.title,
        "labels": list(card.labels),
        "status": card.status,
        "publishedAt": iso(card.published_at),
        "pullRequestUrl": card.pull_request_url,
        "headCommit": card.head_commit,
        "baseCommit": card.base_commit,
        "reworkRound": card.rework_round,
        "reviews": list(card.reviews),
        "sessions": [s.id for s in sessions],
        "totals": totals,
        "attempts": attempts,
        "unattributed": metrics([t.event for t in rest]) if rest else None,
        "flags": card_flags(totals, attempts),
        "skillLoadFailures": skill_load_failures(sessions, nodes, repo),
    }


def select_cards(cards: list[Card], *, now_ms: int, days: int, prefixes: list[str]) -> list[Card]:
    """Cards named by id prefix, or else the ones published in the window."""
    if prefixes:
        chosen: dict[str, Card] = {}
        for p in prefixes:
            hits = [c for c in cards if c.id.startswith(p)]
            if len(hits) != 1:
                raise DigestError(f"--card {p} matches {len(hits)} cards; give a longer id")
            chosen[hits[0].id] = hits[0]
        return list(chosen.values())
    start = now_ms - days * DAY_MS
    return [c for c in cards if c.published_at is not None and start <= c.published_at <= now_ms]


def build_digest(
    cards: list[Card], sessions: list[Session], *, now_ms: int, days: int, prefixes: list[str],
    repo_path: Path | None = None,
) -> FactoryDigest:
    by_card: dict[str, list[Session]] = {}
    for s in sorted(sessions, key=lambda s: s.started_ms):
        by_card.setdefault(s.card_id, []).append(s)
    known = {c.id for c in cards}
    chosen = sorted(
        select_cards(cards, now_ms=now_ms, days=days, prefixes=prefixes),
        key=lambda c: (c.published_at or 0, c.id),
    )
    nodes = node_skills([s for c in chosen for s in by_card.get(c.id, [])])
    return {
        "schema": DIGEST_SCHEMA,
        "builtAt": iso(now_ms) or "",
        "window": {"from": iso(now_ms - days * DAY_MS) or "", "to": iso(now_ms) or "", "days": days},
        "cards": [card_digest(c, by_card.get(c.id, []), nodes, repo_path) for c in chosen],
        "unmatchedSessions": sorted(s.id for s in sessions if s.card_id not in known),
        "nodes": nodes,
    }


# --------------------------------------------------------------------------- markdown
def _pr(url: str | None) -> str:
    if not url:
        return "–"
    m = re.search(r"/pull/(\d+)", url)
    return f"[#{m.group(1)}]({url})" if m else f"[PR]({url})"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _flag_entry(flag: Flag, card: CardDigest) -> str:
    title = _cell(card["title"] or card["cardId"])
    if flag == "skill-load-failed":
        failures = "; ".join(_failure(f, card["baseCommit"]) for f in card["skillLoadFailures"])
        return f"{title} ({_cell(failures)})"
    return title


def _failure(f: SkillLoadFailure, base: str | None) -> str:
    state = {None: "no inventory", True: "installed", False: "not installed"}[f["installed"]]
    hint = f", installed as {', '.join(f['installedAs'])}" if f["installedAs"] else ""
    repo = "" if f["inRepo"] is None or base is None else f", {'in' if f['inRepo'] else 'not in'} repo at {base[:7]}"
    return f"{f['name']} on {f['node']}: {state}{hint}{repo}"


def render_markdown(doc: FactoryDigest) -> str:
    w = doc["window"]
    lines = [
        f"# Factory digest {w['from'][:10]} to {w['to'][:10]}",
        "",
        f"{len(doc['cards'])} cards. The window is the last {w['days']} days of publishing.",
        "",
        "| card | PR | attempts | peak ctx | cost | errors | verify after last edit | flags |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in doc["cards"]:
        t = c["totals"]
        title = c["title"] if len(c["title"]) <= 60 else c["title"][:59] + "…"
        statuses = ", ".join(a["status"] for a in c["attempts"])
        after = t and t["verify"]["afterLastEdit"]
        lines.append("| " + " | ".join([
            _cell(title or c["cardId"]),
            _pr(c["pullRequestUrl"]),
            f"{len(c['attempts'])} ({statuses})" if statuses else "0",
            f"{t['peakContext'] / 1000:.0f}k" if t else "–",
            f"${t['costUsd']:.2f}" if t else "–",
            str(t["errors"]["total"]) if t else "–",
            "–" if after is None else ("yes" if after else "no"),
            ", ".join(c["flags"]),
        ]) + " |")
    flagged = [(f, [c for c in doc["cards"] if f in c["flags"]]) for f in FLAGS]
    if any(cards for _, cards in flagged):
        lines += ["", "## Flags", ""]
        for f, cards in flagged:
            if cards:
                lines.append(f"- **{f}**: " + "; ".join(_flag_entry(f, c) for c in cards))
    if doc["nodes"]:
        lines += ["", "## Node skills", ""]
        for node, inv in doc["nodes"].items():
            got = f"{len(inv['skills'])} skills, pulled {inv['pulledAt'][:10]}" if inv["pulledAt"] else "no inventory"
            lines.append(f"- {_cell(node)} ({got})")
    if doc["unmatchedSessions"]:
        lines += ["", f"{len(doc['unmatchedSessions'])} factory sessions belong to cards not in the input."]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- storage
def save_digest(out_dir: Path, doc: FactoryDigest, *, picked: bool = False) -> Path:
    """Write the digest as JSON and Markdown; a rerun on the same day overwrites.

    The nightly window writes the day's files and the ``factory-latest`` copies.
    Cards picked with ``--card`` go to ``factory-cards-<date>`` so they never
    replace the nightly ones.
    """
    body = json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")
    md = render_markdown(doc).encode("utf-8")
    date = doc["window"]["to"][:10]
    stems = [f"factory-cards-{date}"] if picked else [f"factory-{date}", "factory-latest"]
    for name in stems:
        _atomic_write(out_dir / f"{name}.json", body)
        _atomic_write(out_dir / f"{name}.md", md)
    return out_dir / f"{stems[0]}.json"
