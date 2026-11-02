import type { CadReturnFinding } from '../api/cadlink';

/** What WG found and what it means, in the user's words. */
interface FindingWords { what: string; means: string }

/** Freshness differences that still ask the user before solving. */
const SOLVES_AS_IN_FUSION = 'WG solves the model as it is in Fusion. Approve to go ahead, or change it in Fusion and send it again.';

const FRESHNESS_WORDS: Record<string, FindingWords> = {
  body_modified: { what: 'The horn was changed in Fusion since WG sent it.', means: 'WG solves the changed shape.' },
  design_changed: { what: 'The design in WG has changed since this model was sent', means: SOLVES_AS_IN_FUSION },
  generator_changed: { what: 'This version of WG builds this design differently from the copy in Fusion', means: SOLVES_AS_IN_FUSION },
  missing_design: { what: 'WG no longer has the design this model came from', means: SOLVES_AS_IN_FUSION },
  unknown: { what: 'WG cannot tell whether this model still matches its design', means: SOLVES_AS_IN_FUSION },
};

const FINDING_WORDS: Record<string, FindingWords> = {
  'scope-degradation': {
    what: 'Part of the model was left out when Fusion sent it',
    means: 'WG solves the model without it. Approve to go ahead, or include it in Fusion and send the model again.',
  },
  'source-skip': {
    what: 'A driver or port surface is left out of this solve',
    means: 'WG solves the model without it. Approve to go ahead, or set it up and solve again.',
  },
  'source-paint-missing': {
    what: 'WG found a driver surface from the model’s shape, and it has no paint',
    means: 'WG solves the surface it found. Approve if it is the right one, or paint it in Sources & Mesh… in Fusion and send the model again.',
  },
  'geometry-overrode-paint': {
    what: 'Paint on some surfaces does not match the driver WG found from the model’s shape',
    means: 'WG solves the surface it found, not that paint. Approve if it is the right one, or fix the paint in Sources & Mesh… in Fusion and send the model again.',
  },
  'source-area-drift-override': {
    what: 'A driver surface’s area differs from what Fusion reported',
    means: 'WG solves the surface as it found it. Approve if it is the right one, or check it in Sources & Mesh… in Fusion and send the model again.',
  },
};

/** Findings whose own reason says which part was left out. */
const NAMES_ITS_PART: ReadonlySet<string> = new Set(['scope-degradation', 'source-skip']);

function wordsFor(finding: CadReturnFinding): FindingWords | undefined {
  return finding.kind === 'freshness' ? FRESHNESS_WORDS[String(finding.verdict)] : FINDING_WORDS[finding.kind];
}

/** What a finding of a kind is called, in words, never by id. */
const FINDING_TITLE: Record<string, string> = {
  fem_termination_with_exterior: 'FEM termination requires an interior-only solve',
  'stale-detection-unavailable': 'WG cannot tell whether this model is out of date',
  // Its detail, written from the domain actually solved, is the server's.
  'symmetry-cut-unverified': 'WG could not reduce this model any further',
};

export function findingName(finding: CadReturnFinding): string {
  const words = wordsFor(finding);
  if (words) {
    const named = NAMES_ITS_PART.has(finding.kind) ? finding.reason ?? finding.detail : null;
    return named ? `${words.what} (${String(named).replaceAll('_', ' ')})` : words.what;
  }
  return FINDING_TITLE[finding.kind] ?? finding.kind.replaceAll('-', ' ').replaceAll('_', ' ');
}

/** The sentence a finding carries: what it means and what to do, else its own
 * reason, verdict or written detail, or null for one that carries only
 * structured evidence. */
export function findingSentence(finding: CadReturnFinding): string | null {
  const words = wordsFor(finding);
  if (words) return words.means;
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
