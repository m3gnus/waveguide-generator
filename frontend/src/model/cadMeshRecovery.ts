import type { CadReturnFinding, CadReturnIngestRecord } from '../api/cadlink';

/** Recovery evidence belongs in diagnostics; the viewport owns its public line. */
export function isMeshRecoveryFinding(finding: CadReturnFinding): boolean {
  return finding.kind === 'symmetry-cut-unverified' || finding.kind === 'healing-performed';
}

export function cadMeshRecoveryNotice(record: CadReturnIngestRecord | null, solved: boolean): string | null {
  const recovered = record?.findings?.filter(isMeshRecoveryFinding) ?? [];
  if (!recovered.length) return null;
  const fallback = recovered.some(finding => finding.kind === 'symmetry-cut-unverified');
  const healed = recovered.some(finding => finding.kind === 'healing-performed');
  if (fallback) return solved
    ? `Solved the whole model; WG ${healed ? 'repaired the mesh and ' : ''}could not use symmetry for this one.`
    : `WG will solve the whole model; ${healed ? 'the mesh was repaired and ' : ''}symmetry could not be used for this one.`;
  return solved ? 'Solved after WG repaired the mesh for this model.' : 'WG repaired the mesh and can solve this model.';
}
