import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { parseAsStringEnum, useQueryState, useQueryStates, parseAsString } from 'nuqs';
import {
  FileArrowUp,
  Moon,
  Sun,
  X,
} from '@phosphor-icons/react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { SessionExplorer } from '@/explorer/SessionExplorer';
import { SessionExplorerSkeleton } from '@/explorer/SessionExplorerStates';
import { WINDOW_KEYS, type WindowKey } from '@/core/windowOptions';
import { SessionMapSourceProvider } from '@/data/SourceContext';
import { demoSource } from '@/data/demoSource';
import { fileSource, type OpenResult } from '@/data/fileSource';
import { httpSource, probeServer } from '@/data/httpSource';
import type { SessionMapSource } from '@/data/source';
import { useSessionExplorerStatus } from '@/data/hooks';
import { useTheme } from '@/app/theme';
import { useFileDrop } from '@/app/useFileDrop';

type Mode = 'probing' | 'server' | 'demo';

/** `?server=http://host:port` points the viewer at a server on another
 * origin (e.g. `vite dev` against a running CLI); default is same-origin. */
function serverBase(): string {
  const p = new URLSearchParams(window.location.search).get('server');
  return p && /^https?:\/\//.test(p) ? p : '';
}

/** Local server if `/api/status` answers, else the bundled demo sessions.
 * `?demo` forces the demo. */
function useProbedMode(base: string): Mode {
  const [mode, setMode] = useState<Mode>('probing');
  useEffect(() => {
    let live = true;
    void probeServer(base).then((ok) => {
      if (live) setMode(ok ? 'server' : 'demo');
    });
    return () => {
      live = false;
    };
  }, [base]);
  return mode;
}

export function App() {
  const base = useMemo(serverBase, []);
  const probed = useProbedMode(base);
  const server = useMemo(() => httpSource(base), [base]);
  const demo = useMemo(() => demoSource(), []);
  const files = useMemo(() => fileSource(), []);
  // Opened files take over the view; "Back" returns to the base source.
  const [showFiles, setShowFiles] = useState(false);
  // bumped when files are added: `files.id` changes, this re-renders onto it
  const [, setFilesGen] = useState(0);
  const [notice, setNotice] = useState<OpenResult | null>(null);
  const [forceDemo] = useQueryState('demo', parseAsString);
  const [, setSelection] = useQueryStates({
    session: parseAsString,
    view: parseAsString,
  });
  const mode: Mode = forceDemo !== null ? 'demo' : probed;
  const [windowKey, setWindowKey] = useQueryState(
    'win',
    parseAsStringEnum<WindowKey>([...WINDOW_KEYS]).withDefault('all')
  );
  const inputRef = useRef<HTMLInputElement>(null);

  const openFiles = useCallback(
    async (list: File[]) => {
      const result = await files.addFiles(list);
      setNotice(result.failed.length || !result.added.length ? result : null);
      if (result.added.length) {
        setFilesGen((g) => g + 1);
        setShowFiles(true);
        void setSelection({ session: result.added[0], view: null });
      }
    },
    [files, setSelection]
  );
  const dragging = useFileDrop((f) => void openFiles(f));

  const baseSource = mode === 'server' ? server : demo;
  const openedFiles = showFiles && files.size > 0;
  const source: SessionMapSource = openedFiles ? files : baseSource;
  const picker = (
    <>
      <input
        ref={inputRef}
        type="file"
        accept=".json,.gz,application/json,application/gzip"
        multiple
        hidden
        onChange={(e) => {
          const list = Array.from(e.target.files ?? []);
          e.target.value = '';
          if (list.length) void openFiles(list);
        }}
      />
      <Button variant="outline" size="sm" className="gap-1.5" onClick={() => inputRef.current?.click()}>
        <FileArrowUp size={14} aria-hidden />
        Open map file
      </Button>
    </>
  );

  return (
    <div className="flex h-screen min-h-[700px] w-full flex-col gap-3 px-6 py-3">
      <SessionMapSourceProvider source={source}>
        <AppBar
          mode={mode}
          source={source}
          picker={picker}
          onBack={openedFiles ? () => setShowFiles(false) : undefined}
          baseLabel={baseSource.label}
        />
        {mode === 'demo' && source === demo ? (
          <Banner>
            Demo sessions. Run <code className="font-mono">uvx agentplay</code> (or{' '}
            <code className="font-mono">pipx run agentplay</code>) to see your own, or drop a map
            file anywhere on this page.
          </Banner>
        ) : null}
        {notice ? <OpenNotice result={notice} onClose={() => setNotice(null)} /> : null}
        {mode === 'probing' ? (
          <SessionExplorerSkeleton />
        ) : (
          <SessionExplorer
            key={`explorer:${source.id}`}
            windowKey={windowKey}
            onWindowChange={(k) => void setWindowKey(k === 'all' ? null : k)}
            emptyAction={picker}
          />
        )}
      </SessionMapSourceProvider>
      {dragging ? <DropOverlay /> : null}
    </div>
  );
}

