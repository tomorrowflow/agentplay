/**
 * Pure helpers behind the Session Explorer layout: the one sidebar
 * (tasks / subagents / insights), the subagent view, and the one-line
 * task/subagent strip above the timeline. Kept free of React so every rule
 * here is unit-tested (sessionLayout.test.ts).
 */
import type {
  SessionExplorerSession,
  SessionMap,
  SessionMapEvent,
  SessionMapPr,
} from './schema';
import {
  NON_CALL,
  agentLabel,
  ancestorsOf,
  catLabel,
  clockAt,
  errClassLabel,
  errClassOf,
  fmtDur,
  fmtTok,
  fmtUsd,
  hookErrorTitle,
  isOutputEvent,
  llmTokens,
  outputTotals,
  srcSeqOf,
  stateAt,
  type NodeRef,
  type NodeType,
  type OutputTotals,
  type SessionTree,
} from './sessionMapModel';
import {
  hasReplayEvents,
  summarizeEvents,
  taskNoun,
  type Scope,
  type TaskSummary,
  type TaskWindow,
} from './sessionScope';

// --- windows -----------------------------------------------------------------

/** An inclusive event-index window. */
export interface IndexWindow {
  start: number;
  end: number;
}

/** First and last index of the events that pass `test`, or null. */
export function indexWindow(
  events: readonly SessionMapEvent[],
  test: (e: SessionMapEvent) => boolean
): IndexWindow | null {
  let start = -1;
  let end = -1;
  events.forEach((e, i) => {
    if (!test(e)) return;
    if (start < 0) start = i;
    end = i;
  });
  return start < 0 ? null : { start, end };
}

/** The [first, last] event window a subagent ran in. */
export const agentWindow = (
  events: readonly SessionMapEvent[],
  agentId: string
): IndexWindow | null => indexWindow(events, (e) => e.agent === agentId);

/** The [first, last] event window of task `n` (events must be task-stamped). */
export const taskIndexWindow = (
  events: readonly SessionMapEvent[],
  n: number
): IndexWindow | null => indexWindow(events, (e) => e.task === n);

/** For every event of `agentId` (in order), its index in `events`: the map
 * from a lensed (subagent-only) replay back onto the whole session. */
export function lensSourceIndex(
  events: readonly SessionMapEvent[],
  agentId: string
): number[] {
  const out: number[] = [];
  events.forEach((e, i) => {
    if (e.agent === agentId) out.push(i);
  });
  return out;
}

/** The lens index whose source index is nearest `fullIndex` (ties go to the
 * earlier one). `src` is ascending; returns 0 for an empty map. */
export function nearestLensIndex(src: readonly number[], fullIndex: number) {
  if (!src.length) return 0;
  let lo = 0;
  let hi = src.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (src[mid] < fullIndex) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && fullIndex - src[lo - 1] <= src[lo] - fullIndex) return lo - 1;
  return lo;
}

/** 0..1 position of the playhead inside a window (clamped). */
export function windowProgress(w: IndexWindow | null, p: number): number {
  if (!w) return 0;
  if (w.end <= w.start) return p >= w.end ? 1 : 0;
  return Math.max(0, Math.min(1, (p - w.start) / (w.end - w.start)));
}

// --- tasks -------------------------------------------------------------------

/** The task under the playhead, from the event's task stamp. */
export function taskAt(
  events: readonly SessionMapEvent[],
  p: number
): number | null {
  const t = events[p]?.task;
  return typeof t === 'number' && t > 0 ? t : null;
}

/** The task `delta` steps from `n` (clamped to the list); with no current
 * task, stepping forward starts at the first and back at the last. */
export function adjacentTask(
  tasks: readonly TaskWindow[],
  n: number | null,
  delta: number
): number | null {
  if (!tasks.length) return null;
  const i = n == null ? -1 : tasks.findIndex((t) => t.n === n);
  if (i < 0) return (delta >= 0 ? tasks[0] : tasks[tasks.length - 1]).n;
  const j = Math.max(0, Math.min(tasks.length - 1, i + delta));
  return tasks[j].n;
}

/** Per-task summaries in one pass (events must be task-stamped). */
export function taskSummaries(map: SessionMap): Map<number, TaskSummary> {
  const byTask = new Map<number, SessionMapEvent[]>();
  for (const e of map.events) {
    if (typeof e.task !== 'number') continue;
    const list = byTask.get(e.task);
    if (list) list.push(e);
    else byTask.set(e.task, [e]);
  }
  const out = new Map<number, TaskSummary>();
  for (const [n, events] of byTask)
    out.set(
      n,
      summarizeEvents(
        events,
        events.map((e) => e.model ?? '')
      )
    );
  return out;
}

/** "HH:MM–HH:MM" of a span that starts `startT` ms into the session. */
export const spanLabel = (
  sessionStart: string,
  startT: number,
  endT: number
): string =>
  `${clockAt(sessionStart, startT).slice(0, 5)}–${clockAt(sessionStart, endT).slice(0, 5)}`;

/** "$1.23", or "cost n/a" when nothing in the span was priced. */
export const costLabel = (cost: number): string =>
  cost > 0 ? fmtUsd(cost) : 'cost n/a';

const plural = (n: number, one: string, many = `${one}s`) =>
  `${n} ${n === 1 ? one : many}`;

/** Short "2 commits · 1 PR merged" line of the non-zero outputs. */
export function outputsLine(o: OutputTotals, prsCreated = 0): string {
  const parts: string[] = [];
  if (prsCreated) parts.push(plural(prsCreated, 'PR created', 'PRs created'));
  if (o.prsMerged) parts.push(plural(o.prsMerged, 'PR merged', 'PRs merged'));
  if (o.prsClosed) parts.push(`${o.prsClosed} closed unmerged`);
  if (o.prComments) parts.push(plural(o.prComments, 'PR comment'));
  if (o.commits) parts.push(plural(o.commits, 'commit'));
  if (o.ticketsCreated) parts.push(plural(o.ticketsCreated, 'ticket'));
  if (o.ticketsUpdated) parts.push(`${o.ticketsUpdated} ticket updates`);
  if (o.artifacts) parts.push(plural(o.artifacts, 'artifact'));
  if (o.skillsCreated + o.skillsUpdated)
    parts.push(plural(o.skillsCreated + o.skillsUpdated, 'skill write'));
  return parts.join(' · ');
}

// --- subagents ---------------------------------------------------------------

export interface SubagentInfo {
  id: string;
  /** 1-based position in the ordered list */
  index: number;
  label: string;
  type: string;
  window: IndexWindow;
  /** ms since session start */
  startT: number;
  endT: number;
  calls: number;
  requests: number;
  cost: number;
  tokens: number;
  /** the task the subagent was spawned in (spawn event, else first event) */
  parentTask: number | null;
}

