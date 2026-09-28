/**
 * Domain decisions in the shapes `server/cadlink/domain_decision.py` seals
 * (`_decide`) and a job records (`decision_summary`), for the frontend tests.
 */
import type { DomainDecision, DomainDecisionCut, DomainDecisionSummary } from './domainDecision';

const FRAME_AS_MODELLED = {
  solver_from_cad: [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
  determinant: 1,
  proper: true,
  axis: '+z',
  up: '+y',
};

export function decisionFixture(overrides: Partial<DomainDecision> = {}): DomainDecision {
  return {
    contract: 'cad-domain-decision-v1',
    input_reading: 'full',
    resolved_reading: 'full',
    cad_cuts: [],
    off_centre_cuts: [],
    oblique_cuts: [],
    wg_cut_planes: [],
    solver_domain: { planes: [], fraction: 'full', multiplier: 1 },
    reflected_axes: [],
    reflection: { axes: [], parity: 0, winding_reversed: false, planes: [] },
    frame: { ...FRAME_AS_MODELLED, allowed_axes: ['+x', '-x', '+y', '-y', '+z', '-z'] },
    sources: { by_id: { 'source-hf': { tag: 2, retained_fraction: 1 } }, skipped: [] },
    confidence: 'established',
    evidence: { supporting: [], conflicts: [], ignored: [] },
    offered_changes: [],
    refusal: null,
    identity: { decision_sha256: `sha256:${'a'.repeat(64)}` },
    ...overrides,
  };
}

export function cutFixture(overrides: Partial<DomainDecisionCut> = {}): DomainDecisionCut {
  return {
    plane: 'x0',
    solver_plane: 'x0',
    kept_side: 'positive',
    found_by: 'geometry',
    status: 'recovered',
    reflected: false,
    features: [],
    ...overrides,
  };
}

/** A full model WG cut itself to a half at x = 0. */
export const WG_HALF = decisionFixture({
  wg_cut_planes: ['x0'],
  solver_domain: { planes: ['x0'], fraction: 'half', multiplier: 2 },
});

/** A full model WG cut to a quarter. */
export const WG_QUARTER = decisionFixture({
  wg_cut_planes: ['x0', 'y0'],
  solver_domain: { planes: ['x0', 'y0'], fraction: 'quarter', multiplier: 4 },
});

/** A model cut in CAD at x = 0 keeping x ≤ 0: recovered, its mesh reflected. */
export const RECOVERED_NEGATIVE_HALF = decisionFixture({
  input_reading: 'cut',
  resolved_reading: 'reduced',
  cad_cuts: [cutFixture({ kept_side: 'negative', reflected: true })],
  solver_domain: { planes: ['x0'], fraction: 'half', multiplier: 2 },
  reflected_axes: ['x'],
  reflection: { axes: ['x'], parity: 1, winding_reversed: true, planes: ['x0'] },
  frame: { ...FRAME_AS_MODELLED, allowed_axes: ['+z'] },
});

/** The same CAD half, which WG then also mirrors at y = 0: a quarter. */
export const RECOVERED_HALF_WG_QUARTER = decisionFixture({
  input_reading: 'cut',
  resolved_reading: 'reduced',
  cad_cuts: [cutFixture()],
  wg_cut_planes: ['y0'],
  solver_domain: { planes: ['x0', 'y0'], fraction: 'quarter', multiplier: 4 },
  frame: { ...FRAME_AS_MODELLED, allowed_axes: ['+z'] },
});

/** A CAD cut mirrored because Fusion recorded it (a Split Body feature). */
export const PROVENANCE_HALF = decisionFixture({
  input_reading: 'cut',
  resolved_reading: 'reduced',
  cad_cuts: [cutFixture({
    status: 'mirrored', found_by: 'cad-provenance',
    features: [{ plane: 'x0', kind: 'split-body', name: 'Split Body 3' }],
  })],
  solver_domain: { planes: ['x0'], fraction: 'half', multiplier: 2 },
  confidence: 'evidenced',
  frame: { ...FRAME_AS_MODELLED, allowed_axes: ['+z'] },
  offered_changes: [{ reading: 'as-shown' }],
});

const REFUSAL = { code: 'imported_open_half_shell', message: '' };

export const REFUSED_OBLIQUE = decisionFixture({
  input_reading: 'cut',
  resolved_reading: 'as-shown',
  oblique_cuts: [{ rim_edges: 40 }],
  confidence: 'refused',
  refusal: {
    ...REFUSAL,
    message: 'The model is open along an oblique plane (40 rim edges): it looks cut on an oblique plane, '
      + 'which WG cannot mirror in this version, and solved as shown it would be part of a speaker in free space. '
      + 'Send the uncut model — WG finds the symmetry and reduces it automatically.',
  },
});

export const REFUSED_OFF_CENTRE = decisionFixture({
  input_reading: 'cut',
  resolved_reading: 'as-shown',
  off_centre_cuts: [{ plane_axis: 'x', offset_mm: 12.5, rim_edges: 30 }],
  confidence: 'refused',
  refusal: { ...REFUSAL, message: 'The model is open along x = 12.5 mm (30 rim edges).' },
});

export const REFUSED_CUT = decisionFixture({
  input_reading: 'cut',
  resolved_reading: 'as-shown',
  cad_cuts: [cutFixture({
    status: 'refused',
    kept_side: 'negative',
    recovery: {
      judged: true,
      failed: [{ code: 'source-identity', message: 'the left and right sources are separate identities' }],
      by_change: false,
    },
  })],
  confidence: 'refused',
  refusal: { ...REFUSAL, message: 'The model is open along x = 0 (149 rim edges), so WG would solve half a speaker in free space.' },
});

export const OPEN_SHEET = decisionFixture({ input_reading: 'open-sheet', resolved_reading: 'full' });

export const UNRESOLVED = decisionFixture({
  input_reading: 'unresolved',
  resolved_reading: 'as-shown',
  cad_cuts: [cutFixture({ status: 'unmirrored', found_by: 'geometry' })],
  confidence: 'unresolved',
  offered_changes: [{ reading: 'reduced', planes: ['x0'] }],
});

/** What a job records of a decision (`decision_summary`). */
export function summaryOf(decision: DomainDecision): DomainDecisionSummary {
  return {
    contract: decision.contract,
    decision_sha256: decision.identity?.decision_sha256 ?? null,
    input_reading: decision.input_reading,
    resolved_reading: decision.resolved_reading,
    cad_cuts: decision.cad_cuts.map(({ plane, solver_plane, kept_side, found_by, status, reflected }) => (
      { plane, solver_plane, kept_side, found_by, status, reflected })),
    wg_cut_planes: decision.wg_cut_planes,
    solver_domain: decision.solver_domain,
    reflected_axes: decision.reflected_axes,
    reflection: {
      axes: decision.reflection?.axes, parity: decision.reflection?.parity,
      winding_reversed: decision.reflection?.winding_reversed,
    },
    frame: {
      solver_from_cad: decision.frame?.solver_from_cad, determinant: decision.frame?.determinant,
      proper: decision.frame?.proper, axis: decision.frame?.axis, up: decision.frame?.up,
    },
    sources: Object.fromEntries(Object.entries(decision.sources?.by_id ?? {})
      .map(([id, item]) => [id, { tag: item.tag, retained_fraction: item.retained_fraction }])),
    confidence: decision.confidence,
    refusal: decision.refusal,
  };
}
