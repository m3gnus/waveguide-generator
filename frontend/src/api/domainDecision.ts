import type { CadReturnIngestRecord } from './cadlink';
import type { DomainReading } from './domainInterpretation';

/**
 * The one domain decision WG makes about an imported model
 * (`server/cadlink/domain_decision.py`, contract `cad-domain-decision-v1`):
 * what the model is, which planes the solver mirrors, what was reflected, and
 * the frame. The ingestion record seals it (`domain_decision`), every
 * submission checks it, and a job records its summary
 * (`decision_summary`) -- so the model card, the viewport and a run's
 * details all say what WG actually solves, from one source.
 *
 * Planes named `plane` are the CAD (exported) frame's; `solver_plane` and the
 * solver domain's planes are the solver frame's, which is the frame every
 * mesh the viewport draws is in.
 */
export const DOMAIN_DECISION_CONTRACT = 'cad-domain-decision-v1';

export type MirrorPlane = 'x0' | 'y0';

export interface DomainDecisionCut {
  plane: string;
  solver_plane: string | null;
  kept_side: 'positive' | 'negative' | null;
  found_by: string | null;
  /** `mirrored` on recorded evidence, `recovered` from the geometry alone,
   * `refused` (a flip condition failed), `unmirrored` (not judged a cut). */
  status: 'mirrored' | 'recovered' | 'refused' | 'unmirrored' | string;
  reflected: boolean;
  features?: Array<{ plane?: string; kind?: string; name?: string }>;
  recovery?: {
    judged?: boolean;
    failed?: Array<{ code?: string; message?: string }>;
    by_change?: boolean;
  };
}

/** What a decision and its summary share. */
export interface DomainDecisionCore {
  contract: string;
  decision_sha256?: string | null;
  input_reading: 'full' | 'cut' | 'open-sheet' | 'unresolved' | string;
  resolved_reading?: 'full' | 'reduced' | 'as-shown' | string | null;
  cad_cuts: DomainDecisionCut[];
  wg_cut_planes: string[];
  solver_domain: { planes: string[]; fraction: 'full' | 'half' | 'quarter' | string; multiplier?: number };
  reflected_axes: string[];
  reflection?: { axes?: string[]; parity?: number; winding_reversed?: boolean; planes?: string[] };
  frame?: {
    solver_from_cad?: number[][] | null;
    determinant?: number | null;
    proper?: boolean;
    axis?: string | null;
    up?: string | null;
    allowed_axes?: string[];
  };
  confidence?: string;
  refusal?: { code: string; message: string } | null;
}

/** The fields a job records and the Solve card's plan carries (`decision_summary`). */
export interface DomainDecisionSummary extends DomainDecisionCore {
  sources?: Record<string, { tag?: number; retained_fraction?: number | null }>;
}

/** What only the full decision carries. */
export interface DomainDecisionExtras {
  off_centre_cuts?: Array<{ plane_axis?: string; offset_mm?: number; rim_edges?: number }>;
  oblique_cuts?: Array<{ rim_edges?: number }>;
  evidence?: {
    supporting?: Array<{ source?: string | null; reading?: string | null; planes?: string[] }>;
    conflicts?: Array<Record<string, unknown>>;
    ignored?: Array<Record<string, unknown>>;
  };
  offered_changes?: DomainReading[];
  sources?: { by_id?: Record<string, { tag?: number; retained_fraction?: number | null }>; skipped?: string[] };
  identity?: { decision_sha256?: string; [key: string]: unknown };
}

/** The full decision an ingestion record seals. */
export type DomainDecision = DomainDecisionCore & DomainDecisionExtras;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** A decision (or its summary) this build can read, or null. */
export function readDomainDecision<T extends DomainDecisionCore = DomainDecision>(value: unknown): T | null {
  if (!isRecord(value) || value.contract !== DOMAIN_DECISION_CONTRACT) return null;
  if (!Array.isArray(value.cad_cuts) || !isRecord(value.solver_domain)) return null;
  if (!Array.isArray((value.solver_domain as Record<string, unknown>).planes)) return null;
  return value as unknown as T;
}

/** The decision an ingestion record seals; null for an earlier build's record. */
export function recordDomainDecision(record: Pick<CadReturnIngestRecord, 'domain_decision'> | null | undefined): DomainDecision | null {
  return readDomainDecision<DomainDecision>(record?.domain_decision);
}

function mirrorPlanes(values: readonly unknown[] | null | undefined): MirrorPlane[] {
  const found = new Set((values ?? []).map(String));
  return (['x0', 'y0'] as const).filter((plane) => found.has(plane));
}

