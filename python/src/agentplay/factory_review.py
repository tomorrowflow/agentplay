"""Turn an OpenClaw factory card's review findings into ``agentplay review`` comments.

``agentplay factory-review`` reads the Workboard's card JSON (as
``factory-digest`` does) and, for each card with a published PR and a session
map, traces two kinds of comment to the edits that wrote the lines:

- the inner reviewer's open findings, ``worktree.rounds[].review.items`` that
  are not ``fixed``;
- every ``path:line`` in an operator comment on a published PR, and in an
  agent's ``Verdict: rework: …``. Workboard's own comments (session summaries,
  notices) and every other comment are not mined: a session posts its summary
  before its round is imported, so no round's tree is the one it describes.

A rework round resumes the same session, so a later round can rewrite the line
an earlier finding names. Each comment is therefore pinned to the round it was
written against: ``commit`` is that round's final commit and ``until`` a time
after its edits and before the next round's, so the replay stops there.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .factory_digest import Card, CardComment, Round, Session, iso
from .review import PullRequest

# A repo-relative path (with a `/`, or a file name with a letter-led extension,
# which leaves out IPs and versions), then `:12`, `:L12` or a range `:12-20`.
MENTION = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)+[\w.-]+|[\w-]+(?:\.[\w-]+)*\.[A-Za-z]\w*):L?(\d+)(?:-L?\d+)?\b")
# openclaw's REVIEW_VERDICT_PATTERN (workboard node-ticket.ts), rework only.
_REWORK_VERDICT = re.compile(r"^(\*{1,2}|_{1,2})?Verdict\1?:\1?\s*(?:\*{1,2}|_{1,2})?rework(?![a-z0-9])", re.I)
_PULL_URL = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")


@dataclass(frozen=True)
class Plan:
    """One card's ``agentplay review`` run."""

    card_id: str
    pr: PullRequest
    pull: dict[str, str]  # base, head and url, as build_review takes them
    sessions: list[str]
    comments: list[dict[str, Any]]


def mentions(text: str) -> list[tuple[str, int]]:
    """Each distinct ``path:line`` in free text, in order; a range names its first line."""
    found = ((m[1].removeprefix("./"), int(m[2])) for m in MENTION.finditer(text))
    return list(dict.fromkeys(f for f in found if f[1] >= 1))


def _comment_round(rounds: tuple[Round, ...], c: CardComment) -> Round | None:
    """The round a comment reviews, or None for a comment that is not traced.

    An operator comment reviews the PR published last before it, unless the
    next round had come back by then. An agent's rework verdict is posted
    between its round's import and publish.
    """
    at = c.created_at
    if c.source == "operator":
        i = next((i for i in reversed(range(len(rounds)))
                  if rounds[i].published_at is not None and rounds[i].published_at <= at), None)
        if i is None:
            return None
        after = rounds[i + 1].imported_at if i + 1 < len(rounds) else None
        return rounds[i] if after is None or at < after else None
    if (c.source or "").startswith("agent:") and _REWORK_VERDICT.match(c.body.strip()):
        return next((r for r in reversed(rounds) if r.imported_at is not None and r.imported_at <= at), None)
    return None


def card_comments(card: Card, url: str) -> list[dict[str, Any]]:
    """Open findings and comment mentions, each pinned to the commit and time of its round."""
    def pinned(r: Round, cid: str, path: str, line: int, body: str, author: str | None, source: str) -> dict[str, Any]:
        # publishedAt, not importedAt: the import lands seconds after the round's last edit, so clock skew
        # between the node and the gateway can put real edits past it. The publish is later still, and the
        # next round's rework only starts after it.
        until = r.published_at if r.published_at is not None else r.imported_at
        return {"id": cid, "url": url, "author": author, "source": source, "path": path, "line": line,
                "body": body, "commit": r.commit, **({"until": iso(until)} if until is not None else {})}

    out = []
    for r in card.rounds:
        if r.commit:
            out += [
                pinned(r, f"{card.id[:8]}-r{r.number}-f{i}", f.path, f.line,
                       f"[{f.rule}] {f.body}" if f.rule else f.body, "inner-reviewer", "factory-review")
                for i, f in enumerate(r.findings) if not f.fixed
            ]
    for c in card.comments:
        r = _comment_round(card.rounds, c)
        if r is not None and r.commit:
            out += [
                pinned(r, f"{card.id[:8]}-r{r.number}-{c.id}-m{j}", path, line, c.body, c.source, "rework")
                for j, (path, line) in enumerate(mentions(c.body))
            ]
    return out


def plan_card(card: Card, sessions: list[str]) -> Plan | None:
    """None for a card with nothing to trace: no PR, session, base or head, or no comment."""
    m = _PULL_URL.search(card.pull_request_url or "")
    if not (m and sessions and card.base_commit and card.head_commit):
        return None
    pr = PullRequest(m[1], int(m[2]))
    comments = card_comments(card, pr.url)
    if not comments:
        return None
    return Plan(card.id, pr, {"base": card.base_commit, "head": card.head_commit, "url": pr.url}, sessions, comments)


def plans(cards: list[Card], sessions: list[Session]) -> list[Plan]:
    by_card: dict[str, list[str]] = defaultdict(list)
    for s in sorted(sessions, key=lambda s: s.started_ms):
        by_card[s.card_id].append(s.id)
    return [p for c in cards if (p := plan_card(c, by_card.get(c.id, []))) is not None]