/**
 * Every subagent that ran in the session, in the order it started, with its
 * window, totals and parent task. `map` is the whole (task-stamped) session
 * map; subagents with no events in it are skipped.
 */
export function orderedSubagents(
  map: SessionMap,
  ordinals: Map<string, number>
): SubagentInfo[] {
  const byAgent = new Map<string, number[]>();
  map.events.forEach((e, i) => {
    if (!e.agent || e.agent === 'main') return;
    const list = byAgent.get(e.agent);
    if (list) list.push(i);
    else byAgent.set(e.agent, [i]);
  });
  const spawnTask = new Map<string, number>();
  for (const e of map.events)
    if (e.kind === 'subagent' && e.child && typeof e.task === 'number')
      if (!spawnTask.has(e.child)) spawnTask.set(e.child, e.task);
  const rows: Omit<SubagentInfo, 'index'>[] = [];
  for (const a of map.agents) {
    if (a.id === 'main') continue;
    const idx = byAgent.get(a.id);
    if (!idx?.length) continue;
    const events = idx.map((i) => map.events[i]);
    const s = summarizeEvents(
      events,
      events.map((e) => e.model ?? '')
    );
    const first = events[0];
    const last = events[events.length - 1];
    rows.push({
      id: a.id,
      label: agentLabel(map, a.id, ordinals),
      type: a.subagentType || 'subagent',
      window: { start: idx[0], end: idx[idx.length - 1] },
      startT: first.t,
      endT: last.t,
      calls: s.calls,
      requests: s.requests,
      cost: s.cost,
      tokens: s.tokens,
      parentTask:
        spawnTask.get(a.id) ??
        (typeof first.task === 'number' ? first.task : null),
    });
  }
  rows.sort(
    (x, y) =>
      x.window.start - y.window.start ||
      (ordinals.get(x.id) ?? 0) - (ordinals.get(y.id) ?? 0)
  );
  return rows.map((r, i) => ({ ...r, index: i + 1 }));
}

/** The subagent `delta` steps from `id` in the ordered list, or null at
 * either end. */
export function adjacentSubagent(
  subs: readonly SubagentInfo[],
  id: string,
  delta: number
): string | null {
  const i = subs.findIndex((s) => s.id === id);
  if (i < 0) return null;
  return subs[i + delta]?.id ?? null;
}

// --- touched-by-helper list -----------------------------------------------------

export interface TouchedItem {
  id: string;
  name: string;
  type: NodeType;
  count: number;
}

export interface TouchedGroup {
  id: string;
  name: string;
  items: TouchedItem[];
}

/**
 * Leaves the replay touched, grouped under their top-level map group (a repo,
 * "Outside repo", "Subagents", ...), each group and its items sorted by
 * touch count. Model nodes are left out: every request lights one.
 */
export function touchedGroups(
  tree: SessionTree,
  refs: NodeRef[][]
): TouchedGroup[] {
  const st = stateAt(refs, refs.length - 1);
  const groups = new Map<string, TouchedGroup & { total: number }>();
  for (const leaf of tree.leaves) {
    if (leaf.type === 'model' || leaf.type === 'root') continue;
    const cnt = st.get(leaf.id)?.cnt ?? 0;
    if (!cnt) continue;
    let top = leaf;
    while (top.parent && top.parent.type !== 'root') top = top.parent;
    let g = groups.get(top.id);
    if (!g) {
      g = { id: top.id, name: top.name, items: [], total: 0 };
      groups.set(top.id, g);
    }
    g.items.push({ id: leaf.id, name: leaf.name, type: leaf.type, count: cnt });
    g.total += cnt;
  }
  return [...groups.values()]
    .sort((a, b) => b.total - a.total)
    .map(({ id, name, items }) => ({
      id,
      name,
      items: items.sort((a, b) => b.count - a.count),
    }));
}

// --- the one-line strip ----------------------------------------------------------

export type StripState =
  | { kind: 'sub'; id: string; playing: boolean }
  | {
      kind: 'task';
      n: number;
      playing: boolean;
      /** no task is selected: the strip follows the task under the playhead */
      followsPlayhead: boolean;
    }
  | null;

/**
 * When the strip above the timeline shows, and what: an open subagent or a
 * selected task shows while the sidebar is collapsed or playback runs; while
 * playing with no task selected it follows the task under the playhead (only
 * when the session has more than one task).
 */
export function stripState(s: {
  task: number | null;
  sub: string | null;
  collapsed: boolean;
  playing: boolean;
  playheadTask: number | null;
  taskCount: number;
}): StripState {
  const visible = s.collapsed || s.playing;
  if (s.sub != null)
    return visible ? { kind: 'sub', id: s.sub, playing: s.playing } : null;
  if (s.task != null)
    return visible
      ? { kind: 'task', n: s.task, playing: s.playing, followsPlayhead: false }
      : null;
  if (s.playing && s.playheadTask != null && s.taskCount > 1)
    return {
      kind: 'task',
      n: s.playheadTask,
      playing: true,
      followsPlayhead: true,
    };
  return null;
}

// --- sidebar -------------------------------------------------------------------

export type SidebarTab = 'tasks' | 'errors' | 'subagents' | 'insights';
export const SIDEBAR_TABS: readonly SidebarTab[] = [
  'tasks',
  'errors',
  'subagents',
  'insights',
];

/** Tabs a session's sidebar shows: Tasks only with more than one task,
 * Errors and Subagents only when it has any; Insights always. */
export function sidebarTabs(
  taskCount: number,
  subCount: number,
  errorCount = 0
): SidebarTab[] {
  const out: SidebarTab[] = [];
  if (taskCount > 1) out.push('tasks');
  if (errorCount > 0) out.push('errors');
  if (subCount > 0) out.push('subagents');
  out.push('insights');
  return out;
}

/** The tab to show: the requested one when it is available, else the first. */
export const resolveSidebarTab = (
  want: SidebarTab | null,
  available: readonly SidebarTab[]
): SidebarTab =>
  want && available.includes(want) ? want : (available[0] ?? 'insights');

export const SIDEBAR_COLLAPSED_KEY = 'session-explorer:sidebar-collapsed';

type MaybeStorage = Pick<Storage, 'getItem' | 'setItem'> | null | undefined;

const defaultStorage = (): MaybeStorage => {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
};

/** Persisted collapsed state; false whenever storage is missing or throws. */
export function readSidebarCollapsed(
  storage: MaybeStorage = defaultStorage()
): boolean {
  try {
    return storage?.getItem(SIDEBAR_COLLAPSED_KEY) === '1';
  } catch {
    return false;
  }
}