/**
 * What the viewport draws for an imported model, in the solver frame its
 * meshes are in (display only -- nothing here reaches a solve):
 *
 * - `solvedPlanes`: the planes the solver mirrors. The solve mesh is the
 *   piece on their positive side; mirrored across them it is the whole model.
 * - `cadCutPlanes`: the planes the model was already cut on in CAD (mirrored
 *   on evidence or recovered). The display tessellation is of the model as
 *   it arrived, so it is mirrored across these to show the whole speaker;
 *   WG's own cuts need no mirroring there because the display was never cut.
 *
 * A refused or as-shown model mirrors nothing: its solver domain is empty.
 */
export function displaySymmetry(record: Pick<CadReturnIngestRecord, 'domain_decision' | 'symmetry'>): {
  solvedPlanes: MirrorPlane[];
  cadCutPlanes: MirrorPlane[];
} {
  const decision = recordDomainDecision(record);
  if (decision) {
    const solvedPlanes = mirrorPlanes(decision.solver_domain.planes);
    const cadCut = mirrorPlanes(decision.cad_cuts
      .filter((cut) => cut.status === 'mirrored' || cut.status === 'recovered')
      .map((cut) => cut.solver_plane ?? cut.plane));
    return { solvedPlanes, cadCutPlanes: cadCut.filter((plane) => solvedPlanes.includes(plane)) };
  }
  // An earlier build's record: the planes it states. `domain_planes` are what
  // the solver mirrors, `cut_planes` what WG cut itself.
  const symmetry = record.symmetry ?? {};
  const wgCut = mirrorPlanes(symmetry.cut_planes);
  const solvedPlanes = Array.isArray(symmetry.domain_planes)
    ? mirrorPlanes(symmetry.domain_planes as unknown[])
    : wgCut;
  return { solvedPlanes, cadCutPlanes: solvedPlanes.filter((plane) => !wgCut.includes(plane)) };
}

// -- words -------------------------------------------------------------------

export function planeWords(plane: string): string {
  return `${plane.charAt(0)} = 0`;
}

export function joinPlanes(planes: readonly string[]): string {
  return planes.map(planeWords).join(' and ');
}

function negativeSideWords(planes: readonly string[]): string {
  return planes.map((plane) => `${plane.charAt(0)} ≤ 0`).join(' and ');
}

const FOUND_BY_WORDS: Record<string, string> = {
  declaration: 'declared in Fusion',
  'cad-provenance': 'recorded in Fusion',
  user: 'your choice',
  'user-lineage': 'your earlier choice',
  lineage: 'as before',
};

function mirroredWhy(cuts: readonly DomainDecisionCut[]): string | null {
  const names = [...new Set(cuts.flatMap((cut) => (cut.features ?? []).map((feature) => feature.name).filter(Boolean) as string[]))];
  const by = cuts.map((cut) => cut.found_by).find((value) => value && value !== 'geometry') ?? null;
  if (by === 'cad-provenance' && names.length) return names.join(', ');
  if (by === 'lineage' && names.length) return `${names.join(', ')}, as before`;
  return by ? FOUND_BY_WORDS[by] ?? null : null;
}

function fractionWords(fraction: string): string {
  return fraction === 'quarter' ? 'a quarter' : fraction === 'half' ? 'a half' : fraction;
}

/** Why a refused decision is refused, in a few words (its message says the rest). */
function refusalHeadline(decision: DomainDecisionCore & Partial<DomainDecisionExtras>): string {
  const refusal = decision.refusal!;
  if (refusal.code === 'imported_frame_improper') {
    return 'Refused: the solver frame is not a proper rotation — send the model again';
  }
  if ((decision.oblique_cuts ?? []).length) return 'Refused: cut on an oblique plane — send the uncut model';
  const offCentre = (decision.off_centre_cuts ?? [])[0];
  if (offCentre) {
    const offset = typeof offCentre.offset_mm === 'number' ? `${Number(offCentre.offset_mm.toPrecision(6))} mm` : 'off the origin';
    return `Refused: cut off-centre at ${offCentre.plane_axis ?? '?'} = ${offset} — send the uncut model`;
  }
  const refused = decision.cad_cuts.filter((cut) => cut.status === 'refused').map((cut) => cut.plane);
  if (refused.length) return `Refused: cut in CAD at ${joinPlanes(refused)} cannot be recovered — send the uncut model`;
  return 'Refused: this model cannot be solved as it stands';
}

export interface DecisionReading {
  /** The concluded reading, in one line. */
  text: string;
  /** Set when every engine refuses the model. */
  refused: boolean;
  /** The refusal as the engines state it. */
  refusal: string | null;
  /** The flip conditions a refused CAD cut failed, in words. */
  failed: string[];
  /** Why the model radiates along only one axis, when that restricts it. */
  frameNote: string | null;
}

