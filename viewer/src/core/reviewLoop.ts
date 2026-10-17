import type { ReviewComment, ReviewDoc, SessionMap } from './schema';
import { subagentOrdinal } from './sessionMapModel';

/**
 * What the review block shows for the open session: its real review docs
 * when `agentplay review` wrote any, the locked sample when it has none, and
 * nothing while the answer is still on its way (so the sample never flashes
 * up in front of real comments).
 */
export type ReviewLoopView =
  | { kind: 'pending' }
  | { kind: 'real'; reviews: readonly ReviewDoc[] }
  | { kind: 'locked' };

export function reviewLoopView(query: {
  data?: readonly ReviewDoc[];
  isLoading: boolean;
}): ReviewLoopView {
  if (query.data?.some((r) => r.comments.length > 0)) {
    return { kind: 'real', reviews: query.data.filter((r) => r.comments.length > 0) };
  }
  return query.isLoading ? { kind: 'pending' } : { kind: 'locked' };
}

const UNKNOWN_LABEL: Partial<Record<string, string>> = {
  base: 'Unchanged from the PR base',
  not_observed: 'No recorded edit wrote this line',
  ambiguous: 'Several sessions made this same edit',
  unplaced: 'A recorded edit here could not be replayed',
  line_out_of_range: 'Line is not in the PR head',
  line_split_differs: 'Line holds a break Git does not count',
  file_not_in_head: 'File is not in the PR head',
  no_evidence: 'No edit evidence for this file',
};

/** One step of a causal path, as the comment card lists it. */
export interface CommentStep {
  seq: number;
  who: string;
  what: string;
}

/** A real review comment, shaped for the comment card. */
export interface RealCommentCard {
  id: string;
  file: string;
  line: string;
  author: string | null;
  text: string;
  /** the attributed edit's seq in this map; null when unknown or in another session */
  editSeq: number | null;
  badge: string;
  path: CommentStep[];
  /** set when the edit is in another session the review lists */
  otherSession: string | null;
}

export function realCommentCard(c: ReviewComment, map: SessionMap, stale = false): RealCommentCard {
  const a = c.attribution;
  const card = {
    id: c.id,
    file: c.path,
    line: `L${c.line}`,
    author: c.author,
    text: c.body,
  };
  if (a.kind === 'unknown') {
    return { ...card, editSeq: null, badge: UNKNOWN_LABEL[a.reason] ?? 'Not attributed', path: [], otherSession: null };
  }
  if (stale && a.session === map.session.id) {
    return { ...card, editSeq: null, badge: 'Traced to an edit', path: [], otherSession: null };
  }
  const badge = a.shared ? `edit #${a.seq} · also written in another session` : `edit #${a.seq}`;
  if (a.session !== map.session.id) {
    return { ...card, editSeq: null, badge, path: [], otherSession: a.session };
  }
  const k = subagentOrdinal(map).get(a.agent);
  const who = a.agent === 'main' ? 'Main' : `Sub ${k ?? ''}`.trim();
  return {
    ...card,
    editSeq: a.seq,
    badge,
    path: c.causalPath.map((s) => ({ seq: s.seq, who, what: s.label })),
    otherSession: null,
  };
}