export function writeSidebarCollapsed(
  collapsed: boolean,
  storage: MaybeStorage = defaultStorage()
): void {
  try {
    storage?.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? '1' : '0');
  } catch {
    // private mode / blocked storage: the state just is not remembered
  }
}

/*
 * `useSyncExternalStore` source for the collapsed state, so the first client
 * render reads it directly instead of flashing the expanded sidebar from a
 * mount-time effect. The in-memory value is this tab's source of truth (so
 * collapsing still works with storage blocked); storage seeds and persists
 * it, and `storage` events keep other tabs in step.
 */
let collapsedMem: boolean | null = null;
const collapsedListeners = new Set<() => void>();

export function getSidebarCollapsed(): boolean {
  if (collapsedMem === null) collapsedMem = readSidebarCollapsed();
  return collapsedMem;
}

/** SSR has no storage: render expanded, React swaps in the client value. */
export function getSidebarCollapsedServer(): boolean {
  return false;
}

export function setSidebarCollapsed(next: boolean): void {
  collapsedMem = next;
  writeSidebarCollapsed(next);
  collapsedListeners.forEach((l) => l());
}

export function subscribeSidebarCollapsed(listener: () => void): () => void {
  collapsedListeners.add(listener);
  const onStorage = (e: StorageEvent) => {
    if (e.key !== SIDEBAR_COLLAPSED_KEY) return;
    collapsedMem = e.newValue === '1';
    listener();
  };
  if (typeof window !== 'undefined')
    window.addEventListener('storage', onStorage);
  return () => {
    collapsedListeners.delete(listener);
    if (typeof window !== 'undefined')
      window.removeEventListener('storage', onStorage);
  };
}

/** "12:00–12:30 · 30m · $1.23 · 4 calls · 1 commit" for a task in the strip. */
export function taskStripMeta(
  startedAt: string,
  t: TaskWindow,
  s: TaskSummary | undefined
): string {
  const parts = [
    spanLabel(startedAt, t.startT, t.endT),
    fmtDur(t.endT - t.startT),
    costLabel(s?.cost ?? 0),
    `${s?.calls ?? 0} calls`,
  ];
  const outs = s ? outputsLine(s.outputs, s.prsCreated) : '';
  if (outs) parts.push(outs);
  return parts.join(' · ');
}

export function subStripMeta(startedAt: string, s: SubagentInfo): string {
  return [
    spanLabel(startedAt, s.startT, s.endT),
    `${s.calls} calls`,
    costLabel(s.cost),
    `${fmtTok(s.tokens)} tokens`,
  ].join(' · ');
}

/**
 * Left/width (0..1) of a subagent's position bar in a clipped 100%-wide
 * track. The minimum width keeps a one-event helper visible; the offset is
 * clamped so that minimum still fits when the helper ran at the very end.
 */
export function spanBarGeometry(
  startT: number,
  endT: number,
  durationMs: number
): { left: number; width: number } {
  const MIN = 0.015;
  const d = Math.max(1, durationMs);
  const width = Math.min(1, Math.max(MIN, (endT - startT) / d));
  const left = Math.max(0, Math.min(1 - width, startT / d));
  return { left, width };
}

// --- fit-viewport layout: compact header, toolbar, side column ---------------

/** One PR in the header's "N PRs" popover. */
export interface PrMenuItem {
  key: string;
  /** `repo #12`, or `#12` when the PR carries no repo */
  label: string;
  provider: string;
  /** null when the map's URL is not http(s): shown as text, never a link */
  url: string | null;
  state: string | null;
}

/** `url` if it is an http(s) URL, else null. A map is untrusted input (any
 * file can be dropped on the page), and a `javascript:` href runs on click. */
export function safeLinkUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const { protocol } = new URL(url);
    return protocol === 'https:' || protocol === 'http:' ? url : null;
  } catch {
    return null;
  }
}

/** The session's PRs for the header popover, one row per PR (the first
 * occurrence wins when the map lists a PR twice), in map order. */
export function prMenuItems(prs: readonly SessionMapPr[]): PrMenuItem[] {
  const out = new Map<string, PrMenuItem>();
  for (const pr of prs) {
    const key = `${pr.provider}:${pr.repo ?? ''}:${pr.number}`;
    if (out.has(key)) continue;
    out.set(key, {
      key,
      label: pr.repo ? `${pr.repo} #${pr.number}` : `#${pr.number}`,
      provider: pr.provider,
      url: safeLinkUrl(pr.url),
      state: pr.state ?? null,
    });
  }
  return [...out.values()];
}

/** "1 PR" / "9 PRs": the header's PR popover trigger. */
export const prCountLabel = (n: number): string =>
  n === 1 ? '1 PR' : `${n} PRs`;

/** The one-line outcome: every non-zero output, in the order the old chip row
 * used.
 *
 * The task count is deliberately not here. It was, and on a session that
 * produced nothing it made the callout read "13 tasks" directly under the
 * `Tasks 13` row three lines above -- a brand-tinted box restating its
 * neighbour. With no outputs there is now no callout, which is the honest
 * answer: the session produced nothing. */
export function outcomeParts(map: SessionMap): string[] {
  const o = outputTotals(map.events);
  const items: [number, string, string?][] = [
    [map.totals.prsCreated ?? 0, 'PR created', 'PRs created'],
    [o.prsMerged, 'PR merged', 'PRs merged'],
    [o.prsClosed, 'closed unmerged', 'closed unmerged'],
    [o.prComments, 'PR comment'],
    [o.commits, 'commit'],
    [o.ticketsCreated, 'ticket created', 'tickets created'],
    [o.ticketsUpdated, 'ticket updated', 'tickets updated'],
    [o.artifacts, 'artifact'],
    [o.skillsCreated, 'skill created', 'skills created'],
    [o.skillsUpdated, 'skill updated', 'skills updated'],
    [o.modelSwitches, 'model switch', 'model switches'],
  ];
  return items
    .filter(([n]) => n > 0)
    .map(([n, one, many]) => plural(n, one, many));
}

/** Toolbar trigger text for the task / prompt filter. */
export function taskFilterLabel(
  tasks: readonly TaskWindow[],
  task: number | null
): string {
  const t = task != null ? tasks.find((x) => x.n === task) : undefined;
  return t ? `${t.n} · ${t.name}` : `All ${taskNoun(tasks).many.toLowerCase()}`;
}

/** A task, model or subagent filter is narrowing the replay. */
export const hasScopeFilter = (scope: Scope, sub: string | null): boolean =>
  scope.task != null || scope.model != null || !!sub;