/**
 * The concluded reading of a decision in plain words, e.g.
 * "Full model — solved as a half (x = 0)",
 * "Cut in CAD at x = 0 — recovered by mirroring",
 * "Refused: cut on an oblique plane — send the uncut model".
 * Reads a full decision or the summary a job recorded.
 */
export function decisionReading(
  decision: DomainDecisionCore & Partial<DomainDecisionExtras>,
  options: { userChoice?: boolean } = {},
): DecisionReading {
  const solved = decision.solver_domain.planes.map(String);
  const cuts = decision.cad_cuts.filter((cut) => cut.status === 'mirrored' || cut.status === 'recovered');
  const frameNote = cuts.length && solved.length && decision.frame?.axis
    && Array.isArray(decision.frame.allowed_axes) && decision.frame.allowed_axes.length === 1
    ? `Radiates along ${decision.frame.axis} as modelled: a model cut in CAD is solved only in the frame it was modelled in, whose radiation axis lies in the cut plane.`
    : null;
  if (decision.refusal) {
    const failed = [...new Set(decision.cad_cuts
      .filter((cut) => cut.status === 'refused')
      .flatMap((cut) => (cut.recovery?.failed ?? []).map((item) => item.message).filter(Boolean) as string[]))];
    return { text: refusalHeadline(decision), refused: true, refusal: decision.refusal.message, failed, frameNote: null };
  }
  const mine = options.userChoice ? ' (your choice)' : '';
  let text: string;
  if (cuts.length) {
    const recovered = cuts.every((cut) => cut.status === 'recovered');
    const reflected = cuts.filter((cut) => cut.reflected).map((cut) => cut.plane);
    const why = recovered ? null : mirroredWhy(cuts);
    const notes = [why, reflected.length ? `the ${negativeSideWords(reflected)} side reflected` : null].filter(Boolean);
    text = `Cut in CAD at ${joinPlanes(cuts.map((cut) => cut.plane))} — ${recovered ? 'recovered by mirroring' : 'mirrored'}${notes.length ? ` (${notes.join('; ')})` : ''}`;
    const cutSolverPlanes = cuts.map((cut) => cut.solver_plane ?? cut.plane);
    if (solved.some((plane) => !cutSolverPlanes.includes(plane))) {
      text += ` · solved as ${fractionWords(decision.solver_domain.fraction)} (${joinPlanes(solved)})`;
    }
  } else if (solved.length) {
    text = `Full model — solved as ${fractionWords(decision.solver_domain.fraction)} (${joinPlanes(solved)})`;
  } else {
    const looks = decision.cad_cuts.filter((cut) => cut.status === 'unmirrored').map((cut) => cut.plane);
    switch (decision.input_reading) {
      case 'full': text = 'Full model'; break;
      case 'open-sheet': text = `Open sheet — solved as shown${mine}`; break;
      case 'unresolved': text = looks.length
        ? `Solved as shown${mine} — the geometry alone cannot say whether it is cut at ${joinPlanes(looks)}`
        : `Solved as shown${mine}`;
        break;
      default: text = looks.length ? `Solved as shown${mine} — looks cut at ${joinPlanes(looks)}` : `Solved as shown${mine}`;
    }
  }
  return { text, refused: false, refusal: null, failed: [], frameNote };
}

/** A decision's summary as the run-details rows state it. */
export function decisionSummaryRows(summary: DomainDecisionCore): Array<{ label: string; value: string; title?: string }> {
  const rows: Array<{ label: string; value: string; title?: string }> = [];
  const reading = decisionReading(summary);
  rows.push({ label: 'Reading', value: reading.text, title: reading.refusal ?? undefined });
  const solved = summary.solver_domain.planes.map(String);
  rows.push({
    label: 'Solved as',
    value: solved.length
      ? `${summary.solver_domain.fraction} (mirrored at ${joinPlanes(solved)})`
      : 'the whole model as shown',
  });
  if (summary.reflected_axes.length) {
    const planes = summary.cad_cuts.filter((cut) => cut.reflected).map((cut) => cut.plane);
    rows.push({
      label: 'Reflection',
      value: planes.length ? `the ${negativeSideWords(planes)} side reflected` : summary.reflected_axes.join(', '),
      title: 'The model was cut in CAD keeping the negative side; its mesh was reflected onto the side the solver mirrors.',
    });
  }
  const frame = summary.frame;
  if (frame?.axis) {
    rows.push({
      label: 'Frame',
      value: `${frame.axis} forward${frame.up ? `, ${frame.up} up` : ''}${frame.proper === false ? ' (not a proper rotation)' : ''}`,
    });
  }
  const sha = summary.decision_sha256?.replace(/^sha256:/, '');
  if (sha) rows.push({ label: 'Decision', value: sha.slice(0, 12), title: `sha256:${sha}` });
  return rows;
}
