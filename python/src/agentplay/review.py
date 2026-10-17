"""Trace PR review comments back to the session edit that wrote the commented line.

``agentplay review`` replays the local edit evidence of one or more sessions
(``store.evidence``) over the PR's base and head file contents, read from a
local git checkout, and asks which edit owns each commented head line. The
result is a local-only ``agentplay-review/v1`` document under
``$AGENTPLAY_HOME/reviews/``; it holds the comment bodies, so it is never
written into a map or an export.

Comments come from a generic ``agentplay-review-input/v1`` file, or from
GitHub through the ``gh`` CLI. That fetch, and a ``git fetch`` of the PR head
when the checkout lacks it, are the only network calls, and happen only when
the user runs this command.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._core.code_authorship import inserted_hashes, line_fingerprints, replay_owners
from .store import Store, _atomic_write

REVIEW_SCHEMA = "agentplay-review/v1"
INPUT_SCHEMA = "agentplay-review-input/v1"
CAUSAL_STEPS = 5
_TOUCH_KINDS = ("read", "search", "edit")


class ReviewError(Exception):
    """A problem the user can act on; the message says what to do."""


@dataclass(frozen=True)
class PullRequest:
    repo: str  # owner/name
    number: int

    @classmethod
    def parse(cls, text: str) -> PullRequest:
        m = re.fullmatch(r"([\w.-]+/[\w.-]+)#(\d+)", text.strip())
        if not m:
            raise ReviewError(f"can't read --pr {text!r}; use owner/repo#7")
        return cls(m.group(1), int(m.group(2)))

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}/pull/{self.number}"

    @property
    def file_stem(self) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", self.repo.replace("/", "_")) + f"_{self.number}"


# --------------------------------------------------------------------------- comments
def load_comments(path: Path) -> list[dict[str, Any]]:
    """Comments from an ``agentplay-review-input/v1`` file, in the review doc's shape."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReviewError(f"can't read {path}: {exc}") from None
    if not isinstance(raw, dict) or raw.get("schema") != INPUT_SCHEMA or not isinstance(raw.get("comments"), list):
        raise ReviewError(f"{path} is not an {INPUT_SCHEMA} document")
    out = []
    for i, c in enumerate(raw["comments"]):
        if (
            not isinstance(c, dict)
            or not isinstance(c.get("path"), str)
            or type(c.get("line")) is not int
            or not isinstance(c.get("body"), str)
        ):
            raise ReviewError(f"{path}: comment {i} needs a string path, an integer line and a string body")
        out.append({
            "id": str(c.get("id") or i),
            "url": c.get("url") if isinstance(c.get("url"), str) else None,
            "author": c.get("author") if isinstance(c.get("author"), str) else None,
            "source": c.get("source") if isinstance(c.get("source"), str) else "file",
            "path": c["path"],
            "line": c["line"],
            "body": c["body"],
        })
    return out


def _gh(*args: str) -> str:
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        raise ReviewError("the GitHub CLI (gh) is not installed; pass --comments, --base and --head instead") from None
    if done.returncode != 0:
        raise ReviewError(f"gh {' '.join(args)} failed: {done.stderr.strip() or done.returncode}")
    return done.stdout


def _json_pages(text: str) -> list[Any]:
    """``gh api --paginate`` prints one JSON array per page, back to back."""
    decoder, items, pos = json.JSONDecoder(), [], 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        page, pos = decoder.raw_decode(text, pos)
        items.extend(page if isinstance(page, list) else [page])
    return items


def github_pull(pr: PullRequest) -> dict[str, str]:
    """The PR's base and head commits and its URL, via ``gh api``."""
    data = json.loads(_gh("api", f"repos/{pr.repo}/pulls/{pr.number}"))
    return {"base": data["base"]["sha"], "head": data["head"]["sha"], "url": data.get("html_url") or pr.url}


def github_comments(pr: PullRequest) -> tuple[list[dict[str, Any]], int]:
    """Inline review comments on the PR's current head, and how many were left out.

    A comment GitHub no longer places on the head (outdated, ``line`` null) or one
    on a removed line (``side`` LEFT) names no head line to attribute.
    """
    out, skipped = [], 0
    for c in _json_pages(_gh("api", "--paginate", f"repos/{pr.repo}/pulls/{pr.number}/comments")):
        if not isinstance(c, dict):
            continue
        if type(c.get("line")) is not int or c.get("side", "RIGHT") != "RIGHT" or not isinstance(c.get("path"), str):
            skipped += 1
            continue
        out.append({
            "id": str(c.get("id")),
            "url": c.get("html_url"),
            "author": (c.get("user") or {}).get("login"),
            "source": "github",
            "path": c["path"],
            "line": c["line"],
            "body": str(c.get("body") or ""),
        })
    return out, skipped


