import type { ReactNode } from 'react';
import { Badge } from '@/components/ui/badge';
import type { ReviewDoc, SessionMap } from '@/core/schema';
import {
  realCommentCard,
  reviewLoopView,
  type CommentStep,
} from '@/core/reviewLoop';
import { srcSeqOf } from '@/core/sessionMapModel';
import { useSessionReviews } from '@/data/hooks';
import { cn } from '@/lib/utils';

/**
 * The review loop, in the outcomes panel: the review comments `agentplay
 * review` traced on this machine, each with the edit that caused it. A
 * session with no review docs renders `empty`, which defaults to nothing.
 */
export function ReviewLoopBlock({
  map,
  path = null,
  onPath,
  onSeek,
  empty = null,
}: {
  map: SessionMap;
  /** the picked comment's causal path, lit on the map */
  path?: readonly number[] | null;
  onPath?: (seqs: readonly number[] | null) => void;
  /** move the playhead to the flagged edit when a comment is picked */
  onSeek?: (index: number) => void;
  empty?: ReactNode;
}) {
  const view = reviewLoopView(useSessionReviews(map.session.id));
  if (view.kind === 'pending') return null;
  if (view.kind === 'none') return empty;
  // `srcSeqOf`, not `e.seq`: under a task or model scope the map is a
  // renumbered copy, so the cited number read against `seq` parks the
  // playhead on an unrelated event.
  const seekSeq = onSeek
    ? (seq: number) => {
        const i = map.events.findIndex((e) => srcSeqOf(e) === seq);
        if (i >= 0) onSeek(i);
      }
    : undefined;
  return (
    <ReviewLoopReal
      map={map}
      reviews={view.reviews}
      path={path}
      onPath={onPath}
      onSeek={seekSeq}
    />
  );
}

const samePath = (path: readonly number[] | null | undefined, steps: readonly CommentStep[]) =>
  !!path && steps.length > 0 && path.length === steps.length && path.every((s, i) => s === steps[i].seq);

/** Picking a comment lights its path and parks the playhead on its edit; picking it again clears. */
function pickHandler(
  steps: readonly CommentStep[],
  editSeq: number,
  path: readonly number[] | null | undefined,
  onPath?: (seqs: readonly number[] | null) => void,
  onSeek?: (seq: number) => void
) {
  if (!onPath) return undefined;
  return () => {
    const clearing = samePath(path, steps);
    onPath(clearing ? null : steps.map((s) => s.seq));
    if (!clearing) onSeek?.(editSeq);
  };
}

/**
 * Real comments, from the review docs `agentplay review` wrote on this
 * machine. No rule: clustering comments into rules needs every session, which
 * a local review does not have.
 */
function ReviewLoopReal({
  map,
  reviews,
  path,
  onPath,
  onSeek,
}: {
  map: SessionMap;
  reviews: readonly ReviewDoc[];
  path?: readonly number[] | null;
  onPath?: (seqs: readonly number[] | null) => void;
  onSeek?: (seq: number) => void;
}) {
  return (
    <section className="mt-3 flex shrink-0 flex-col gap-3 rounded-lg border border-ds-border-subtlest bg-ds-surface-white p-3">
      {reviews.map((r) => (
        <div key={`${r.pr.repo}#${r.pr.number}`} className="flex flex-col gap-3">
          <p className="ds-text-body-xs-semibold text-ds-text-subtle">
            {r.comments.length} review{' '}
            {r.comments.length === 1 ? 'comment' : 'comments'} on{' '}
            <a href={r.pr.url} target="_blank" rel="noopener noreferrer" className="underline">
              {r.pr.repo} #{r.pr.number}
            </a>
            {onPath && !r.stale ? ' \u00b7 pick one to light its path on the map' : null}
          </p>
          {r.stale ? (
            <p className="ds-text-body-xs-medium text-ds-text-subtle">
              Map rebuilt since this review; rerun agentplay review to light its paths.
            </p>
          ) : null}
          {r.comments.map((raw) => {
            const c = realCommentCard(raw, map, r.stale);
            return (
              <ReviewCommentCard
                key={c.id}
                file={c.file}
                line={c.line}
                author={c.author}
                text={c.text}
                steps={c.path}
                picked={samePath(path, c.path)}
                onPick={
                  c.editSeq !== null && c.path.length
                    ? pickHandler(c.path, c.editSeq, path, onPath, onSeek)
                    : undefined
                }
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant={c.editSeq === null && !c.otherSession ? 'secondary' : 'error'}>
                    {c.badge}
                  </Badge>
                  {c.otherSession ? (
                    <span className="font-mono ds-text-body-xs-medium text-ds-text-subtle">
                      in session {c.otherSession.slice(-12)}
                    </span>
                  ) : null}
                </div>
              </ReviewCommentCard>
            );
          })}
        </div>
      ))}
    </section>
  );
}

/**
 * One comment. Picking it lights its causal path on the map
 * and dims the rest -- the same focus mechanism the dock's category chips
 * use, so the path is drawn by the map rather than painted over it. Picking
 * it again clears the focus. `children` sits between the comment and its
 * path.
 */
function ReviewCommentCard({
  file,
  line,
  author,
  text,
  steps,
  picked,
  onPick,
  children,
}: {
  file: string;
  line: string;
  author: string | null;
  text: string;
  steps: readonly CommentStep[];
  picked: boolean;
  onPick?: () => void;
  children?: ReactNode;
}) {
  const body = (
    <>
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate font-mono ds-text-body-xs-medium text-ds-danger-default">
          {file} {line}
        </span>
        {author ? (
          <span className="shrink-0 ds-text-body-xs-medium text-ds-text-subtle">
            @{author}
          </span>
        ) : null}
      </div>
      <p className="ds-text-body-xs-medium text-ds-text-default">“{text}”</p>
      {children}
      {steps.length ? (
        <div className="flex flex-col gap-0.5">
          <span className="ds-text-body-xs-semibold text-ds-text-subtle">
            Causal path
          </span>
          {steps.map((s) => (
            <div key={s.seq} className="flex items-baseline gap-2">
              <span className="w-8 shrink-0 font-mono ds-text-body-xs-medium text-ds-text-subtle">
                #{s.seq}
              </span>
              <span className="w-8 shrink-0 ds-text-body-xs-medium text-ds-text-subtle">
                {s.who}
              </span>
              <span className="min-w-0 flex-1 font-mono ds-text-body-xs-medium text-ds-text-default">
                {s.what}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </>
  );
  return (
    <div
      className={cn(
        'flex w-full flex-col gap-1.5 rounded-md border p-2 text-left transition-colors',
        picked
          ? 'border-ds-brand-subtler bg-ds-brand-lightest'
          : 'border-ds-border-subtlest'
      )}
    >
      {onPick ? (
        <button
          type="button"
          aria-pressed={picked}
          aria-label={`Light the path to ${file} ${line} on the map`}
          onClick={onPick}
          className="-m-1 flex cursor-pointer flex-col gap-1.5 rounded p-1 text-left transition-colors hover:bg-ds-gray-subtlest focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ds-brand-subtle focus-visible:ring-offset-1 focus-visible:ring-offset-ds-surface-white"
        >
          {body}
        </button>
      ) : (
        <div className="flex flex-col gap-1.5">{body}</div>
      )}
    </div>
  );
}
