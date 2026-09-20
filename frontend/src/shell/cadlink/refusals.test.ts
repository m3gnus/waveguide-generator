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

  /** Phrases that would state a cause, for a refusal that has none established.
   *
   * The first version of this test forbade only the literal words "was
   * deleted", and passed a remedy that told users to check the component was
   * "at the top level of the assembly" -- a condition that cannot produce this
   * refusal at all: `_matching_occurrences` scans
   * `design.rootComponent.allOccurrences`, which traverses nested occurrences,
   * and a nested wrapper raises its own distinct refusal
   * (`wglink_send.py`, "Move the wrapper to the root level and send again").
   * So the list is by mechanism, not by the one phrase that got through:
   * placement, deletion, renaming, and any word that frames a step as the
   * explanation rather than as something to try.
   */
  const CAUSAL_PHRASES = [
    'top level', 'top-level', 'root level', 'root-level', 'nested', 'inside another',
    'was deleted', 'has been deleted', 'were deleted', 'you deleted',
    'was renamed', 'has been renamed', 'you renamed', 'was moved', 'you moved',
    'because', 'this happens when', 'this occurs when', 'the cause', 'caused by',
    'which means', 'means that', 'due to',
  ];

  it('asserts no cause in any remedy, for any refusal in the table', () => {
    // Every entry, not just the one a reviewer happens to look at: a remedy
    // added later must not name a cause either. An empty table would make
    // this vacuous, so the table has to have entries for it to constrain.
    expect(REFUSAL_COPY.length).toBeGreaterThan(0);
    for (const entry of REFUSAL_COPY) {
      const remedy = entry.remedy.toLowerCase();
      for (const phrase of CAUSAL_PHRASES) {
        expect(`${entry.invariant} :: ${phrase} :: ${remedy.includes(phrase)}`)
          .toBe(`${entry.invariant} :: ${phrase} :: false`);
      }
    }
  });

  it('checks the phrase list would actually catch a remedy that names a cause', () => {
    // A forbidden-phrase list that matches nothing proves nothing. This is the
    // control: the exact remedy this test was written against once shipped,
    // and every phrase below is one it or a plausible successor contains.
    const rejected = 'Check that the component is still present, at the top level of the '
      + 'assembly, because the wrapper was deleted or was renamed.';
    expect(CAUSAL_PHRASES.filter((phrase) => rejected.includes(phrase)))
      .toEqual(expect.arrayContaining(['top level', 'because', 'was deleted', 'was renamed']));
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
