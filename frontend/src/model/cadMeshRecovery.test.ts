import { describe, expect, it } from 'vitest';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { cadMeshRecoveryNotice } from './cadMeshRecovery';

describe('mesh recovery public copy', () => {
  it.each([
    [['symmetry-cut-unverified'], 'Solved the whole model; WG could not use symmetry for this one.'],
    [['healing-performed'], 'Solved after WG repaired the mesh for this model.'],
    [['symmetry-cut-unverified', 'healing-performed'], 'Solved the whole model; WG repaired the mesh and could not use symmetry for this one.'],
  ])('gives one line for %j with no internal evidence', (kinds, expected) => {
    const record = { findings: (kinds as string[]).map(kind => ({
      id: `finding-${kind}-raw`, kind, blocking: false, reason: 'internal failure detail',
    })) } as CadReturnIngestRecord;
    expect(cadMeshRecoveryNotice(record, true)).toBe(expected);
    expect(cadMeshRecoveryNotice(record, false)).not.toContain('Solved');
    expect(cadMeshRecoveryNotice(record, false)).not.toContain('internal');
  });
  it('words a fallback from the domain actually solved', () => {
    const record = { findings: [{
      id: 'finding-fallback', kind: 'symmetry-cut-unverified', blocking: false, solved_domain: 'half',
    }] } as unknown as CadReturnIngestRecord;
    expect(cadMeshRecoveryNotice(record, true)).toBe(
      'Solved the half model as cut in CAD; WG could not use any further symmetry for this one.');
    expect(cadMeshRecoveryNotice(record, false)).not.toContain('whole');
  });
  it('says nothing when recovery was not needed', () => {
    expect(cadMeshRecoveryNotice(null, true)).toBeNull();
    expect(cadMeshRecoveryNotice({ findings: [] } as unknown as CadReturnIngestRecord, true)).toBeNull();
  });
});