# --------------------------------------------------------------------------- git
class Checkout:
    """File contents at fixed commits of a local clone, read with ``git show``."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._cache: dict[tuple[str, str], str | None] = {}

    def _git(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(["git", "-C", str(self.path), *args], capture_output=True)
        except FileNotFoundError:
            raise ReviewError("git is not installed") from None

    def ensure(self, pr: PullRequest, *shas: str) -> None:
        """Make sure every commit is present, fetching the PR head from origin once if not."""
        missing = [s for s in shas if not self._has(s)]
        if not missing:
            return
        self._git("fetch", "-q", "origin", f"pull/{pr.number}/head")
        still = [s for s in missing if not self._has(s)]
        if still:
            raise ReviewError(
                f"commit {', '.join(s[:12] for s in still)} is not in {self.path}, even after "
                f"`git fetch origin pull/{pr.number}/head`; fetch it there, or pass --repo-path to a clone that has it"
            )

    def _has(self, sha: str) -> bool:
        return self._git("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0

    def show(self, sha: str, path: str) -> str | None:
        """The file's content at ``sha``, or None when it does not exist there."""
        key = (sha, path)
        if key not in self._cache:
            done = self._git("show", f"{sha}:{path}")
            self._cache[key] = done.stdout.decode("utf-8", errors="replace") if done.returncode == 0 else None
        return self._cache[key]


# --------------------------------------------------------------------------- attribution
def sessions_for(store: Store, pr: PullRequest) -> list[str]:
    """Sessions whose map lists this PR among the ones it created."""
    return [
        row["sessionId"]
        for row in store.rows()
        if any(
            str(p.get("repo") or "").lower() == pr.repo.lower() and p.get("number") == pr.number
            for p in row.get("prs") or []
        )
    ]


def _started(doc: dict[str, Any]) -> float:
    try:
        return datetime.fromisoformat(str(doc["session"]["startedAt"]).replace("Z", "+00:00")).timestamp()
    except (KeyError, ValueError):
        return 0.0


def _pr_repo_ids(doc: dict[str, Any], pr: PullRequest) -> set[str]:
    """The map's repo ids for the PR's repository: same remote, or a fork's clone of it (same name)."""
    name = pr.repo.lower().rsplit("/", 1)[-1]
    return {
        r["id"]
        for r in doc.get("repos", [])
        if str(r.get("remote") or "").lower() == pr.repo.lower() or str(r.get("name") or "").lower() == name
    }


def _observed_edits(
    sessions: dict[str, tuple[dict[str, Any], dict[str, Any] | None]], pr: PullRequest
) -> dict[str, list[dict[str, Any]]]:
    """Every session's evidence edits in the PR's repo, per path, in wall-clock order."""
    rows = []
    for order, (sid, (doc, evidence)) in enumerate(sessions.items()):
        repo_ids = _pr_repo_ids(doc, pr)
        t = {e["seq"]: e.get("t", 0) for e in doc.get("events", [])}
        for i, edit in enumerate((evidence or {}).get("edits", [])):
            if edit.get("path") and edit.get("repo") in repo_ids:
                at = _started(doc) + t.get(edit["seq"], 0) / 1000
                rows.append((at, order, edit["seq"], i, {**edit, "session_id": sid, "event_id": edit["seq"]}))
    by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for *_, edit in sorted(rows, key=lambda r: r[:4]):
        by_path[edit["path"]].append(edit)
    return by_path


