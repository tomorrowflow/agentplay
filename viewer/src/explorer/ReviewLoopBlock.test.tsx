// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { sessionMapSchema, type ReviewDoc } from '@/core/schema';
import { SessionMapSourceProvider } from '@/data/SourceContext';
import { parseReviews } from '@/data/parse';
import type { SessionMapSource } from '@/data/source';
import { ReviewLoopBlock } from './ReviewLoopBlock';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const map = sessionMapSchema.parse(
  JSON.parse(
    fs.readFileSync(
      path.resolve(__dirname, '../../public/demo/claude-code-rate-limit.json'),
      'utf8'
    )
  )
);
const sid = map.session.id;

const [review] = parseReviews({
  reviews: [
    {
      schema: 'agentplay-review/v1',
      pr: { repo: 'acme/api', number: 142, url: 'https://github.com/acme/api/pull/142', base: 'b', head: 'h' },
      sessions: [sid],
      builtAt: '2026-10-17T10:00:00Z',
      comments: [
        {
          id: '1',
          url: null,
          author: 'rev',
          source: 'file',
          path: 'src/middleware/rateLimit.ts',
          line: 64,
          body: 'Keep this in Redis.',
          attribution: { kind: 'edit', session: sid, seq: 31, agent: 'main', shared: false },
          causalPath: [{ seq: 31, kind: 'edit', label: 'Edit src/middleware/rateLimit.ts' }],
        },
      ],
    },
  ],
});

let root: Root | null = null;
afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

async function render(reviews: ReviewDoc[]): Promise<HTMLElement> {
  const reviewsFn = vi.fn(async () => reviews);
  const source = { id: 'test', label: 'Test', reviews: reviewsFn } as unknown as SessionMapSource;
  const el = document.createElement('div');
  document.body.append(el);
  root = createRoot(el);
  const client = new QueryClient();
  act(() =>
    root!.render(
      <QueryClientProvider client={client}>
        <SessionMapSourceProvider source={source}>
          <ReviewLoopBlock map={map} />
        </SessionMapSourceProvider>
      </QueryClientProvider>
    )
  );
  await act(() =>
    vi.waitFor(() => {
      expect(reviewsFn).toHaveBeenCalled();
      expect(client.isFetching()).toBe(0);
    })
  );
  return el;
}

describe('ReviewLoopBlock', () => {
  it('renders nothing for a session with no review', async () => {
    const el = await render([]);
    expect(el.innerHTML).toBe('');
    expect(el.textContent).not.toContain('Connect your PRs');
  });

  it('renders the traced comments of a local review', async () => {
    const el = await render([review]);
    expect(el.textContent).toContain('1 review comment on acme/api #142');
    expect(el.textContent).toContain('Keep this in Redis.');
  });
});