/** Title of the right column's Details card. */
export function detailsTitle(s: {
  inspector: boolean;
  selected: TaskWindow | undefined;
  taskCount: number;
  /** what the windows are called (`taskNoun`) */
  noun: { one: string };
  scoped: boolean;
}): string {
  if (s.inspector) return 'Subagent';
  if (s.selected) return `${s.noun.one} ${s.selected.n} of ${s.taskCount}`;
  return s.scoped ? 'Filtered session' : 'Whole session';
}

/** "1,204 chars · 2 images · 1 URL" for a task's prompt row. */
export function promptLine(
  s: Pick<TaskSummary, 'promptChars' | 'promptImages' | 'promptUrls'>
): string {
  const plural = (n: number, one: string) => `${n} ${one}${n > 1 ? 's' : ''}`;
  const parts = [`${s.promptChars} chars`];
  if (s.promptImages) parts.push(plural(s.promptImages, 'image'));
  if (s.promptUrls) parts.push(plural(s.promptUrls, 'URL'));
  return parts.join(' · ');
}

/** The sessions list's day groups (list order kept; a day that reappears
 * later in the list starts a new group, as before). */
export function groupSessionsByDay(
  sessions: readonly SessionExplorerSession[]
): { day: string; items: SessionExplorerSession[] }[] {
  const groups: { day: string; items: SessionExplorerSession[] }[] = [];
  for (const s of sessions) {
    const day = (s.sessionDate ?? s.startedAt).slice(0, 10);
    const last = groups[groups.length - 1];
    if (last && last.day === day) last.items.push(s);
    else groups.push({ day, items: [s] });
  }
  return groups;
}

/** One row of the toolbar's Agent picker. */
export interface AgentOption {
  /** agent id; `main` is the whole session (every agent) */
  id: string;
  label: string;
  calls: number;
  cost: number;
  /** set by `agentOptionsInScope`: no events in the prompt/model scope */
  empty?: boolean;
}

/** The Agent picker's rows: the whole session, then every subagent (as
 * `Sub n: type`). Empty when the session ran no subagents. */
export function agentOptions(
  map: SessionMap,
  ordinals: Map<string, number>
): AgentOption[] {
  const subs = map.agents.filter((a) => a.id !== 'main');
  if (!subs.length) return [];
  return [
    {
      id: 'main',
      label: 'All agents',
      calls: map.totals.calls,
      cost: map.totals.costUsd,
    },
    ...subs.map((a) => ({
      id: a.id,
      label: agentLabel(map, a.id, ordinals),
      calls: a.calls,
      cost: a.cost,
    })),
  ];
}

/**
 * The Agent picker's rows counted inside the prompt/model scope (`scoped` is
 * `deriveScopeDoc`'s output, before the lens): the same rows, with the calls
 * and cost the scope holds, `empty` where the agent has no event in it.
 */
export function agentOptionsInScope(
  options: readonly AgentOption[],
  scoped: SessionMap
): AgentOption[] {
  const active = new Set(scoped.events.map((e) => e.agent));
  return options.map((o) => {
    if (o.id === 'main')
      return {
        ...o,
        calls: scoped.totals.calls,
        cost: scoped.totals.costUsd,
        empty: !hasReplayEvents(scoped),
      };
    const a = scoped.agents.find((x) => x.id === o.id);
    return {
      ...o,
      calls: a?.calls ?? 0,
      cost: a?.cost ?? 0,
      empty: !active.has(o.id),
    };
  });
}