function AppBar({
  mode,
  source,
  picker,
  onBack,
  baseLabel,
}: {
  mode: Mode;
  source: SessionMapSource;
  picker: ReactNode;
  onBack?: () => void;
  baseLabel: string;
}) {
  const { theme, toggle } = useTheme();
  const status = useSessionExplorerStatus();
  const count = mode === 'probing' ? null : (status.data?.mapCount ?? null);
  return (
    <header className="flex shrink-0 items-center gap-3">
      <div className="flex items-center gap-2">
        <img src="entelligence-logo.svg" alt="" className="size-7" />
        <span className="ds-text-body-sm-semibold text-ds-text-darker">agentplay</span>
      </div>
      {mode !== 'probing' ? (
        <Badge variant="keyline">
          {source.label}
          {count != null ? ` · ${count} ${count === 1 ? 'map' : 'maps'}` : ''}
        </Badge>
      ) : null}
      {onBack ? (
        <Button variant="ghost" size="sm" className="h-7 px-2 text-ds-text-subtle" onClick={onBack}>
          Back to {baseLabel.toLowerCase()}
        </Button>
      ) : null}
      <div className="ml-auto flex items-center gap-2">
        {picker}
        <Button
          variant="ghost"
          size="iconSm"
          aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
          onClick={toggle}
        >
          {theme === 'dark' ? <Sun size={16} aria-hidden /> : <Moon size={16} aria-hidden />}
        </Button>
      </div>
    </header>
  );
}

function Banner({ children }: { children: ReactNode }) {
  return (
    <div
      role="note"
      className="shrink-0 rounded-lg border border-ds-brand-subtlest bg-ds-brand-lightest px-3 py-2 ds-text-body-xs-medium text-ds-text-default"
    >
      {children}
    </div>
  );
}

function OpenNotice({ result, onClose }: { result: OpenResult; onClose: () => void }) {
  const failed = result.failed;
  return (
    <div
      role="alert"
      className="flex shrink-0 items-start gap-2 rounded-lg border border-ds-danger-subtler bg-ds-danger-subtlest px-3 py-2 ds-text-body-xs-medium text-ds-danger-darkest"
    >
      <div className="flex-1">
        {failed.length
          ? `${failed.length === 1 ? 'One file' : `${failed.length} files`} could not be opened: ${failed
              .map((f) => `${f.name} (${f.reason})`)
              .join(', ')}.`
          : 'No session maps found in those files.'}
      </div>
      <button type="button" aria-label="Dismiss" onClick={onClose} className="shrink-0">
        <X size={14} aria-hidden />
      </button>
    </div>
  );
}

function DropOverlay() {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-3 z-50 flex items-center justify-center rounded-2xl border-2 border-dashed border-ds-brand-subtler bg-ds-surface-white/80 backdrop-blur-sm"
    >
      <div className="flex flex-col items-center gap-2">
        <FileArrowUp size={28} className="text-ds-brand-subtle" aria-hidden />
        <p className="ds-text-body-sm-semibold text-ds-text-darker">Drop session maps to open them</p>
        <p className="ds-text-body-xs-medium text-ds-text-subtle">
          .json or .json.gz, read in this tab only
        </p>
      </div>
    </div>
  );
}
