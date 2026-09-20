/** Presenting a refusal the CAD adapter reported, without arguing with it.
 *
 * WGLink refuses rather than invent: an operation it cannot carry out exactly
 * is declined, never approximated. That discipline is right and nothing here
 * softens it. What it leaves behind is a message written for whoever wrote the
 * check -- an entity id, an internal invariant, no remedy -- shown to someone
 * who is modelling a waveguide.
 *
 * So this is a translation table, not a judgement: the primary path says what
 * happened and what to do, and the report the adapter sent goes verbatim into
 * the disclosure beside it. A message with no entry here keeps its own words;
 * WG never invents a summary for a refusal it does not recognise, and never
 * names a cause it has not established.
 */

export interface PresentedRefusal {
  /** What happened, in the user's terms. Never empty. */
  summary: string;
  /** What to do about it, or null when WG has nothing honest to offer. */
  remedy: string | null;
  /** The adapter's own words, kept whole for the disclosure. */
  diagnostics: string;
}

export interface RefusalCopy {
  /** A phrase that identifies the refusal without matching its variable parts. */
  invariant: string;
  summary: string;
  remedy: string;
}

/** One entry per refusal whose own message leaves the user with nothing to do.
 *
 * Matched on the fixed half of the sentence, because the rest of it is an
 * instance id that differs every time.
 *
 * Exported so the tests can constrain every entry rather than the one they
 * happen to look up: a remedy that names an unestablished cause is the defect
 * this table is most likely to grow, and it has to be caught table-wide.
 */
export const REFUSAL_COPY: readonly RefusalCopy[] = [
  {
    // fusion-addins/WGLink/wglink_send.py, `_strict_assembly_from_link`:
    // `_matching_occurrences` found nothing to place this link against, and
    // the function's rule is "never a plausible default". Defaulting the
    // placement to identity would solve the geometry at the wrong position,
    // which nothing downstream could detect, so the refusal is correct.
    invariant: 'has no resolvable wrapper occurrence',
    summary: 'Fusion refused to export this waveguide: it could not find the component '
      + 'the waveguide was placed as, so it does not know where the geometry sits in the '
      + 'assembly. Nothing was exported and nothing was solved at a guessed position.',
    // No cause is named. `_matching_occurrences` scans occurrences under a
    // bare `except Exception: continue`, so an absent occurrence and an
    // unreadable one are indistinguishable from here, and neither has been
    // reproduced. An earlier draft of this remedy told the user to check the
    // component was at the top level of the assembly; that condition cannot
    // produce this refusal at all -- the scan walks
    // `design.rootComponent.allOccurrences`, which traverses nested
    // occurrences, and a nested wrapper raises its own distinct refusal
    // immediately afterwards. This says what to try, and nothing about why.
    remedy: 'Open the linked document in Fusion and check that the WG waveguide’s own '
      + 'component is still there and is the one this design is linked to. Then send the '
      + 'design from WG again to rebuild the link, and ask for the geometry once more.',
  },
];

/** What to show for one refusal, or null when the adapter reported no words. */
export function presentCadRefusal(message: string | null | undefined): PresentedRefusal | null {
  const reported = (message ?? '').trim();
  if (!reported) return null;
  const known = REFUSAL_COPY.find((entry) => reported.includes(entry.invariant));
  return known
    ? { summary: known.summary, remedy: known.remedy, diagnostics: reported }
    : { summary: reported, remedy: null, diagnostics: reported };
}

/** The one-line form, for a surface that carries a single string. */
export function refusalSentence(presented: PresentedRefusal): string {
  return presented.remedy ? `${presented.summary} ${presented.remedy}` : presented.summary;
}
