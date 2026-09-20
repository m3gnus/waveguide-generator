import { describe, expect, it } from 'vitest';
import { REFUSAL_COPY, presentCadRefusal, refusalSentence } from './refusals';

// The verbatim refusal a real Fusion session produced, from
// `_strict_assembly_from_link` in the WGLink add-in. The refusal itself is
// correct -- defaulting the placement to identity would solve the geometry at
// the wrong position -- so nothing here argues with it. What is presented is.
const WRAPPER_REFUSAL = "WGLink instance '393aaad4-9e78-462d-ad6c-126d411fbefd' "
  + 'has no resolvable wrapper occurrence; placement was not defaulted to identity.';

describe('presentCadRefusal', () => {
  it('keeps the opaque identity and the internal invariant out of the primary path', () => {
    const presented = presentCadRefusal(WRAPPER_REFUSAL);
    expect(presented).not.toBeNull();
    expect(presented!.summary).not.toContain('393aaad4');
    expect(presented!.summary).not.toContain('wrapper occurrence');
    expect(presented!.summary).not.toContain('defaulted to identity');
    // A modeller has to learn that Fusion refused and that nothing was solved
    // at a guessed position; both belong in the sentence they are shown.
    expect(presented!.summary.toLowerCase()).toContain('fusion');
    expect(presented!.summary.toLowerCase()).toContain('refused');
  });

  it('offers a remedy the modeller can carry out', () => {
    const presented = presentCadRefusal(WRAPPER_REFUSAL)!;
    expect(presented.remedy).not.toBeNull();
    expect(presented.remedy!.toLowerCase()).toContain('send');
  });

  /** The exact sentence the user reads, per refusal: summary and remedy as
   * `refusalSentence` composes them.
   *
   * An allowlist, not a list of forbidden phrasings, because three successive
   * attempts at a narrower constraint each shipped a false cause through the
   * part the constraint did not cover. A list forbidding "was deleted" passed
   * a remedy saying the component must be "at the top level of the assembly"
   * -- a condition that cannot produce this refusal at all, since
   * `_matching_occurrences` scans `design.rootComponent.allOccurrences`, which
   * traverses nested occurrences, and a nested wrapper raises its own distinct
   * refusal. A longer list, forbidding "this happens when", passed "This
   * usually happens if the managed body was suppressed in the timeline". And
   * an allowlist over `remedy` alone passed "This happens when the wrapper
   * component was deleted from the assembly" -- in `summary`, which the user
   * reads in the same breath.
   *
   * So what is pinned is the composed, user-visible sentence rather than any
   * one field: whatever fields the table grows, the thing a person is shown is
   * the thing that has been approved, character for character. Any edit fails
   * here and has to be made in this file too, which is the point -- a person
   * then reads the new sentence and decides whether WG can stand behind it,
   * instead of a regex deciding.
   */
  const APPROVED_SENTENCES: Readonly<Record<string, string>> = {
    'has no resolvable wrapper occurrence':
      'Fusion refused to export this waveguide: it could not find the component '
      + 'the waveguide was placed as, so it does not know where the geometry sits in the '
      + 'assembly. Nothing was exported and nothing was solved at a guessed position. '
      + 'Open the linked document in Fusion and check that the WG waveguide’s own '
      + 'component is still there and is the one this design is linked to. Then send the '
      + 'design from WG again to rebuild the link, and ask for the geometry once more.',
  };

  /** The remedy half on its own, so the per-field check below stays useful. */
  const APPROVED_REMEDIES: Readonly<Record<string, string>> = {
    'has no resolvable wrapper occurrence':
      'Open the linked document in Fusion and check that the WG waveguide’s own '
      + 'component is still there and is the one this design is linked to. Then send the '
      + 'design from WG again to rebuild the link, and ask for the geometry once more.',
  };

  it('shows only the composed sentence that has been read and approved', () => {
    // The authoritative check: whatever `presentCadRefusal` fills in and
    // `refusalSentence` joins, this is what reaches the user. An entry's own
    // invariant is a message that matches it, so this covers every row without
    // needing a sample refusal written for each.
    expect(REFUSAL_COPY.length).toBeGreaterThan(0);
    expect(REFUSAL_COPY.map((entry) => entry.invariant).sort())
      .toEqual(Object.keys(APPROVED_SENTENCES).sort());
    for (const entry of REFUSAL_COPY) {
      const presented = presentCadRefusal(entry.invariant);
      expect(presented).not.toBeNull();
      expect(refusalSentence(presented!)).toBe(APPROVED_SENTENCES[entry.invariant]);
    }
  });

  it('ships only remedy copy that has been read and approved', () => {
    // Kept alongside the composed check: it says which half a diff changed.
    expect(REFUSAL_COPY.map((entry) => entry.invariant).sort())
      .toEqual(Object.keys(APPROVED_REMEDIES).sort());
    for (const entry of REFUSAL_COPY) {
      expect(entry.remedy).toBe(APPROVED_REMEDIES[entry.invariant]);
    }
  });

  it('presents exactly the approved remedy, not merely an equivalent one', () => {
    // The table is one hop from the user; this is the other. A transform that
    // decorated the remedy on the way out would satisfy the test above while
    // still putting unapproved words on screen.
    const presented = presentCadRefusal(WRAPPER_REFUSAL)!;
    expect(presented.remedy).toBe(APPROVED_REMEDIES['has no resolvable wrapper occurrence']);
  });

  it('preserves the report verbatim for the disclosure', () => {
    const presented = presentCadRefusal(WRAPPER_REFUSAL)!;
    expect(presented.diagnostics).toBe(WRAPPER_REFUSAL);
    // The identity the add-in named is evidence, not noise: it is how a second
    // report is told from a repeat of the first one.
    expect(presented.diagnostics).toContain('393aaad4-9e78-462d-ad6c-126d411fbefd');
  });

  it('never invents a summary or a remedy for a refusal it does not recognise', () => {
    const other = 'WGLink refused something nobody has written copy for yet.';
    const presented = presentCadRefusal(other)!;
    expect(presented.summary).toBe(other);
    expect(presented.remedy).toBeNull();
    expect(presented.diagnostics).toBe(other);
  });

  it('has nothing to present when Fusion reported no message', () => {
    expect(presentCadRefusal(null)).toBeNull();
    expect(presentCadRefusal('')).toBeNull();
    expect(presentCadRefusal('   ')).toBeNull();
  });

  it('reads as one sentence, with the remedy only when there is one', () => {
    expect(refusalSentence(presentCadRefusal(WRAPPER_REFUSAL)!))
      .toBe(`${presentCadRefusal(WRAPPER_REFUSAL)!.summary} ${presentCadRefusal(WRAPPER_REFUSAL)!.remedy}`);
    const plain = presentCadRefusal('Refused.')!;
    expect(refusalSentence(plain)).toBe('Refused.');
  });
});
