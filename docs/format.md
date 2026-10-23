# The `agentplay/v1` format

A **session map** is a redacted, chronological replay of one coding-agent session. It records every tool call, file touch, MCP call, skill, subagent and model request, in order, with repo-relative paths. It does not contain the content of any of them.

One map is one JSON object. Its machine-readable definition is [`schema/agentplay-v1.schema.json`](../schema/agentplay-v1.schema.json) (JSON Schema 2020-12). This page explains the meaning of each part.

- **Media type:** `application/json`. It is commonly stored gzipped as `*.json.gz`.
- **Schema id:** `"schema": "agentplay/v1"`. Readers must also accept `"entelligence.session-map/v1"`, the format's earlier name. The shape is identical.
- **Evolution:** fields are only ever added inside v1, never removed or repurposed. Readers must ignore fields they do not know. A breaking change would be `agentplay/v2`.

## Contents

- [Redaction contract](#redaction-contract)
- [Top level](#top-level)
- [`session`](#session)
- [`totals`](#totals)
- [`repos`, `files`](#repos-files)
- [`agents`](#agents)
- [`tasks`](#tasks)
- [`prs`](#prs)
- [`events`](#events)
- [Outcomes](#outcomes)
- [Content hash](#content-hash)
- [Writing a producer](#writing-a-producer)
- [Local-only companions](#local-only-companions): edit evidence, review docs (also from factory cards) and factory digests, not part of `agentplay/v1`

## Redaction contract

These rules are hard rules. A producer that breaks one of them is not producing a valid session map.

1. **No text content.** A map never carries prompt text, assistant or reasoning text, tool inputs, tool outputs, file contents, or command arguments.
2. **Shell commands contribute a verb only.** The verb is the program plus a subcommand for multi-verb tools such as `git log`, `npm run`, `pytest` or `gh pr view`. Never flags, paths or values.
3. **Subagents contribute only `subagentType` and `model`.** Never their description or prompt.
4. **User turns contribute only shape:** `chars` (the length), plus counts of `images` and `urls`. The `images` count is the only trace of an image; producers must never embed image data.
5. **Model responses contribute only metadata:** token counts, latency, stop reason, whether there was thinking, the number of tool calls requested, and the length of text blocks (`textChars`).
6. **Paths.** Inside a git repository, a path is repo-relative. Outside any repository, it collapses to a bucket label: `/tmp (scratch)`, `~/Documents`, `~/Downloads`, `~/Desktop`, `~/.claude/<area>`, `~ (home)` or `outside-repo`.
7. **Path modes** (`session.pathMode`):
   - `relative` (default): repo-relative paths as above.
   - `dirs_only`: each file name becomes `•<sha256(path)[:6]>` (the repo-relative path); directories are kept. The hash is short and unkeyed, so a common name in a known directory (`README.md`, `package.json`) can be guessed by hashing candidates; use `hashed` with a key for stronger protection.
   - `hashed`: every path segment becomes `HMAC-SHA256(key, segment)[:10]`. Without a key, this mode falls back to `dirs_only`.

Identifiers that may appear are repository names and remotes, MCP server and tool names, skill names, subagent types, model names, web host names, hook names, and PR or ticket numbers and URLs. These are needed to make a map useful. Producers should treat them as metadata the user can see and remove.

The reference producer ([`python/src/agentplay/builder.py`](../python/src/agentplay/builder.py)) is tested against this contract. [`tests/test_builder.py::test_builder_never_leaks_free_text`](../python/tests/test_builder.py) builds a map from a transcript seeded with secret prompt text, secret arguments and secret subagent descriptions, and asserts that none of it appears in the output.

## Top level

```jsonc
{
  "schema": "agentplay/v1",
  "session": { … },
  "totals":  { … },
  "repos":   [ … ],
  "files":   [ … ],
  "agents":  [ … ],
  "tasks":   [ … ],   // optional
  "prs":     [ … ],
  "events":  [ … ]    // sorted by t; events[i].seq === i
}
```

## `session`

| field | type | meaning |
|---|---|---|
| `id` | string | Stable session id, conventionally `local:<agent>:<native id>` |
| `agentTool` | string | `claude-code`, `codex`, `cursor`, `copilot`, `copilot-vscode`, `opencode`, `kiro`, … |
| `cliVersion` | string | Version of the producer. Excluded from the content hash |
| `startedAt` | ISO-8601 UTC | Time of the first event |
| `durationMs` | number | Time from the first event to the last |
| `models` | string[] | Every model that served a request |
| `pathMode` | `relative` \| `dirs_only` \| `hashed` | See the redaction contract |
| `truncated` | bool | `true` when the producer stopped at its event cap (20,000 in the reference producer) |
| `outcome` | string | See [Outcomes](#outcomes) |
| `role` | `root` \| `subagent` \| `helper` \| `unknown` \| null | `subagent` and `helper` sessions are usually hidden from lists; their work already appears inside their parent |
| `parentSessionId` | string \| null | Set when `role` is `subagent` |

## `totals`

These are session-wide counters, so a list or dashboard never has to scan `events`.

| field | meaning |
|---|---|
| `calls` | tool calls. Excludes `llm`, `user`, `compaction`, `error` and `delay` events |
| `requests` | model requests |
| `userTurns` | user messages |
| `subagents` | subagent lanes |
| `errors` | events with `err: true` |
| `tokens` | all input, cache-write, cache-read and output tokens |
| `costUsd` | estimated cost at list prices. Advisory only |
| `filesTouched`, `filesCreated`, `filesDeleted` | distinct files |
| `errorsBySource` | `{tool, mcp, llm, hook, orchestration: n}` |
| `errorsByClass` | `{exit_code, timeout, rate_limit, …: n}` |
| `retries` | events with `retryOf` |
| `delays`, `delayMs` | delay events and their total duration |
| `maxContext` | largest main-agent context (`in + cw + cr`) seen |
| `compactions` | compaction events |
| `llmLatencyP50`, `llmLatencyP95` | model latency percentiles, in ms |
| `outputTokens` | output tokens only |
| `tasks` | number of task segments |
| `prsCreated`, `prsMerged`, `prsClosed`, `prComments`, `commits`, `ticketsCreated`, `ticketsUpdated`, `artifacts`, `skillsCreated`, `skillsUpdated`, `modelSwitches` | outputs the session produced (confirmed only) |
| `promptChars`, `promptImages`, `promptUrls` | the shape of what the user sent |

## `repos`, `files`

```jsonc
"repos": [{ "id": "r0", "name": "api", "remote": "acme/api" }],
"files": [{ "id": 0, "repo": "r0", "path": "src/orders.ts", "reads": 2, "edits": 2, "hits": 1, "first": 17, "op": "modify" }]
```

- **`files[].first`** is the `seq` of the event that first touched the file.
- **`files[].op`** is the last file operation seen for it: `create`, `modify`, `delete`, `rename-from` or `rename-to`.
- **Touch levels** (used by `targets[].touch`): `hit` means the file was named or listed, for example matched by a search. `read` means its contents were read. `edit` means it was changed.

## `agents`

```jsonc
"agents": [
  { "id": "main", "parent": null, "kind": "main", "start": 0, "end": 250000,
    "calls": 20, "requests": 16, "tokens": 900000, "cost": 1.61, "outcome": "pr_created" },
  { "id": "sa-ae824d54211f", "parent": "main", "kind": "subagent", "subagentType": "Explore",
    "model": "claude-haiku-4-5", "launchSeq": 14, "start": 31000, "end": 70000, "…": "same counters" }
]
```

Every event carries `agent`, which is `main` or one of these ids. `launchSeq` points at the `subagent` event that started the lane. A subagent has no further nesting.

## `tasks`

This field is optional. It records how the session divides into units of work:

```jsonc
"tasks": [{ "n": 1, "id": "t1", "name": "Explore the request pipeline", "source": "segments",
            "startSeq": 0, "endSeq": 12, "startT": 0, "endT": 70000, "firstTurn": 0, "lastTurn": 1 }]
```

- **`source`** is either `segments` or `turns`. `segments` means the tasks came from a task segmenter, which may use a model and produce names. `turns` means one task per user turn.
- **`startSeq` and `endSeq`** are inclusive.
- **`events[].task`** holds the task number `n`.
- **Fallback:** a malformed `tasks` block must never fail a reader. Drop it and fall back to one task per user turn.

## `prs`

```jsonc
"prs": [{ "provider": "github", "repo": "acme/api", "number": 142, "url": "https://github.com/acme/api/pull/142", "seq": 51, "state": "open" }]
```

A PR or MR is listed only once its URL is confirmed in a tool result. `seq` points at the event that created it.

## `events`

Every event has these fields:

| field | meaning |
|---|---|
| `seq` | index into `events` |
| `t` | ms since the first event |
| `kind` | see the table below |
| `agent` | `main` or a subagent id |
| `err` | the event failed |
| `task` | task number (when `tasks` is present) |
| `model` | the model driving the agent at this point |
| `cat` | tool category: `file`, `shell`, `vcs`, `test`, `cloud`, `http`, `fs`, `package`, `build`, `mcp`, `subagent`, `skill`, `web` or `tool` |

| kind | extra fields |
|---|---|
| `read`, `search`, `edit`, `exec`, `verify` | `tool`, `verb` (shell only), `targets[]`, `outBytes`, `dur`, `vcs?` |
| `mcp` | `server`, `tool`, `outBytes`, `dur` |
| `skill` | `skill`, `typed?` (`true` when the user started the skill by typing `/name`; absent when the model called the Skill tool) |
| `subagent` | `subagentType`, `model?`, `child` (agent id) |
| `web` | `host` (a hostname, or `search`) |
| `tool` | `tool`: any other tool (ToolSearch, AskUserQuestion, TodoWrite, …) |
| `llm` | `model`, `in` (fresh input), `cw` (cache write), `cr` (cache read), `out`, `cost?`, `latency`, `stop`, `thinking`, `toolCalls`, `textChars` |
| `user` | `chars`, `images`, `urls` |
| `error` | `source` (the builder writes `llm`, `hook` or `orchestration`; the schema also accepts `tool`, `mcp` and null, which a builder could use for a standalone error event, but a failed tool or MCP call is instead flagged on its own `tool`/`mcp` event and counted under that source in `totals.errorsBySource`), `errClass`, `hook?`, `hookEvent?`, `srcUuid?`, `ref?`, `retry?` |
| `delay` | `cause` (`tool`, `subagent`, `llm` or `stall`), `ms`, `ref?` (the causing event) |
| `compaction` | `trigger` (`auto` or `manual`), `preTokens`, `postTokens`, `ms` |
| `commit` | `repo`, `sha`, `files`, `ins`, `del` |
| `pr_merge`, `pr_close`, `pr_comment` | `repo`, `number`, `url?` |
| `ticket` | `tracker`, `action` (`create` or `update`), `id` |
| `artifact` | `url`, `title?`, `version?` |
| `skill_write` | `name`, `action` (`create` or `update`) |
| `model_switch` | `from`, `to` |

`verify` is a shell command recognised as a test, build, lint or type check (for example `pytest`, `pnpm test`, `tsc`, `cargo clippy`, `git diff --check`). Output kinds such as `commit` and `pr_merge` are emitted only when confirmed in a tool result.

### Targets

```jsonc
"targets": [{ "file": 0, "touch": "edit", "op": "modify" }, { "scope": "outside", "label": "/tmp (scratch)", "touch": "read" }]
```

### Signals

- **Failures.** A failed tool, MCP or subagent call can carry the following:
  - `errClass`: one of `exit_code`, `timeout`, `not_found`, `validation`, `permission_denied`, `blocked`, `stale_file`, `model_unavailable`, `rate_limit`, `api_error`, `interrupted`, `no_result`, `subagent_failed`, `subagent_killed` or `other`;
  - `exitCode`;
  - `retryOf`: the `seq` of the failed attempt it repeats (same agent, tool, verb or server, and first target, within the last 8 calls);
  - `attempt`: 2 or higher.
- **`error` events** record failures that are not tied to one tool call. Their `errClass` is one of `rate_limit`, `usage_limit`, `spend_limit`, `overloaded`, `server_error`, `auth`, `bad_request`, `api_error`, `refusal_fallback`, `hook_blocking`, `hook_error` or `interrupted`.
  - For a hook failure, `hook` is the hook's name (such as `PostToolUse:Bash`) and `hookEvent` the event it ran on (such as `PostToolUse`). `ref` is the `seq` of the tool call the hook ran after, when there is one. `srcUuid` is the id of the transcript record, an opaque random value that lets a viewer on the machine that recorded the session look the hook's message and command up; neither is ever stored.
- **`delay` events** are produced at these thresholds in the reference producer: a tool call of 30 s or more, model latency of 60 s or more, or a stall where the main agent was idle for 120 s or more while not waiting on the user.
- **`vcs`** is present on any VCS operation: `{provider: git|github|gitlab|bitbucket, action, repo?, number?}`.
  - For git, `action` is the subcommand. For gh or glab it is `<noun>_<verb>`.
  - `pr_create` is set only once the PR URL is confirmed. `pr_create_attempt` and `pr_create_failed` cover the rest.

## Outcomes

`session.outcome` and `agents[].outcome` take one of the values below. The first rule that matches wins.

1. `pr_created`: a confirmed PR or MR creation in the lane.
2. `interrupted`: an `interrupted` error in the last 12 events.
3. `blocked`: a `blocked` or `permission_denied` error in the last 12 events.
4. `verified`: the last `verify` event succeeded, with no edit after it.
5. `unresolved_errors`: 2 or more errors in the last 12 events.
6. `incomplete`: the lane ends on an edit or exec with no assistant text after it.
7. `completed`: none of the above.

## Content hash

`contentHash` is the SHA-256 of the canonical JSON of the map, with `session.cliVersion` removed. Canonical JSON means sorted keys, compact separators and UTF-8. Two producers that agree on a map produce the same hash, so independent builders can be checked against each other.

## Writing a producer

To make your agent's sessions work with the viewer:

1. Emit one map per session following this page. Start with the required fields: `schema`, `session.id`, `session.startedAt`, `totals` and `events[].t` and `kind`. Everything else is additive.
2. Validate your maps against the JSON Schema.
3. Keep to the redaction contract. If your agent's own logs contain text, drop it at the producer. Readers assume maps are safe to show.
4. Open a map by dragging the file onto the viewer, or add it to your local store with `agentplay import map.json.gz` so it shows in your session list. For a new agent, the better path is a parser in this repo; see [CONTRIBUTING.md](../CONTRIBUTING.md#adding-an-agent).

## Local-only companions

The reference tool keeps three more kinds of document next to the maps. None is part of `agentplay/v1`: they break the redaction contract on purpose, so they stay on the machine that made them. `agentplay export` never includes them, the server shows them only to a viewer on that machine (factory digests not at all), and a map never points at them. Another producer or reader may ignore them entirely.

### Edit evidence

`$AGENTPLAY_HOME/evidence/<map key>.json`, one per Claude Code map, written and deleted together with the map:

```jsonc
{
  "schema": "agentplay-evidence/v1",
  "sessionId": "local:claude-code:5f2c…",
  "edits": [
    { "seq": 31, "agent": "main", "repo": "r0", "path": "src/middleware/rateLimit.ts",
      "operation": "replace", "before": ["<sha256 of a line>", "…"], "after": ["…"] }
  ]
}
```

- One entry per observed change: an `Edit` is one entry per hunk of its recorded patch, a `Write` of a new file one `write` entry with every line.
- `seq` and `agent` are the map event of the call. `repo` is the map's repo id and `path` the unredacted repo-relative path, whatever the map's path mode, so it lines up with a PR's file paths. Both are null for a file outside any repository.
- `before` and `after` are SHA-256 hashes of whole lines. A short line's hash can be reversed by hashing candidates, which is why this is not in the map.

### Review docs

`agentplay review` reads comments from GitHub or from an input file:

```jsonc
{
  "schema": "agentplay-review-input/v1",
  "comments": [
    { "id": "c1", "path": "src/orders.ts", "line": 38, "body": "…",
      "author": "reviewer", "url": "https://…", "source": "my-review-bot", "createdAt": "2026-10-01T10:00:00Z",
      "commit": "<sha>", "until": "2026-10-01T09:00:00Z" }
  ]
}
```

`path` (repo-relative), `line` (1-based, in the PR head) and `body` are required; the rest is optional. `commit` is the commit whose tree `line` refers to, when that is not the PR head. `until` is an ISO-8601 time (UTC when it has no offset): only edits observed before it are replayed for this comment. Together they let a comment on an earlier commit find its edit after a later edit in the same session rewrote the line. Without them a comment is attributed in the PR head against every observed edit, as before. It writes `$AGENTPLAY_HOME/reviews/<owner>_<repo>_<n>.json`, overwritten on every run:

```jsonc
{
  "schema": "agentplay-review/v1",
  "pr": { "repo": "acme/api", "number": 142, "url": "https://github.com/acme/api/pull/142", "base": "<sha>", "head": "<sha>" },
  "sessions": ["local:claude-code:5f2c…"],
  "maps": { "local:claude-code:5f2c…": "<contentHash>" },
  "builtAt": "2026-10-17T10:00:00Z",
  "comments": [
    { "id": "c1", "url": null, "author": "reviewer", "source": "file", "path": "src/orders.ts", "line": 38, "body": "…",
      "attribution": { "kind": "edit", "session": "local:claude-code:5f2c…", "seq": 40, "agent": "main", "shared": false },
      "causalPath": [ { "seq": 15, "kind": "read", "label": "Read src/orders.ts" },
                      { "seq": 40, "kind": "edit", "label": "Edit src/orders.ts" },
                      { "seq": 44, "kind": "verify", "label": "pnpm test" } ] }
  ]
}
```

- **`attribution`** is either `{"kind": "edit", …}`, the session, map seq and agent of the edit that owns the commented head line after replaying the sessions' edit evidence over the PR base, or `{"kind": "unknown", "reason": …}`. `shared` is true when another listed session's edit wrote that line before the owning edit replaced it.
- **Reasons:** `base` (the replay carried the line through from the PR base untouched), `not_observed` (no placed edit wrote the line: it was written or rewritten outside the sessions), `ambiguous` (the edit that wrote it was observed, identically, in more than one session), `unplaced` (an edit that inserted this text could not be placed in the replay, so its position is unknown), `line_out_of_range` (the head file has no such line), `line_split_differs` (the line holds a character that ends a line for the replay but not for git, such as a form feed, so which part of it was meant is unknown; later lines are still counted as git counts them), `file_not_in_head` and `no_evidence` (none of the sessions has edit evidence for that file).
- **`causalPath`** comes from the attributed session's map alone, so it is content-free: up to five events of the same agent that touched the edited file up to and including the edit, then the first `verify` after it. `label` is the tool and the map's path, or the shell verb.
- **`maps`** is each session's map `contentHash` at the time of the review. A rebuild can renumber seqs, so `GET /api/reviews` adds `"stale": true` to a doc whose hash for the requested session no longer matches, and the viewer then neither lights paths nor seeks for it.
- `body`, `author` and `url` are the comment as given. They live only in this file. `commit` and `until` are kept when the input had them.

### Factory reviews

`agentplay factory-review --cards <file|-> --repo-path <clone>` writes the same review docs for an OpenClaw software factory's published cards, from the Workboard's card JSON (`openclaw workboard list --json`), without an input file. A card is traced when it has a GitHub PR URL, a base and head commit, and at least one session map (matched to the card as for [factory digests](#factory-digests)). Its comments are:

- each open inner-review finding, `worktree.rounds[r].review.items[]` with `fixed: false`, as `{"id": "<card8>-r<r>-f<i>", "author": "inner-reviewer", "source": "factory-review"}`, with `body` prefixed by `[<rule>] ` when the finding names a rule. Fixed findings are not traced;
- each `path:line` mentioned in an operator comment (`source: "operator"`) or in an agent's rework verdict (a `source` starting `agent:` and a body that opens with `Verdict: rework`, Markdown emphasis allowed, as Workboard reads it), as `{"id": "<card8>-r<r>-<commentId>-m<j>", "author": <the comment's source>, "source": "rework"}` with the whole comment as `body`. A path contains a `/` or is a file name with an extension; `:12`, `:L12` and a range `:12-20` (its first line) are read, in backticks or not.

Every comment is pinned to a round `r`. A finding belongs to its round. An operator comment reviews the published PR, so it belongs to the round with the latest `worktree.reviews[r].publishedAt` at or before its `createdAt`, and only while that round is the newest on the board: a comment at or after the next round's `importedAt`, or before the first publish (such as an answer to a `needs_input` question), is not traced. An agent's rework verdict comes between a round's import and its publish, so it belongs to the round with the latest `importedAt` at or before its `createdAt`. No other card comment is mined. Comments without a `source` are Workboard's own, such as a session's summary, which is posted before its round is imported and so would pin to the previous round's tree; agent comments that are not a rework verdict, and accept verdicts, are left out too. The comment's `commit` is the round's final commit (`worktree.rounds[r].changes.to`), and its `line` is read in that commit's tree, not the PR head. `until` is the round's `publishedAt`, else its `importedAt`, so a later round of the same resumed session cannot hide the line. The publish is preferred because the import lands only seconds after the round's last edit, and clock skew between the node and the gateway can put real edits past it; the next round's rework starts only after the publish. A comment of a round with neither time has no `until` and is replayed against every observed edit. A round without its final commit is skipped. An operator comment is traced only when it follows the publish of the round it reviews. One posted before the rework decision, while the inner review is still running, is not traced. The doc's `base` is the worktree's `baseCommit` and `head` the handoff's `headCommit`.

A card with nothing to trace writes nothing. A rerun overwrites the same `<owner>_<repo>_<n>.json`. A commit missing from the clone even after a `git fetch` of the PR head fails that card alone, on stderr, and the command then exits 1.

### Factory digests

`agentplay factory-digest` joins the maps of an OpenClaw software factory's sessions with the factory's Workboard cards (`openclaw workboard list --json`) and writes `$AGENTPLAY_HOME/digests/factory-<YYYY-MM-DD>.json`, a Markdown table beside it, and `factory-latest.json` and `.md` copies. A session belongs to a card when its transcript's project directory ends in `-wb-<cardId>`. A rerun on the same day overwrites that day's files. With `--card`, the command prints the digest of the named cards (Markdown, or JSON with `--json`) and writes nothing; with `--out` as well, it writes `factory-cards-<YYYY-MM-DD>.json` and `.md` to that folder. Neither touches the nightly files.

```jsonc
{
  "schema": "agentplay-factory-digest/v1",
  "builtAt": "2026-10-17T06:00:00Z",
  "window": { "from": "2026-10-10T06:00:00Z", "to": "2026-10-17T06:00:00Z", "days": 7 },
  "cards": [
    { "cardId": "3f1c…", "title": "Add the rate limiter", "labels": ["api"], "status": "review",
      "publishedAt": "2026-10-15T11:00:00Z", "pullRequestUrl": "https://github.com/acme/api/pull/77",
      "headCommit": "<sha>", "baseCommit": "<sha>", "reworkRound": 1,
      "reviews": [ { "round": 1, "verdict": "rework", "decidedBy": "reviewer" } ],
      "sessions": ["local:claude-code:5f2c…"],
      "totals": {
        "peakContext": 142000, "compactions": 0, "costUsd": 3.2141, "tokens": 2210400, "requests": 61, "calls": 88,
        "errors": { "total": 3, "bySource": { "tool": 3 }, "byClass": { "exit_code": 2, "permission_denied": 1 } },
        "retries": 1, "delays": 0, "edits": 14,
        "verify": { "afterLastEdit": true, "verbsAfterLastEdit": ["pnpm test", "git diff"], "lastVerifyFailed": false },
        "stops": { "permissionDenied": 1, "blocked": 0 },
        "skills": { "pstack:how": { "calls": 2, "failed": 0 }, "pstack:tdd": { "calls": 1, "failed": 0 } }
      },
      "attempts": [
        { "id": "a1", "status": "succeeded", "startedAt": "2026-10-15T10:00:00Z", "endedAt": "2026-10-15T10:20:00Z",
          "error": null, "contextTokens": 98000, "transcriptOutcome": "verified", "stoppedOnDenial": false, "fidelity": "ok", "metrics": { /* as totals */ } }
      ],
      "unattributed": null,
      "flags": [] }
  ],
  "unmatchedSessions": ["local:claude-code:9a0b…"]
}
```

- **Selection.** A card is published at its handoff's `publishedAt` when the handoff phase is `published`, otherwise at its rework's `acceptedAt`. The digest lists the cards published in the window, or the cards named with `--card`, whenever they were published.
- **`totals`** covers every event of the card's sessions, in wall-clock order; it is null when no session was found. **`metrics`** and **`unattributed`** use the same shape. `peakContext` is the largest main-agent request (fresh input plus cache write plus cache read), as `totals.maxContext` in a map. `verify.afterLastEdit` is null when there is no edit, and `verbsAfterLastEdit` names the shell verbs of the `verify` events after it. A check refused as `permission_denied` or `blocked` did not run, so `verify` leaves it out; it still counts in `errors` and `stops`. `skills` counts the `skill` events by skill name, whether the model called the Skill tool or the user typed `/name`; `failed` counts those whose `err` is set.
- **`attempts`** are the card's own attempts. Each event goes to the latest-started attempt whose start and end (open while running) hold it, with one second of slack because a map's `session.startedAt` drops its fraction of a second, so an event placed from it can sit up to a second early (the card's times are milliseconds); events outside every attempt are `unattributed`. A session resumed for rework so yields two attempts. `status`, `error` and `contextTokens` are the card's; `transcriptOutcome` is the [outcome](#outcomes) of the attempt's events alone, so it reads `blocked` when a denial is among the last events even if the agent worked past it. `stoppedOnDenial` is true when the attempt's last call (any event but a request, user turn, compaction, error, delay, model switch or VCS record) failed as `permission_denied` or `blocked`.
- **`fidelity`** is `mismatch` when the card and the transcript disagree: an attempt the card calls `stopped` or `succeeded` that has `stoppedOnDenial`, or a `succeeded` one whose `transcriptOutcome` is `interrupted`. It is `no_events` when no event falls in the attempt.
- **`flags`**: `no-verify-after-edit`, `verify-failed-last`, `stopped-on-permission` (some attempt has `stoppedOnDenial`; a denial the agent recovered from only counts in `stops`), `attempt-fidelity-mismatch`, `no-transcript` and `skill-load-failed` (some skill in `totals.skills` has `failed` above 0; the Markdown names those skills after the card).
- **`unmatchedSessions`** are factory sessions whose card is not in the input.
- `title` and `labels` are the card's own text. They live only in these files.
