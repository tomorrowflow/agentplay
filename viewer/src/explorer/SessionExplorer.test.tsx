// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { NuqsTestingAdapter } from 'nuqs/adapters/testing';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { sessionExplorerSessionSchema } from '@/core/schema';
import { SessionMapSourceProvider } from '@/data/SourceContext';
import type { SessionMapSource, SessionsParams } from '@/data/source';
import { SessionExplorer } from './SessionExplorer';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Three pages of two, newest first, like the local server's list.
const ids = ['s6', 's5', 's4', 's3', 's2', 's1'];
const row = (sessionId: string) =>
  sessionExplorerSessionSchema.parse({ sessionId, startedAt: '2026-10-13T18:22:17Z', calls: 3 });

let root: Root | null = null;
afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

async function render(session: string) {
  const sessions = vi.fn(async ({ cursor }: SessionsParams) => {
    const start = Number(cursor ?? 0);
    const next = start + 2 < ids.length ? String(start + 2) : null;
    return { sessions: ids.slice(start, start + 2).map(row), nextCursor: next, hasMore: next !== null };
  });
  const maps = vi.fn(async (want: string[]) => ({
    maps: want.map((sessionId) => ({ sessionId, status: 'not_found' as const })),
  }));
  const source = { id: 'test', label: 'Test', sessions, maps } as unknown as SessionMapSource;
  const el = document.createElement('div');
  document.body.appendChild(el);
  root = createRoot(el);
  await act(async () => {
    root!.render(
      <NuqsTestingAdapter searchParams={{ session }}>
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <SessionMapSourceProvider source={source}>
            <SessionExplorer windowKey="all" />
          </SessionMapSourceProvider>
        </QueryClientProvider>
      </NuqsTestingAdapter>
    );
  });
  await vi.waitFor(() => expect(maps).toHaveBeenCalled());
  return { sessions, maps };
}

describe('SessionExplorer', () => {
  it('opens a linked session that is pages deep, not the newest', async () => {
    const { sessions, maps } = await render('s1');
    expect(sessions).toHaveBeenCalledTimes(3);
    expect(maps.mock.calls[0][0]).toContain('s1');
  });
});