def _attribute(
    comment: dict[str, Any], checkout: Checkout, pull: dict[str, str], edits: list[dict[str, Any]]
) -> dict[str, Any]:
    head = checkout.show(pull["head"], comment["path"])
    if head is None:
        return {"kind": "unknown", "reason": "file_not_in_head"}
    final = line_fingerprints(head)
    index = comment["line"] - 1
    if not 0 <= index < len(final):
        return {"kind": "unknown", "reason": "line_out_of_range"}
    if not edits:
        return {"kind": "unknown", "reason": "no_evidence"}
    base = checkout.show(pull["base"], comment["path"])
    surviving, _, _ = replay_owners(base, head, edits)
    inserted = inserted_hashes(edits, base is None)
    owner = surviving.get(index)
    if owner is None:
        observed = any(final[index] in hashes for hashes in inserted.values())
        return {"kind": "unknown", "reason": "ambiguous" if observed else "not_observed"}
    sid, seq = owner
    agent = next(e["agent"] for e in edits if e["session_id"] == sid and e["event_id"] == seq)
    claimants = {sid} | {other for other, hashes in inserted.items() if final[index] in hashes}
    return {"kind": "edit", "session": sid, "seq": seq, "agent": agent, "shared": len(claimants) > 1}


def _label(e: dict[str, Any], files: dict[int, str]) -> str:
    if e.get("verb"):
        return str(e["verb"])
    path = next((files.get(t["file"]) for t in e.get("targets", []) if "file" in t), None)
    return " ".join(x for x in (e.get("tool") or e["kind"], path) if x)


def causal_path(doc: dict[str, Any], seq: int, agent: str) -> list[dict[str, Any]]:
    """Up to five steps on the edited file leading to the edit, then the next verify.

    Content-free: each step is the event's kind and the label the map already
    holds (tool and path, or a shell verb).
    """
    events = doc.get("events", [])
    if not 0 <= seq < len(events):
        return []
    edit = events[seq]
    files = {f["id"]: f["path"] for f in doc.get("files", [])}
    edited = {t["file"] for t in edit.get("targets", []) if t.get("touch") == "edit" and "file" in t}
    steps = [
        e for e in events[: seq + 1]
        if e.get("agent") == agent
        and e["kind"] in _TOUCH_KINDS
        and any(t.get("file") in edited for t in e.get("targets", []))
    ][-CAUSAL_STEPS:]
    verify = next((e for e in events[seq + 1:] if e.get("agent") == agent and e["kind"] == "verify"), None)
    if verify is not None:
        steps.append(verify)
    return [{"seq": e["seq"], "kind": e["kind"], "label": _label(e, files)} for e in steps]


def build_review(
    store: Store,
    pr: PullRequest,
    *,
    sessions: list[str],
    checkout: Checkout,
    pull: dict[str, str],
    comments: list[dict[str, Any]],
) -> dict[str, Any]:
    """The ``agentplay-review/v1`` document for one PR."""
    loaded: dict[str, tuple[dict[str, Any], dict[str, Any] | None]] = {}
    for sid in sessions:
        doc = store.get(sid)
        if doc is None:
            raise ReviewError(f"no map for session {sid}; run `agentplay build` first")
        loaded[sid] = (doc, store.evidence(sid))
    checkout.ensure(pr, pull["base"], pull["head"])
    by_path = _observed_edits(loaded, pr)
    out = []
    for c in comments:
        attribution = _attribute(c, checkout, pull, by_path.get(c["path"], []))
        path = (
            causal_path(loaded[attribution["session"]][0], attribution["seq"], attribution["agent"])
            if attribution["kind"] == "edit"
            else []
        )
        out.append({**c, "attribution": attribution, "causalPath": path})
    return {
        "schema": REVIEW_SCHEMA,
        "pr": {"repo": pr.repo, "number": pr.number, "url": pull.get("url") or pr.url,
               "base": pull["base"], "head": pull["head"]},
        "sessions": sessions,
        "builtAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "comments": out,
    }


# --------------------------------------------------------------------------- storage
def reviews_dir(store: Store) -> Path:
    return store.base / "reviews"


def save_review(store: Store, doc: dict[str, Any]) -> Path:
    """Write (or overwrite) the PR's review doc; one file per PR, so reruns converge."""
    pr = PullRequest(doc["pr"]["repo"], doc["pr"]["number"])
    path = reviews_dir(store) / f"{pr.file_stem}.json"
    _atomic_write(path, json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8"))
    return path


def reviews_for(store: Store, session_id: str) -> list[dict[str, Any]]:
    """Every stored review doc that lists ``session_id``, newest first."""
    found = []
    for path in sorted(reviews_dir(store).glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and doc.get("schema") == REVIEW_SCHEMA and session_id in (doc.get("sessions") or []):
            found.append(doc)
    return sorted(found, key=lambda d: str(d.get("builtAt") or ""), reverse=True)
