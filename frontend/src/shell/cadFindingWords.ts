import type { CadReturnFinding } from '../api/cadlink';

/** What a finding of a kind is called, in words, never by id. */
const FINDING_TITLE: Record<string, string> = {
  fem_termination_with_exterior: 'FEM termination requires an interior-only solve',
  'stale-detection-unavailable': 'WG cannot tell whether this model is out of date',
};

export function findingName(finding: CadReturnFinding): string {
  return FINDING_TITLE[finding.kind] ?? finding.kind.replaceAll('-', ' ').replaceAll('_', ' ');
}

/** The sentence a finding carries (its reason, verdict or written detail), or
 * null for one that carries only structured evidence. */
export function findingSentence(finding: CadReturnFinding): string | null {
  const preferred = finding.reason ?? finding.verdict ?? finding.detail;
  return preferred ? String(preferred).replaceAll('_', ' ') : null;
}

/** A finding's structured evidence, for developer details. */
export function findingEvidence(finding: CadReturnFinding): string {
  return Object.entries(finding)
    .filter(([key]) => !['id', 'kind', 'blocking', 'evidence_path'].includes(key))
    .map(([key, value]) => `${key.replaceAll('_', ' ')}: ${value === null || value === undefined ? '—'
      : typeof value === 'object' ? JSON.stringify(value) : String(value)}`)
    .join(' · ');
}
