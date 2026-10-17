import type { ReactNode } from 'react';
import {
  ArrowSquareOut,
  CaretDown,
  GitPullRequest,
} from '@phosphor-icons/react';
import type {
  SessionExplorerSession,
  SessionMap,
} from '@/core/schema';
import { GitHubMark } from '@/assets/GitHubMark';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { agentToolLabel, sessionTitle } from './SessionList';
import { prCountLabel, prMenuItems } from '@/core/sessionLayout';
import { clockAt, dayLabel, outcomeMeta } from '@/core/sessionMapModel';

interface Props {
  /** the replayed (scoped + lensed) map */
  map: SessionMap;
  row: SessionExplorerSession | null;
  /** the open subagent's label (agent lens), or null on the main session */
  lensLabel: string | null;
  onExitLens: () => void;
  /** the Task / Agent / Model filters, on the same line at the right */
  filters?: ReactNode;
}

/**
 * The compact session header (fit-viewport layout): the title, tool, outcome
 * pill and PR menu, with the Task / Agent / Model filters at the right of
 * the same line (the separate toolbar row is gone, so the map gets its
 * height); the session id and start time live in the title's tooltip. Cost/tokens/duration and coverage
 * already live in Details (`SessionSummary`/`TaskDetail`, below Outcome);
 * models, errors/retries/delays/compactions and coverage already live in
 * the sidebar's Insights tab -- keeping them here too was pure duplication
 * that cost the map its vertical space, so this header carries only what
 * isn't shown anywhere else.
 */
export function SessionHeader(props: Props) {
  const { map, row, lensLabel } = props;
  const s = map.session;
  const title =
    lensLabel ??
    (row ? sessionTitle(row) : `${agentToolLabel(s.agentTool)} session`);
  const stamp = `${(s.id.split(':').pop() ?? s.id).slice(0, 8)} · ${dayLabel(s.startedAt)} ${clockAt(s.startedAt, 0).slice(0, 5)} UTC`;
  return (
    <header className="flex min-w-0 shrink-0 flex-wrap items-center gap-x-3 gap-y-1">
      <div className="flex min-w-[240px] flex-1 items-center gap-2">
        {lensLabel ? <LensCrumb onExit={props.onExitLens} /> : null}
        <h2
          className="min-w-0 truncate ds-text-body-sm-semibold text-ds-text-darker"
          title={`${title}\n${stamp}`}
        >
          {title}
        </h2>
        <Badge variant="secondary" className="shrink-0">
          {agentToolLabel(s.agentTool)}
        </Badge>
        {s.outcome ? <OutcomePill outcome={s.outcome} /> : null}
        <PrMenu prs={map.prs} />
      </div>
      {props.filters}
      {/* The id / start time is also in the title's tooltip; shown inline
          only where the row has room for it next to the filters. */}
      <span className="hidden shrink-0 font-mono ds-text-body-xs-medium text-ds-text-subtle 2xl:inline">
        {stamp}
      </span>
    </header>
  );
}

function LensCrumb({ onExit }: { onExit: () => void }) {
  return (
    <nav
      aria-label="Breadcrumb"
      className="flex shrink-0 items-center gap-1.5 ds-text-body-xs-medium text-ds-text-subtle"
    >
      <Button variant="outline" size="sm" onClick={onExit} className="h-6 px-2">
        ← Session
      </Button>
      <span aria-hidden>›</span>
    </nav>
  );
}

function OutcomePill({ outcome }: { outcome: string }) {
  const meta = outcomeMeta(outcome);
  if (meta.variant === 'success') {
    return (
      <Badge variant="success">{meta.label}</Badge>
    );
  }
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}

/** One "N PRs" button; the PRs themselves are listed in its popover. */
function PrMenu({ prs }: { prs: SessionMap['prs'] }) {
  const items = prMenuItems(prs);
  if (!items.length) return null;
  const label = prCountLabel(items.length);
  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button
            variant="outline"
            size="sm"
            className="h-6 gap-1 px-2 font-mono"
            aria-label={`${label} in this session`}
          >
            <GitPullRequest size={12} aria-hidden />
            {label}
            <CaretDown size={10} aria-hidden />
          </Button>
        }
      />
      <PopoverContent align="start" className="w-72 p-1.5">
        <ul
          aria-label="Pull requests"
          className="flex max-h-72 flex-col overflow-y-auto"
        >
          {items.map((pr) => {
            const body = (
              <>
                {pr.provider === 'github' ? (
                  <GitHubMark className="size-3 shrink-0 text-ds-text-subtle" />
                ) : null}
                <span
                  className="min-w-0 flex-1 truncate font-mono"
                  title={pr.label}
                >
                  {pr.provider === 'github'
                    ? pr.label
                    : `${pr.provider} ${pr.label}`}
                </span>
              </>
            );
            const row =
              'flex items-center gap-2 rounded-md px-3 py-1.5 ds-text-body-xs-medium text-ds-text-default';
            return (
              <li key={pr.key}>
                {/* `url` is null unless it is http(s), so a crafted map can't
                 * plant a javascript: link. */}
                {pr.url ? (
                  <a
                    href={pr.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={`${row} hover:bg-ds-gray-subtlest focus-visible:bg-ds-gray-subtlest focus-visible:outline-none`}
                  >
                    {body}
                    <ArrowSquareOut
                      size={12}
                      aria-hidden
                      className="shrink-0 text-ds-text-subtle"
                    />
                  </a>
                ) : (
                  <div className={row}>{body}</div>
                )}
              </li>
            );
          })}
        </ul>
      </PopoverContent>
    </Popover>
  );
}
