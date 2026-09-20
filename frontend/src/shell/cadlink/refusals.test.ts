import { describe, expect, it } from 'vitest';
import { presentCadRefusal, refusalSentence } from './refusals';

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

  it('offers a remedy the modeller can carry out, and asserts no cause', () => {
    const presented = presentCadRefusal(WRAPPER_REFUSAL)!;
    expect(presented.remedy).not.toBeNull();
    expect(presented.remedy!.toLowerCase()).toContain('send');
    // `_matching_occurrences` scans occurrences under a bare
    // `except Exception: continue`, so an unreadable occurrence and an absent
    // one are indistinguishable from here. WG must not name one of them.
    expect(presented.remedy!.toLowerCase()).not.toContain('was deleted');
    expect(presented.remedy!.toLowerCase()).not.toContain('has been deleted');
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
