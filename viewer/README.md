# agentplay viewer

The Session Explorer as a standalone single-page app: a radial map of every
file, tool, MCP server, subagent, model and output a coding-agent session
touched, a replayable timeline, and the details panels beside them. It reads
`agentplay/v1` map documents (the older `entelligence.session-map/v1` name
is the same shape and is accepted too).

![Session Explorer, light](../docs/images/explorer-light.png)
![Session Explorer, dark](../docs/images/explorer-dark.png)

Vite + React 19 + TypeScript + Tailwind 3. Data through TanStack Query, URL
state (selected session, scope, tabs, timeline chips) through nuqs, so every
view is a shareable link.

## Develop

```bash
pnpm install
pnpm dev             # http://localhost:5173, demo sessions
AGENTPLAY_SERVER=http://127.0.0.1:7317 pnpm dev   # proxy /api to `agentplay view --no-open`
                    # (start that with AGENTPLAY_DEV_ORIGINS=http://localhost:5173 so the server trusts the dev origin)
pnpm test            # vitest: model and data-layer tests
pnpm typecheck
pnpm build           # tsc -b && vite build -> dist/
node scripts/make-demo.mjs      # regenerate public/demo (deterministic)
node scripts/screenshots.mjs    # after a build: ../docs/images/explorer-{light,dark}.png
```

`dist/` uses relative asset paths (`base: './'`), so it can be served from any
path. The Python package ships it and serves it next to its API.

## Data sources

Every component reads through one injected `SessionMapSource`
(`src/data/source.ts`):

```ts
interface SessionMapSource {
  id: string; label: string;
  status(): Promise<Status>;
  sessions(p: { from?, to?, search?, pageSize?, cursor? }): Promise<SessionsPage>;
  maps(ids: string[]): Promise<MapBatch>;
  fetchMap(meta: MapMeta): Promise<SessionMap>;
}
```

`App.tsx` picks one at load:

| Source | When | What it reads |
| --- | --- | --- |
| `httpSource(base)` | `GET /api/status` answers JSON | the local CLI server (contract below) |
| `demoSource()` | no server, or `?demo` in the URL | `demo/index.json` + `demo/<file>` static files |
| `fileSource()` | you drop or pick files | `.json` / `.json.gz` maps, parsed in the tab, never uploaded |

Drag-and-drop works anywhere on the page in every mode; opened files replace
the list until you click "Back". `?server=http://host:port` points the viewer
at a server on another origin.

### Local server contract

All answers are camelCase JSON, validated by the zod schemas in
`src/core/schema.ts`:

- `GET /api/status` -> `{success, available, mapCount, latestSessionDate}`
- `GET /api/sessions?from=&to=&search=&pageSize=50&cursor=` ->
  `{success, sessions: [row], nextCursor, hasMore}`
- `GET /api/map?session_id=A&session_id=B` -> `{success, expiresIn, maps:
  [{sessionId, url, schemaVersion, contentHash, bytesGz} | {sessionId, status:
  "not_found"}]}`
- `GET <url>` -> the map document (plain or gzipped JSON)

## Embedding

The explorer is a plain component tree. To embed it in another React app:

```tsx
<QueryClientProvider client={queryClient}>
  <NuqsAdapter>                        {/* any nuqs adapter */}
    <SessionMapSourceProvider source={httpSource('https://maps.example')}>
      <div className="flex h-screen flex-col">
        <SessionExplorer windowKey="all" onWindowChange={setWindowKey} />
      </div>
    </SessionMapSourceProvider>
  </NuqsAdapter>
</QueryClientProvider>
```

Implement `SessionMapSource` for any other backend. The explorer fills the
height of its flex parent. It needs the `ds-*` tokens in
`src/styles/index.css` and the Tailwind theme in `tailwind.config.ts`; the
tokens are trimmed to what the explorer uses, light and dark (`.dark` on
`<html>`, set from `prefers-color-scheme` or `?theme=light|dark`).

## Layout

- `src/core/` pure TypeScript: map model, layout, scoping, signals, playback,
  timeline drawing, colours, schema and map-loading policy, with their tests.
- `src/explorer/` the React components and hooks.
- `src/data/` the source interface, the three sources, and the TanStack
  Query hooks the components call.
- `src/components/`, `src/lib/`, `src/hooks/` the small UI kit (shadcn-style
  on Base UI), `cn`, the chart palette and helpers.
- `public/demo/` synthetic demo sessions from `scripts/make-demo.mjs`
  (invented `acme/*` repos; every title starts with "Demo:").
