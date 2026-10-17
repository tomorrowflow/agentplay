import { ArrowRight } from '@phosphor-icons/react';
import { parseAsString, useQueryState } from 'nuqs';
import { Badge } from '@/components/ui/badge';
import { buttonVariants } from '@/components/ui/button';
import { LockedBlock } from '@/components/LockedBlock';
import { FixNowDialog } from './FixNowDialog';
import { SAMPLE_SESSION_ID, useSampleMode } from '@/app/sampleMode';
import { reviewLoopCopy, type LinkSource } from '@/core/entelligence';
import {
  SAMPLE_COMMENTS,
  type SampleComment as SampleCommentData,
} from './reviewSample';
import type { ReactNode } from 'react';
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
 * The review loop, in the outcomes panel: a review comment on a PR, the edit
 * that caused it, and the rule the repeats cluster into.
 *
 * When `agentplay review` has traced this session's PR comments on this
 * machine, they show here, unlocked. Otherwise the block gates a capability
 * the map alone cannot have: review comment text needs the host's API, and
 * attributing one to an edit needs the comment plus the session's local edit
 * evidence. The cluster needs every other session too, which stays locked.
 */
export function ReviewLoopBlock({
  map,
  src = 'outcomes',
  path = null,
  onPath,
  onSeek,
}: {
  map: SessionMap;
  src?: LinkSource;
  /** the picked comment's causal path, lit on the map */
  path?: readonly number[] | null;
  onPath?: (seqs: readonly number[] | null) => void;
  /** move the playhead to the flagged edit when a comment is picked */
  onSeek?: (index: number) => void;
}) {
  const copy = reviewLoopCopy(map.prs);
  const [sample, setSample] = useQueryState('sample', parseAsString);
  const mode = useSampleMode();
  const view = reviewLoopView(useSessionReviews(map.session.id));
  // `srcSeqOf`, not `e.seq`: under a task or model scope the map is a
  // renumbered copy, so the cited number read against `seq` parks the
  // playhead on an unrelated event.
  const seekSeq = onSeek
    ? (seq: number) => {
        const i = map.events.findIndex((e) => srcSeqOf(e) === seq);
        if (i >= 0) onSeek(i);
      }
    : undefined;
  if (view.kind === 'pending') return null;
  if (view.kind === 'real') {
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
  // Revealed only on the session the comments are about. `sample` survives
  // picking another session out of the sidebar, and the steps they cite
  // exist in that map too -- they would light the wrong nodes in it.
  const shown = sample !== null && map.session.id === SAMPLE_SESSION_ID;
  return (
    <LockedBlock
      title={copy.title}
      blurb={copy.blurb}
      ctaLabel="Connect your PRs"
      src={src}
      preview={
        <ReviewLoopSample path={path} onPath={onPath} onSeek={seekSeq} />
      }
      shown={shown}
      // Showing the sample swaps the whole view onto the bundled session
      // that opened a PR, so the map, the timeline and the panels are all
      // the same session the comment below belongs to. Hiding it only
      // re-blurs: leaving the sample is a separate, named action, because
      // doing it silently would throw away a session the user had open.
      onToggle={() => (shown ? void setSample(null) : mode.show())}
      exit={
        mode.active && mode.canExit ? (
          <button
            type="button"
            onClick={mode.exit}
            className={cn(buttonVariants({ variant: 'link', size: 'sm' }))}
          >
            Back to your sessions
          </button>
        ) : null
      }
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
 * machine. No rule or "Fix now": clustering comments into rules needs every
 * session, which a local review does not have.
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
            {onPath ? ' \u00b7 pick one to light its path on the map' : null}
          </p>
          {r.comments.map((raw) => {
            const c = realCommentCard(raw, map);
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
 * Invented, and labelled as such by `LockedBlock` -- but every step number,
 * file and task below is a real one from the demo session the "See sample"
 * button switches to, so a reader can scrub the timeline and find each one.
 * Shaped like the real panel: comment, cause, causal path, cluster.
 */
function ReviewLoopSample({
  path,
  onPath,
  onSeek,
}: {
  path?: readonly number[] | null;
  onPath?: (seqs: readonly number[] | null) => void;
  onSeek?: (seq: number) => void;
}) {
  return (
    <div className="flex flex-col gap-3">
      <p className="ds-text-body-xs-semibold text-ds-text-subtle">
        {SAMPLE_COMMENTS.length} review comments on acme/api #142
        {onPath ? ' \u00b7 pick one to light its path on the map' : null}
      </p>
      {SAMPLE_COMMENTS.map((c) => (
        <ReviewCommentCard
          key={c.editSeq}
          file={c.file}
          line={c.line}
          author={c.author}
          text={c.text}
          steps={c.path}
          picked={samePath(path, c.path)}
          onPick={pickHandler(c.path, c.editSeq, path, onPath, onSeek)}
          after={<SampleTail c={c} />}
          footer={<FixNowDialog c={c} />}
        >
          <SampleCause c={c} />
        </ReviewCommentCard>
      ))}
    </div>
  );
}

/** What the detector flagged, the edit, the task and the cause: sample-only detail. */
function SampleCause({ c }: { c: SampleCommentData }) {
  return (
    <>
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="error">{c.flagged}</Badge>
        <span className="font-mono ds-text-body-xs-medium text-ds-text-subtle">
          edit #{c.editSeq}
        </span>
      </div>
      <p className="ds-text-body-xs-medium text-ds-text-subtle">
        Task: {c.task}
      </p>
      <div className="flex flex-col gap-0.5">
        <span className="ds-text-body-xs-semibold text-ds-text-subtle">
          Cause · {c.cause}
        </span>
        <span className="ds-text-body-xs-medium text-ds-text-default">
          {c.why}
        </span>
      </div>
    </>
  );
}

/** The never-opened file and the cluster the sample comment lands in. */
function SampleTail({ c }: { c: SampleCommentData }) {
  return (
    <>
      {c.ghost ? (
        <p className="ds-text-body-xs-medium text-ds-text-subtle">
          Ghost: {c.ghost}
        </p>
      ) : null}
      <div className="flex items-center gap-1 ds-text-body-xs-medium text-ds-brand-default">
        <span>
          Clustered into {c.learning} with {c.alsoIn}{' '}
          {c.alsoIn === 1 ? 'other session' : 'other sessions'}
        </span>
        <ArrowRight size={12} aria-hidden className="shrink-0" />
      </div>
    </>
  );
}

/**
 * One comment, real or sample. Picking it lights its causal path on the map
 * and dims the rest -- the same focus mechanism the dock's category chips
 * use, so the path is drawn by the map rather than painted over it. Picking
 * it again clears the focus. `children` sits between the comment and its
 * path, `after` below the path, `footer` in its own row.
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
  after,
  footer,
}: {
  file: string;
  line: string;
  author: string | null;
  text: string;
  steps: readonly CommentStep[];
  picked: boolean;
  onPick?: () => void;
  children?: ReactNode;
  after?: ReactNode;
  footer?: ReactNode;
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
      {after}
    </>
  );
  // The card is a div, not a button: "Fix now" lives inside it, and a
  // button inside a button is invalid and unreachable by keyboard. The
  // comment itself is the pick target; the footer is its own row.
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
          className="-m-1 flex cursor-pointer flex-col gap-1.5 rounded p-1 text-left transition-colors hover:bg-ds-gray-subtlest focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ds-brand-subtler"
        >
          {body}
        </button>
      ) : (
        <div className="flex flex-col gap-1.5">{body}</div>
      )}
      {footer ? (
        <div className="flex items-center gap-2 border-t border-ds-border-subtlest pt-1.5">
          {footer}
        </div>
      ) : null}
    </div>
  );
}
