// @vitest-environment node
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { parseReviews } from '@/data/parse';
import { realCommentCard, reviewLoopView } from './reviewLoop';
import { sessionMapSchema } from './schema';

const map = sessionMapSchema.parse(
  JSON.parse(
    fs.readFileSync(
      path.resolve(__dirname, '../../public/demo/claude-code-rate-limit.json'),
      'utf8'
    )
  )
);
const sid = map.session.id;

/** A review doc as `agentplay review` writes it. */
const doc = (comments: unknown[]) => ({
  schema: 'agentplay-review/v1',
  pr: { repo: 'acme/api', number: 142, url: 'https://github.com/acme/api/pull/142', base: 'b', head: 'h' },
  sessions: [sid],
  builtAt: '2026-10-17T10:00:00Z',
  comments,
});
const edited = {
  id: '1',
  url: null,
  author: 'rev',
  source: 'file',
  path: 'src/middleware/rateLimit.ts',
  line: 64,
  body: 'Keep this in Redis.',
  attribution: { kind: 'edit', session: sid, seq: 31, agent: 'main', shared: false },
  causalPath: [
    { seq: 12, kind: 'read', label: 'Read src/config/env.ts' },
    { seq: 31, kind: 'edit', label: 'Edit src/middleware/rateLimit.ts' },
  ],
};
const unknown = {
  ...edited,
  id: '2',
  attribution: { kind: 'unknown', reason: 'line_out_of_range' },
  causalPath: [],
};

describe('parseReviews', () => {
  it('parses each review doc on its own and drops the ones that do not parse', () => {
    const reviews = parseReviews({
      success: true,
      reviews: [
        doc([edited, unknown]),
        { ...doc([]), schema: 'agentplay-review/v9' },
        doc([{ ...edited, attribution: { kind: 'edit', session: sid } }]),
      ],
    });
    expect(reviews).toHaveLength(1);
    expect(reviews[0].comments.map((c) => c.attribution)).toEqual([
      edited.attribution,
      unknown.attribution,
    ]);
  });
});

describe('reviewLoopView', () => {
  const [real] = parseReviews({ reviews: [doc([edited])] });
  const [empty] = parseReviews({ reviews: [doc([])] });

  it('shows real comments when a review has any', () => {
    expect(reviewLoopView({ data: [empty, real], isLoading: false })).toEqual({
      kind: 'real',
      reviews: [real],
    });
  });

  it('keeps the locked sample when there is no review, or no comment in one', () => {
    expect(reviewLoopView({ data: [], isLoading: false })).toEqual({ kind: 'locked' });
    expect(reviewLoopView({ data: [empty], isLoading: false })).toEqual({ kind: 'locked' });
    expect(reviewLoopView({ data: undefined, isLoading: false })).toEqual({ kind: 'locked' });
  });

  it('shows nothing while the answer is on its way', () => {
    expect(reviewLoopView({ data: undefined, isLoading: true })).toEqual({ kind: 'pending' });
  });
});

describe('realCommentCard', () => {
  const [review] = parseReviews({ reviews: [doc([edited, unknown])] });

  it('cites the edit and its causal path in this session', () => {
    const card = realCommentCard(review.comments[0], map);
    expect(card).toMatchObject({ file: 'src/middleware/rateLimit.ts', line: 'L64', editSeq: 31, badge: 'edit #31' });
    expect(card.path).toEqual([
      { seq: 12, who: 'Main', what: 'Read src/config/env.ts' },
      { seq: 31, who: 'Main', what: 'Edit src/middleware/rateLimit.ts' },
    ]);
  });

  it('gives an unknown attribution its reason and no path', () => {
    expect(realCommentCard(review.comments[1], map)).toMatchObject({
      editSeq: null,
      badge: 'Line is not in the PR head',
      path: [],
    });
  });

  it('does not light this map for an edit in another session', () => {
    const other = {
      ...review.comments[0],
      attribution: { kind: 'edit' as const, session: 'local:claude-code:other', seq: 31, agent: 'main', shared: false },
    };
    const card = realCommentCard(other, map);
    expect(card).toMatchObject({ editSeq: null, path: [], otherSession: 'local:claude-code:other' });
  });
});