/** The empty-replay sentence, naming only the filters that are set. */
export function emptyScopeDescription(
  scope: Scope,
  sub: string | null,
  noun: { one: string }
): string {
  const names = [
    scope.task != null ? noun.one.toLowerCase() : null,
    scope.model != null ? 'model' : null,
    sub ? 'agent' : null,
  ].filter((n): n is string => n != null);
  const list =
    names.length > 1
      ? `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
      : names.join('');
  return `No events match the selected ${list}. Clear a filter above.`;
}

/** The collapsed sidebar rail's vertical label. */
export function sidebarRailLabel(s: {
  /** the open subagent's label, if any */
  subLabel: string | null;
  mode: 'list' | 'session';
  sessionId: string;
  taskCount: number;
  /** what the windows are called (`taskNoun`) */
  noun: { one: string; many: string };
}): string {
  if (s.subLabel) return `↳ ${s.subLabel}`;
  if (s.mode !== 'session') return 'Sessions';
  const shortId = (s.sessionId.split(':').pop() ?? s.sessionId).slice(0, 8);
  return `${shortId} · ${s.taskCount} ${(s.taskCount === 1 ? s.noun.one : s.noun.many).toLowerCase()}`;
}

// --- event categories: the dock's one chip row (timeline, map, Activity) -----

/**
 * The event categories behind the dock's chip row. One selection drives the
 * timeline's Events bar (matching marks bright, the rest dim), the map
 * (matching nodes bright) and the Activity feed.
 *
 * Mapping (event kind -> category; `categoryMatches` is the membership test,
 * `categoryOf` the one primary category an event is coloured by):
 * - prompt:  `user` turns
 * - error:   any failed event (`err`) and `error` signals -- also counted in
 *            the category of what failed (an errored MCP call is MCP too)
 * - delay:   `delay` and `compaction`
 * - out:     commits, PR merge / close / comment, tickets, artifacts, skill
 *            writes, and a tool call that opened a PR (`vcs.action`)
 * - edit:    `edit`
 * - explore: `read` and `search` (the read / view / find calls)
 * - mcp:     `mcp` calls (and any call categorised `cat: 'mcp'`)
 * - skill:   `skill` invocations (and `cat: 'skill'`)
 * - tool:    every other tool call: `exec`, `verify`, `web`, `tool`, and the
 *            `subagent` spawn call
 * - model:   `llm` requests and `model_switch`
 * - cost:    `llm` requests that cost something
 * - tokens:  `llm` requests that carry tokens
 */
export type Category =
  | 'prompt'
  | 'error'
  | 'delay'
  | 'out'
  | 'edit'
  | 'explore'
  | 'mcp'
  | 'skill'
  | 'tool'
  | 'model'
  | 'cost'
  | 'tokens';

export const CATEGORIES: readonly Category[] = [
  'prompt',
  'error',
  'delay',
  'out',
  'edit',
  'explore',
  'mcp',
  'skill',
  'tool',
  'model',
  'cost',
  'tokens',
];

export const CATEGORY_LABEL: Record<Category, string> = {
  prompt: 'Prompts',
  error: 'Errors',
  delay: 'Delays',
  out: 'Outputs',
  edit: 'Edits',
  explore: 'Exploration',
  mcp: 'MCP',
  skill: 'Skill',
  tool: 'Tools',
  model: 'Model',
  cost: 'Cost',
  tokens: 'Tokens',
};

/** Delays, Outputs and the activity categories are the noisiest, so the
 * dock starts on Prompts + Errors. */
export const DEFAULT_CATEGORIES: readonly Category[] = ['prompt', 'error'];

const EXPLORE_KINDS = new Set(['read', 'search']);
const MODEL_KINDS = new Set(['llm', 'model_switch']);
const isMcp = (e: SessionMapEvent) => e.kind === 'mcp' || e.cat === 'mcp';
/** A skill read from its SKILL.md file counts too: the Skill tool can't load repo skills in factory runs. */
const isSkill = (e: SessionMapEvent) =>
  e.kind === 'skill' || e.cat === 'skill' || (e.kind === 'read' && !!e.skill);
/** A tool call that no more specific call category claims -- including
 * Outputs: an `exec` that opened a PR is an output, not "another tool". */
const isOtherTool = (e: SessionMapEvent) =>
  !NON_CALL.has(e.kind) &&
  e.kind !== 'edit' &&
  !EXPLORE_KINDS.has(e.kind) &&
  !isMcp(e) &&
  !isSkill(e) &&
  !isOutputEvent(e);

const CATEGORY_TEST: Record<Category, (e: SessionMapEvent) => boolean> = {
  prompt: (e) => e.kind === 'user',
  error: (e) => !!e.err || e.kind === 'error',
  delay: (e) => e.kind === 'delay' || e.kind === 'compaction',
  out: isOutputEvent,
  edit: (e) => e.kind === 'edit',
  explore: (e) => EXPLORE_KINDS.has(e.kind),
  mcp: (e) => !NON_CALL.has(e.kind) && isMcp(e),
  skill: (e) => !NON_CALL.has(e.kind) && isSkill(e),
  tool: isOtherTool,
  model: (e) => MODEL_KINDS.has(e.kind),
  cost: (e) => e.kind === 'llm' && (e.cost ?? 0) > 0,
  tokens: (e) => e.kind === 'llm' && llmTokens(e) > 0,
};

/** Whether `e` belongs to `cat` (an event can be in several). */
export const categoryMatches = (e: SessionMapEvent, cat: Category): boolean =>
  CATEGORY_TEST[cat](e);

/** Whether `e` belongs to any category in `cats`. */
export const matchesAnyCategory = (
  e: SessionMapEvent,
  cats: Iterable<Category>
): boolean => {
  for (const c of cats) if (CATEGORY_TEST[c](e)) return true;
  return false;
};

/** Primary categories, most specific first: the one an event is coloured by
 * when none of its selected categories applies. `cost` / `tokens` are
 * measures of a model request, never primary. */
const PRIMARY_ORDER: readonly Category[] = [
  'prompt',
  'delay',
  'out',
  'edit',
  'explore',
  'mcp',
  'skill',
  'model',
  'tool',
];

/** The one category an event is drawn as. A standalone `error` signal is an
 * error; a failed call keeps the category of the call (its error shows
 * through `categoryMatches`). Every event has one (`tool` catches the rest). */
export function categoryOf(e: SessionMapEvent): Category {
  if (e.kind === 'error') return 'error';
  return PRIMARY_ORDER.find((c) => CATEGORY_TEST[c](e)) ?? 'tool';
}

/** The category to colour `e` by under a selection: the first selected one
 * it matches (so an errored edit reads red while Errors is on), else its
 * primary category. */
export function displayCategory(
  e: SessionMapEvent,
  selected: ReadonlySet<Category>
): Category {
  for (const c of CATEGORIES)
    if (selected.has(c) && CATEGORY_TEST[c](e)) return c;
  return categoryOf(e);
}

/** Indices (in time order) of the events in `cat`, optionally only those
 * `allow` accepts (the open subagent's events in the lensed timeline). */
export function categoryIndices(
  events: readonly SessionMapEvent[],
  cat: Category,
  allow?: (i: number) => boolean
): number[] {
  const out: number[] = [];
  events.forEach((e, i) => {
    if (CATEGORY_TEST[cat](e) && (!allow || allow(i))) out.push(i);
  });
  return out;
}

/** Toggle one category (the selection may become empty). */
export function toggleCategory(
  selected: ReadonlySet<Category>,
  c: Category
): Set<Category> {
  const next = new Set(selected);
  if (next.has(c)) next.delete(c);
  else next.add(c);
  return next;
}

/** The chips' click order across a selection change `prev` -> `next`:
 * newly selected chips are appended, deselected ones dropped. (The URL
 * rebuilds a selection in CATEGORIES order, so the Set's own order can't
 * say which chip was clicked last.) */
export function pickHistory(
  history: readonly Category[],
  prev: ReadonlySet<Category>,
  next: ReadonlySet<Category>
): Category[] {
  const out = history.filter((c) => next.has(c));
  for (const c of CATEGORIES)
    if (next.has(c) && !prev.has(c) && !out.includes(c)) out.push(c);
  return out;
}

/** The most recently picked selected chip, optionally among `eligible`.
 * Chips selected without a click this visit (the URL or default) count as
 * picked first, in CATEGORIES order. */
export function lastPicked(
  history: readonly Category[],
  selected: ReadonlySet<Category>,
  eligible?: readonly Category[]
): Category | null {
  const pickedSet = new Set(history);
  const order = [
    ...CATEGORIES.filter((c) => selected.has(c) && !pickedSet.has(c)),
    ...history.filter((c) => selected.has(c)),
  ];
  const eligibleSet = eligible && new Set(eligible);
  for (let i = order.length - 1; i >= 0; i--)
    if (!eligibleSet || eligibleSet.has(order[i])) return order[i];
  return null;
}

/** Every category: a selection that narrows nothing. */
export const ALL_CATEGORIES: ReadonlySet<Category> = new Set(CATEGORIES);

/** The selection is exactly the default one. */
export const isDefaultCategories = (s: ReadonlySet<Category>): boolean =>
  s.size === DEFAULT_CATEGORIES.length &&
  DEFAULT_CATEGORIES.every((c) => s.has(c));

/** The selection narrows the map / Activity feed: anything but the default,
 * every category, or none of them (all three render unfiltered -- an empty
 * selection is "no filter active", the same as picking every chip, not "hide
 * everything"; `ev=none` round-trips it through the URL as exactly that). */
export const narrowsView = (s: ReadonlySet<Category>): boolean =>
  s.size > 0 && !isDefaultCategories(s) && s.size < CATEGORIES.length;

/** Which measure sizes the LLM marks on the Events bar: Cost wins when both
 * Cost and Tokens are selected. */
export function sizeMetric(s: ReadonlySet<Category>): 'cost' | 'tokens' | null {
  if (s.has('cost')) return 'cost';
  if (s.has('tokens')) return 'tokens';
  return null;
}

/** A mark's size fraction in [0, 1] for `v` against the largest `max`:
 * square-root scaled so one outlier does not flatten the rest; 0 for
 * nothing, and never below `floor` for anything above zero. */
export function magnitude(v: number, max: number, floor = 0.12): number {
  if (!(v > 0) || !(max > 0)) return 0;
  return Math.max(floor, Math.min(1, Math.sqrt(v / max)));
}

const isCategory = (v: string): v is Category =>
  (CATEGORIES as readonly string[]).includes(v);

/**
 * The chip selection from the URL: `ev` (a comma list of categories, `none`
 * for an explicitly empty one); else the default. Unknown values are ignored,
 * never thrown on.
 */
export function parseCategories(ev: string | null | undefined): Set<Category> {
  if (ev === 'none') return new Set();
  const keys = (ev ?? '')
    .split(',')
    .map((k) => k.trim())
    .filter(isCategory);
  if (keys.length) return new Set(CATEGORIES.filter((c) => keys.includes(c)));
  return new Set(DEFAULT_CATEGORIES);
}

/** The `ev` URL value for a selection: null (no param) for the default. */
export function serializeCategories(s: ReadonlySet<Category>): string | null {
  if (isDefaultCategories(s)) return null;
  if (!s.size) return 'none';
  return CATEGORIES.filter((c) => s.has(c)).join(',');
}

/**
 * The map nodes a non-default selection highlights: every node an event in
 * the selection touches (`refs`), plus its ancestors so the branch to it
 * stays lit. Null (the map renders as today) for the default or full
 * selection, or when nothing selected touches the map (prompts / delays
 * alone have no nodes: dimming everything would read as an empty map).
 */
export function mapFocus(
  tree: SessionTree,
  refs: readonly NodeRef[][],
  events: readonly SessionMapEvent[],
  selected: ReadonlySet<Category>
): Set<string> | null {
  if (!narrowsView(selected)) return null;
  const ids: string[] = [];
  events.forEach((e, i) => {
    if (!matchesAnyCategory(e, selected)) return;
    for (const r of refs[i] ?? []) ids.push(r.id);
  });
  if (!ids.length) return null;
  return ancestorsOf(tree, ids);
}

/**
 * The map nodes on one review comment's causal path: the nodes its steps
 * touch, plus their ancestors so the branch to each stays lit. Same shape as
 * `mapFocus`, so the map dims everything else exactly as it does for a
 * category selection -- a path is just a different reason to narrow.
 *
 * `seqs` are event `seq` values, which is what a comment cites, rather than
 * indices into `events`: a scoped map drops events, so the two differ.
 * Returns null when none of them are in this scope, so the caller shows an
 * unchanged map instead of dimming everything to nothing.
 *
 * Matched through `srcSeqOf`, not `e.seq`: a scoped or lensed map renumbers
 * `seq` over the events it kept, so a cited number read against `seq` there
 * resolves to whatever event now sits at that position -- lighting unrelated
 * nodes while the cited ones stay dark.
 */
export function pathFocus(
  tree: SessionTree,
  refs: readonly NodeRef[][],
  events: readonly SessionMapEvent[],
  seqs: readonly number[]
): Set<string> | null {
  const want = new Set(seqs);
  const ids: string[] = [];
  events.forEach((e, i) => {
    const seq = srcSeqOf(e);
    if (seq == null || !want.has(seq)) return;
    for (const r of refs[i] ?? []) ids.push(r.id);
  });
  if (!ids.length) return null;
  return ancestorsOf(tree, ids);
}

/**
 * What the map filters its event refs by, given the dock's chip selection and
 * whether a review comment's causal path is currently lit.
 *
 * A picked path already wins the *focus* set (`pathFocus` over `mapFocus`),
 * but the map narrows twice: it also blanks the refs of every event outside
 * the selection (`activeRefs`), which decides the active dots and the labels.
 * Left narrowed, a path step whose own category is unchecked would be blanked
 * out of both, so picking a comment lit only the steps the chips happened to
 * include. While a path is lit the chips are suspended; with
 * no path they are returned untouched, so ordinary filtering is unchanged.
 */
export const mapChipSelection = (
  selected: ReadonlySet<Category>,
  pathLit: boolean
): ReadonlySet<Category> => (pathLit ? ALL_CATEGORIES : selected);

/** `refs`, with every event that doesn't match the selection blanked out.
 * Feeds the map's "what's currently touched" pipeline (`stateAt`,
 * `activeSet`, `recentTargets`) so that a narrowed selection's recent-N
 * window, dot prominence and labels are scoped to the selection too --
 * otherwise a burst of unselected activity (e.g. many MCP calls while only
 * Edits is selected) fills the recent-touch budget and an edited file's
 * label never appears, or stops updating as the playhead advances past it.
 * Identity (same reference) when the selection doesn't narrow the view, so
 * it costs nothing there. Deliberately NOT applied to `nextTouchOf`'s seek
 * target, which stays on the raw event stream regardless of the selection.
 *
 * A kept event's passive refs (an LLM request's model) turn active only
 * when the selection itself includes Model / Cost / Tokens -- the model
 * *is* what was selected there, and left passive its leaf would never
 * light or get its label. Selected through some other category (e.g. an
 * errored LLM matched via Errors), the model stays passive: it wasn't
 * what the user asked to see, just incidental to the event that was. */
const MODEL_CATEGORIES: ReadonlySet<Category> = new Set([
  'model',
  'cost',
  'tokens',
]);
export function activeRefs(
  refs: NodeRef[][],
  events: readonly SessionMapEvent[],
  selected: ReadonlySet<Category>
): NodeRef[][] {
  if (!narrowsView(selected)) return refs;
  const promotePassive = [...MODEL_CATEGORIES].some((c) => selected.has(c));
  return refs.map((r, i) => {
    if (!matchesAnyCategory(events[i], selected)) return [];
    if (!promotePassive || !r.some((x) => x.passive)) return r;
    return r.map((x) => (x.passive ? { ...x, passive: false } : x));
  });
}

// --- timeline dock: stepping, pinned height ----------------------------------

/**
 * The event `dir` steps from the pinned one in `list` (clamped at the ends).
 * With nothing pinned in the list, forward goes to the first event after the
 * playhead (wrapping to the first) and back to the last one before it
 * (wrapping to the last). Null for an empty list.
 */
export function stepEvent(
  list: readonly number[],
  pinned: number | null,
  p: number,
  dir: 1 | -1
): number | null {
  if (!list.length) return null;
  const k = pinned == null ? -1 : list.indexOf(pinned);
  if (k >= 0) return list[Math.max(0, Math.min(list.length - 1, k + dir))];
  if (dir > 0) return list.find((i) => i > p) ?? list[0];
  for (let j = list.length - 1; j >= 0; j--) if (list[j] < p) return list[j];
  return list[list.length - 1];
}

/** "k/N" when the pinned event is in `list`, else null. */
export function stepPosition(
  list: readonly number[],
  pinned: number | null
): string | null {
  const k = pinned == null ? -1 : list.indexOf(pinned);
  return k >= 0 ? `${k + 1}/${list.length}` : null;
}

/** One row per subagent that has events in the timeline's map, in the
 * order they started, labelled "n · type". */
export function subagentLanes(
  subs: readonly SubagentInfo[],
  events: readonly SessionMapEvent[]
): { id: string; label: string }[] {
  const present = new Set(events.map((e) => e.agent));
  return subs
    .filter((s) => present.has(s.id))
    .map((s) => ({ id: s.id, label: `${s.index} · ${s.type}` }));
}

export const DOCK_HEIGHT_KEY = 'session-explorer:timeline-height';
/** The dock never gets shorter than this (header + a few lanes)... */
export const DOCK_MIN_HEIGHT = 140;
/** ...nor, when dragged, taller than this share of the explorer. */
export const DOCK_MAX_FRACTION = 0.72;

/** A dragged dock height clamped to [min, 72% of the explorer]. */
export function clampDockHeight(h: number, containerH: number): number {
  const max = Math.max(DOCK_MIN_HEIGHT, containerH * DOCK_MAX_FRACTION);
  return Math.round(Math.max(DOCK_MIN_HEIGHT, Math.min(h, max)));
}

/** How far the dock (rendered `dockH` tall) grows past the height the
 * explorer budgets for it: its optional lanes' height (`optionalH`), less
 * whatever a dragged `floor` had already reserved. The explorer extends by
 * exactly this below the fold, so turning on Subagents / Context scrolls the
 * page instead of shrinking the map. */
export function dockOverflow(
  dockH: number,
  optionalH: number,
  floor: number | null
): number {
  return Math.max(
    0,
    Math.round(dockH - Math.max(floor ?? 0, dockH - optionalH))
  );
}

/** The persisted dock height, or null (content height) when it is unset,
 * unparseable, or storage is missing or throws. */
export function readDockHeight(
  storage: MaybeStorage = defaultStorage()
): number | null {
  try {
    const v = Number(storage?.getItem(DOCK_HEIGHT_KEY) ?? '');
    return Number.isFinite(v) && v >= DOCK_MIN_HEIGHT ? Math.round(v) : null;
  } catch {
    return null;
  }
}

export function writeDockHeight(
  h: number | null,
  storage: MaybeStorage = defaultStorage()
): void {
  try {
    // '' (reset) reads back as null: content height
    storage?.setItem(DOCK_HEIGHT_KEY, h == null ? '' : String(Math.round(h)));
  } catch {
    // private mode / blocked storage: the height just is not remembered
  }
}

/* `useSyncExternalStore` source for the dock height, like the sidebar's
 * collapsed state: memory is this tab's truth, storage seeds and persists
 * it, and `storage` events keep other tabs in step. */
let dockMem: number | null | undefined;
const dockListeners = new Set<() => void>();

export function getDockHeight(): number | null {
  if (dockMem === undefined) dockMem = readDockHeight();
  return dockMem;
}

/** SSR has no storage: render at content height. */
export function getDockHeightServer(): number | null {
  return null;
}

/** Set (and persist, unless `persist` is false: mid-drag) the height. */
export function setDockHeight(next: number | null, persist = true): void {
  dockMem = next;
  if (persist) writeDockHeight(next);
  dockListeners.forEach((l) => l());
}

export function subscribeDockHeight(listener: () => void): () => void {
  dockListeners.add(listener);
  const onStorage = (e: StorageEvent) => {
    if (e.key !== DOCK_HEIGHT_KEY) return;
    dockMem = readDockHeight();
    listener();
  };
  if (typeof window !== 'undefined')
    window.addEventListener('storage', onStorage);
  return () => {
    dockListeners.delete(listener);
    if (typeof window !== 'undefined')
      window.removeEventListener('storage', onStorage);
  };
}

// --- errors ------------------------------------------------------------------

/** One failed call, with the detail needed to decide what to do about it. */
export interface SessionError {
  /** Index into the map's events, for seeking. */
  index: number;
  seq: number;
  t: number;
  /** Shell verb, MCP server/tool, or tool name. */
  label: string;
  errClass: string;
  exitCode?: number;
  /** How long the call ran before failing. A 72s failure and an 0.8s one are
   * different problems, and this is the only cost an error row can show. */
  durMs?: number;
  /** Only when it names something real. Every target on an error event is a
   * coarse privacy bucket ("outside-repo", "/tmp (scratch)") unless the call
   * touched a file inside a repo, and a bucket on every row is noise. */
  target?: string;
  /** The task it happened in, for jumping back to what was being asked. */
  task?: number;
  agent: string;
}

/** Failures of one class, newest run first. */
export interface ErrorGroup {
  errClass: string;
  label: string;
  /** Provider-side (rate limit, overload, auth) rather than something this
   * harness can fix per call. Ranked last so it cannot bury the rest. */
  provider: boolean;
  errors: SessionError[];
  /** Longest run of consecutive failures of the same label in this class.
   * One failure is noise; four of the same command in a row is a harness
   * problem, and that is what makes the list actionable rather than a log. */
  worstRun: number;
  worstRunLabel?: string;
}

const errorLabelOf = (e: SessionMapEvent): string => {
  const hookTitle = hookErrorTitle(e);
  if (hookTitle) return hookTitle;
  if (e.kind === 'mcp') return [e.server, e.tool].filter(Boolean).join('.') || 'mcp';
  return e.verb || e.tool || e.kind;
};

/** Only a target that names something. The path mode collapses anything
 * outside a repo into a coarse bucket ("outside-repo", "/tmp (scratch)");
 * repeating that on every row crowds out the detail that does vary. */
const errorTargetOf = (e: SessionMapEvent): string | undefined => {
  const t = e.targets?.find((x) => x.label && x.scope !== 'outside');
  return t?.label ?? undefined;
};

/** Provider-side failures: rate limits, overloads, auth. Real, but not something
 * a per-call fix addresses, and they can outnumber tool failures, so they
 * must not bury the ones the harness can act on. */
const PROVIDER_CLASSES = new Set([
  'api_error',
  'rate_limit',
  'model_unavailable',
  'server_error',
  'overloaded',
  'spend_limit',
  'usage_limit',
  'auth',
]);

/** How a failure is grouped and named.
 *
 * `errClass` is sometimes missing or "other", and
 * "other error" is not something anyone can act on. Those events still carry a
 * tool category, so fall back to it: "Shell failed" beats "other error". */
function errorGroupOf(e: SessionMapEvent): { key: string; label: string } {
  const cls = errClassOf(e);
  // A hook failure is named for its hook: "Stop hook blocked" is a different
  // problem from "PostToolUse:Bash hook error".
  const hookTitle = hookErrorTitle(e);
  if (hookTitle) return { key: `${cls}/${e.hook ?? ''}`, label: hookTitle };
  if (cls && cls !== 'other') return { key: cls, label: errClassLabel(cls) };
  const cat = typeof e.cat === 'string' ? e.cat : e.kind;
  return { key: `cat:${cat}`, label: `${catLabel(cat)} failed` };
}

/**
 * Every failure in the map, grouped by class and ordered by weight.
 *
 * Over whichever map it is given: a scoped map lists that scope, and an index
 * can be handed straight to `seek` on that map. See `wholeSessionErrors` for
 * the list that ignores the scope.
 */
export function sessionErrors(map: SessionMap): {
  groups: ErrorGroup[];
  total: number;
} {
  const all: SessionError[] = [];
  const labelOfKey = new Map<string, string>();
  map.events.forEach((e, index) => {
    if (!e.err && e.kind !== 'error') return;
    const group = errorGroupOf(e);
    labelOfKey.set(group.key, group.label);
    all.push({
      index,
      seq: e.seq ?? index,
      t: e.t ?? 0,
      label: errorLabelOf(e),
      errClass: group.key,
      exitCode: typeof e.exitCode === 'number' ? e.exitCode : undefined,
      durMs: typeof e.dur === 'number' ? e.dur : undefined,
      target: errorTargetOf(e),
      task: typeof e.task === 'number' ? e.task : undefined,
      agent: e.agent ?? 'main',
    });
  });

  const byClass = new Map<string, SessionError[]>();
  for (const err of all) {
    const list = byClass.get(err.errClass);
    if (list) list.push(err);
    else byClass.set(err.errClass, [err]);
  }

  const groups: ErrorGroup[] = [];
  for (const [errClass, errors] of byClass) {
    // Longest consecutive run of the same label, over the whole session: a
    // retry loop is the same command failing again, not merely failing twice.
    let worstRun = 1;
    let worstRunLabel: string | undefined;
    let run = 0;
    let runLabel = '';
    for (const err of all) {
      if (err.errClass === errClass && err.label === runLabel) run += 1;
      else {
        run = err.errClass === errClass ? 1 : 0;
        runLabel = err.errClass === errClass ? err.label : '';
      }
      if (run > worstRun) {
        worstRun = run;
        worstRunLabel = runLabel;
      }
    }
    groups.push({
      errClass,
      label: labelOfKey.get(errClass) ?? errClass,
      provider: PROVIDER_CLASSES.has(errClass),
      errors,
      worstRun,
      worstRunLabel,
    });
  }
  // What the harness can fix first, then the biggest problem: a retry run
  // outranks a larger pile of one-offs.
  groups.sort(
    (a, b) =>
      Number(a.provider) - Number(b.provider) ||
      b.worstRun - a.worstRun ||
      b.errors.length - a.errors.length
  );
  return { groups, total: all.length };
}

/** Errors per task, for the task list's error tags. */
export interface TaskErrors {
  count: number;
  /** Whole-session `seq` (`srcSeqOf`) of the row the Errors tab lists first
   * once scoped to this task, so the tag lands on what the user then sees at
   * the top. Not an index, and not the scoped `seq`: deriving a scope renumbers
   * both, and the jump re-scopes. */
  firstSeq: number;
}

/** The map's failures, each carrying its whole-session number as `seq` so a
 * list built from them reports it rather than its own index. */
function errorEventsBySessionSeq(map: SessionMap): SessionMapEvent[] {
  const out: SessionMapEvent[] = [];
  map.events.forEach((e, index) => {
    if (!e.err && e.kind !== 'error') return;
    out.push({ ...e, seq: srcSeqOf(e) ?? index });
  });
  return out;
}

/** The Errors tab's list: every failure of the whole session, however the
 * player is scoped. Pass the unscoped map. Rows carry the whole-session `seq`
 * (not an index into a scoped map), so use `errorClickAction` to act on one. */
export function wholeSessionErrors(map: SessionMap): {
  groups: ErrorGroup[];
  total: number;
} {
  return sessionErrors({ ...map, events: errorEventsBySessionSeq(map) });
}

/** What clicking an Errors row does: seek the player, or re-scope first. */
export type ErrorClickAction =
  | { kind: 'seek'; index: number }
  | { kind: 'jump'; task: number | null; seq: number };

/**
 * The list covers the whole session, the player may not. An error the current
 * map still holds is sought in place, by whole-session `seq` (a scope renumbers
 * `seq` and the index). One the scope hides re-scopes to its task, or clears the
 * task when it has none, and lands on it there.
 */
export function errorClickAction(
  err: Pick<SessionError, 'seq' | 'task'>,
  scopedMap: SessionMap
): ErrorClickAction {
  const index = scopedMap.events.findIndex((e, i) => (srcSeqOf(e) ?? i) === err.seq);
  if (index >= 0) return { kind: 'seek', index };
  return { kind: 'jump', task: err.task ?? null, seq: err.seq };
}

/** Per task, how many errors it had and where its Errors list starts. Over the
 * whole session (tasks are stamped on `fullMap`), like the rest of the task
 * list, so the tags don't change with the current scope. */
export function taskErrors(map: SessionMap): Map<number, TaskErrors> {
  const byTask = new Map<number, SessionMapEvent[]>();
  for (const ev of errorEventsBySessionSeq(map)) {
    if (typeof ev.task !== 'number') continue;
    const list = byTask.get(ev.task);
    if (list) list.push(ev);
    else byTask.set(ev.task, [ev]);
  }
  const out = new Map<number, TaskErrors>();
  for (const [task, events] of byTask) {
    // The same grouping and ranking the Errors tab applies to this task alone.
    const { groups, total } = sessionErrors({ ...map, events });
    const first = groups[0]?.errors[0];
    if (first) out.set(task, { count: total, firstSeq: first.seq });
  }
  return out;
}
