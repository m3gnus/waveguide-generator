import { describe, expect, it } from 'vitest';
import { findingName, findingSentence } from './cadFindingWords';

describe('finding words', () => {
  it('leaves what a symmetry fallback solves to the server’s own sentence', () => {
    const detail = 'WG will solve the half model as cut in CAD; it could not use any further symmetry for this one.';
    const finding = { id: 'f', kind: 'symmetry-cut-unverified', blocking: false, solved_domain: 'half', detail } as never;
    expect(findingSentence(finding)).toBe(detail);
    expect(findingName(finding)).not.toContain('whole');
  });
  it('keeps the server’s reason for a model WG cannot judge', () => {
    const finding = { id: 'f', kind: 'stale-detection-unavailable', blocking: false, reason: 'no_document_signature' } as never;
    expect(findingName(finding)).toBe('WG cannot tell whether this model is out of date');
    expect(findingSentence(finding)).toBe('no document signature');
  });
});
