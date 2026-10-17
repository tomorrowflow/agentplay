/**
 * The one file in the viewer that knows an Entelligence URL.
 *
 * The README promises the viewer "loads no remote fonts or scripts" and that
 * the package makes no network calls except the optional email signup and
 * disconnect. Both stay true here: nothing in this file fetches anything. It
 * builds a string for an `<a href>` the user has to click, and `grep -rn "entelligence.ai" src/` returns this file and its test,
 * so the claim stays checkable in under a minute.
 *
 * Nothing from the session goes into a link. The source tag is a constant per
 * call site -- no repo name, no path, no identifier.
 */

export const ENTELLIGENCE_HOME = 'https://entelligence.ai/';

/** The research write-up the welcome card links to: why agentplay exists. */
export const SELF_IMPROVING_AGENTS = `${ENTELLIGENCE_HOME}research/self-improving-coding-agents`;

export type LinkSource = 'welcome';

/** The write-up, tagged with the surface it was opened from. */
export function researchUrl(src: LinkSource): string {
  return `${SELF_IMPROVING_AGENTS}?src=agentplay-viewer&from=${src}`;
}
