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

  /** The exact remedy copy that has been read and approved, per refusal.
   *
   * An allowlist, not a list of forbidden phrasings, and the reason is that
   * two successive attempts at the forbidden-phrase form both shipped a remedy
   * that named a cause nobody had reproduced. The first forbade only the
   * literal words "was deleted" and passed a remedy telling users to check the
   * component was "at the top level of the assembly" -- a condition that
   * cannot produce this refusal at all, since `_matching_occurrences` scans
   * `design.rootComponent.allOccurrences`, which traverses nested occurrences,
   * and a nested wrapper raises its own distinct refusal. The second forbade
   * "this happens when" and passed "This usually happens if the managed body
   * was suppressed in the timeline". A list of ways to phrase a diagnosis can
   * never be complete, because English always has one more.
   *
   * So the constraint is inverted: this is the copy, character for character.
   * Any edit to a remedy -- adding a sentence, softening one, or adding a new
   * entry to the table -- fails here and has to be changed in this file too,
   * which is the point. A person then reads the new sentence and decides
   * whether WG can stand behind it, instead of a regex deciding.
   */
  const APPROVED_REMEDIES: Readonly<Record<string, string>> = {
    'has no resolvable wrapper occurrence':
      'Open the linked document in Fusion and check that the WG waveguide’s own '
      + 'component is still there and is the one this design is linked to. Then send the '
      + 'design from WG again to rebuild the link, and ask for the geometry once more.',
  };

  it('ships only remedy copy that has been read and approved', () => {
    // Both directions, so neither a new table entry nor a stale approval can
    // pass unnoticed, and an empty table cannot make this vacuous.
    expect(REFUSAL_COPY.length).toBeGreaterThan(0);
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
