import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { NuqsTestingAdapter } from 'nuqs/adapters/testing';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { sessionExplorerSessionSchema, type SessionMap } from '@/core/schema';
import { listRows } from '@/data/memoryListing';
import { SessionMapSourceProvider } from '@/data/SourceContext';
import type { SessionMapSource } from '@/data/source';
import type { WindowKey } from '@/core/windowOptions';
import { SessionExplorer } from './SessionExplorer';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const rows = Array.from({ length: 120 }, (_, i) => {
  const n = String(i).padStart(3, '0');
  return sessionExplorerSessionSchema.parse({
    sessionId: `s${n}`,
    sessionTitle: `Session ${n}`,
    startedAt: new Date(Date.UTC(2020, 8, 30) - i * 3_600_000).toISOString(),
    calls: 1,
  });
});

const mapNeverArrives = () => new Promise<SessionMap>(() => {});

function pagedSource() {
  const maps = vi.fn(async (ids: string[]) => ({
    success: true,
    expiresIn: 3600,
    maps: ids.map((id) =>
      rows.some((r) => r.sessionId === id)
        ? { sessionId: id, url: `memory:${id}`, schemaVersion: 1, contentHash: id, bytesGz: null }
        : { sessionId: id, status: 'not_found' as const }
    ),
  }));
  const source: SessionMapSource = {
    id: 'paged',
    label: 'Paged',
    status: async () => ({
      success: true,
      available: true,
      mapCount: rows.length,
      latestSessionDate: null,
      scanning: false,
    }),
    sessions: async (params) => listRows(rows, params),
    maps,
    fetchMap: mapNeverArrives,
  };
  return { source, maps };
}

let root: Root | null = null;

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = '';
});

async function renderExplorer(search: string, windowKey: WindowKey = 'all') {
  const { source, maps } = pagedSource();
  const el = document.body.appendChild(document.createElement('div'));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(el);
  await act(async () => {
    root!.render(
      <QueryClientProvider client={client}>
        <NuqsTestingAdapter searchParams={search}>
          <SessionMapSourceProvider source={source}>
            <SessionExplorer windowKey={windowKey} />
          </SessionMapSourceProvider>
        </NuqsTestingAdapter>
      </QueryClientProvider>
    );
  });
  return { el, maps };
}

async function until(done: () => boolean, what: string) {
  for (let i = 0; i < 100 && !done(); i++) {
    await act(() => new Promise((r) => setTimeout(r, 10)));
  }
  if (!done()) throw new Error(`timed out waiting for ${what}`);
}

const listed = (el: HTMLElement) => !!el.querySelector('nav[aria-label="Sessions with maps"]');

const highlighted = (el: HTMLElement) =>
  el.querySelector('[aria-current="true"]')?.textContent ?? null;

describe('SessionExplorer selection', () => {
  it('opens a linked session that is past the first page', async () => {
    const { el, maps } = await renderExplorer('?session=s080');
    await until(() => maps.mock.calls.length > 0 && listed(el), 'the list and a map request');
    expect(maps.mock.calls[0][0]).toEqual(['s080']);
    expect(highlighted(el)).toBeNull();

    const more = [...el.querySelectorAll('button')].find(
      (b) => b.textContent === 'Load more sessions'
    );
    await act(async () => more!.click());
    await until(() => highlighted(el) != null, 'a highlighted row');
    expect(highlighted(el)).toContain('Session 080');
    expect(maps.mock.calls.map(([ids]) => ids[0]).filter((id) => id !== 's080')).toEqual([]);
  });

  it('shows the not-found state for an id the source does not have', async () => {
    const { el, maps } = await renderExplorer('?session=nope');
    await until(() => maps.mock.calls.length > 0, 'a map request');
    expect(maps.mock.calls[0][0]).toEqual(['nope']);
    const alert = () => el.querySelector('[role="alert"]')?.textContent ?? null;
    await until(() => alert() != null, 'an alert');
    expect(alert()).toContain('Map not available');
    expect(maps.mock.calls.map(([ids]) => ids)).toEqual([['nope']]);
  });

  it('opens a linked session when the window holds no sessions', async () => {
    const { el, maps } = await renderExplorer('?session=s080', 'mtd');
    await until(() => listed(el) || el.textContent!.includes('No session maps yet'), 'the explorer');
    expect(el.textContent).not.toContain('No session maps yet');
    expect(maps.mock.calls[0][0]).toEqual(['s080']);
  });

  it('opens the newest session when the link names none', async () => {
    const { el, maps } = await renderExplorer('');
    await until(() => maps.mock.calls.length > 0 && listed(el), 'the list and a map request');
    expect(maps.mock.calls[0][0]).toEqual(['s000', 's001', 's002']);
    expect(highlighted(el)).toContain('Session 000');
  });
});
