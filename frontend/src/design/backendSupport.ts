import { plannedEngineNames, type EngineCapability, type EngineSelection } from '../jobs/actions';
import type { SolverMode } from '../stores/solveOptions';

export type BackendIdentity = string | EngineCapability | null;

/**
 * Which solver capabilities the UI offers that not every backend implements.
 *
 * WG has always let every host pick every option and then failed at solve time,
 * which on a Windows/BEMPP box meant meshing a design for a minute before being
 * told the backend cannot solve it at all. These names let the controls that
 * offer a backend-specific choice hide it up front instead.
 *
 * Support comes from the server's capability record. This file owns only the
 * UI vocabulary and remedies; it does not maintain a second engine matrix.
 */
export type BackendFeature =
  /** Coupled infinite-baffle solves — ``server/solver/infinite_baffle.py``. */
  | 'infinite-baffle'
  /** Rigid ground-plane solves — ``server/solver/ground_plane.py``. */
  | 'ground-plane'
  /** The axisymmetric meridian fast path — ``server/solver/bempp.py``. */
  | 'meridian-fast-path'
  /** Solving ingested CAD geometry — ``server/jobs/runtime.py``. */
  | 'imported-geometry';

/** Human phrasing for the feature, as the object of "X does not support …". */
const FEATURE_LABELS: Record<BackendFeature, string> = {
  'infinite-baffle': 'coupled infinite-baffle simulation',
  'ground-plane': 'a rigid ground plane',
  'meridian-fast-path': 'the axisymmetric meridian fast path',
  'imported-geometry': 'solving imported CAD geometry',
};

/** What to do instead, so a warning is actionable rather than just a refusal. */
const FEATURE_REMEDIES: Record<BackendFeature, string> = {
  'infinite-baffle':
    'Use Metal or BEMPP full 3D, or the Axisymmetric meridian path for eligible circular geometry.',
  'ground-plane':
    'Ground-plane solves need BEMPP full 3D on this build. Note that an infinite baffle is a different boundary, not a substitute.',
  'meridian-fast-path':
    'Use Auto or Force full 3D; this geometry cannot use the platform-neutral meridian runner.',
  'imported-geometry':
    'Imported CAD solves need the Metal backend. The parametric workspace solves on this machine.',
};

/**
 * The backend a solve would actually use, or null when none is available.
 *
 * Deliberately total where ``resolveEngine`` throws: this drives which controls
 * render, and a capability probe that has not landed yet must not blank the
 * panel. An unknown backend is treated as capable so nothing is hidden on a
 * guess — the server still refuses, which is the behaviour we have today.
 */
export function activeBackendName(
  engine: string,
  engines: readonly EngineCapability[],
  selection?: Readonly<EngineSelection>,
): string | null {
  const requested = engine.trim().toLowerCase();
  if (requested && requested !== 'auto') return requested;
  const resolved = selection?.resolvedDefault?.toLowerCase();
  if (resolved && engines.some((item) => item.available
    && item.name.toLowerCase() === resolved)) return resolved;
  const order = selection?.full3dOrder.map((name) => name.toLowerCase())
    ?? engines.filter((item) => item.formulations?.includes('full-3d'))
      .map((item) => item.name.toLowerCase());
  const available = order
    .flatMap((name) => engines.filter((item) => item.available && item.name.toLowerCase() === name));
  return available[0]?.name.toLowerCase() ?? null;
}

/** The capability record for the selected/resolved full-3D backend. */
export function activeBackendCapability(
  engine: string,
  engines: readonly EngineCapability[],
  selection?: Readonly<EngineSelection>,
): EngineCapability | null {
  const name = activeBackendName(engine, engines, selection);
  return name === null
    ? null
    : engines.find((item) => item.name.toLowerCase() === name) ?? null;
}

/**
 * Available capabilities the server may plan for the requested engine.
 *
 * Solver mode AUTO can first select the advertised meridian runner for
 * eligible geometry, even with an explicit non-dryrun backend chosen as the
 * full-3D fallback. An AUTO engine then walks the server-advertised full-3D
 * order. Forced Full 3D excludes the meridian runner; forced CircSym includes
 * only that runner. Keeping the whole AUTO-engine plan matters on a GPU host:
 * BEAT can be the resolved free-standing default while BEMPP later in the plan
 * handles coupled infinite-baffle solves.
 *
 * Geometry eligibility and final routing stay the server planner's call. This
 * list only prevents the frontend from hiding an option no planned candidate
 * could run.
 */
export function plannedBackendCapabilities(
  engine: string,
  engines: readonly EngineCapability[],
  selection?: Readonly<EngineSelection>,
  solverMode: SolverMode = 'auto',
): readonly EngineCapability[] {
  let advertised: readonly string[];
  try {
    advertised = plannedEngineNames(
      engine,
      { engines, engineSelection: selection },
      solverMode,
    );
  } catch {
    // Gating is deliberately total while capabilities/local settings settle;
    // resolveEngine still blocks the stale or contradictory submission.
    return [];
  }
  const planned: EngineCapability[] = [];
  for (const name of advertised) {
    const capability = engines.find((item) => item.available && item.name.toLowerCase() === name);
    if (capability && !planned.includes(capability)) planned.push(capability);
  }
  return planned;
}

/** The engine name WG advertised while BEAT's backends were one entry. */
export const LEGACY_BEAT_ENGINE = 'beat';

/** Whether `name` is one of BEAT's per-backend engines. */
export function isBeatBackendEngine(name: string): boolean {
  return name.toLowerCase().startsWith('beat-');
}

/**
 * What a stored `beat` selection should become, or `null` to leave it alone.
 *
 * BEAT is four engines now -- `beat-cuda`, `beat-rocm`, `beat-metal`,
 * `beat-cpu` -- and design files and persisted solve options written before the
 * split still say `beat`. The server still accepts that name and resolves it,
 * so a solve submitted with it runs; what it cannot do is put a matching option
 * in the picker, which would render with nothing selected while the store still
 * said `beat`. Migrating the stored value is what keeps the control and the
 * request agreeing about what will run.
 *
 * The preferred answer is the first *available* variant in the server's own
 * full-3D order, so "run this on BEAT" keeps meaning "on the best BEAT this
 * machine has" -- which is what it meant when a probe was choosing. Failing
 * that it is the first advertised variant, available or not: a greyed-out
 * BEAT row carrying its reason tells the user why their saved choice cannot
 * run here, where a silent jump to AUTO would not.
 *
 * `null` when the engine is not `beat`, when capabilities have not loaded, or
 * when the server advertises no BEAT variants at all -- an older server, whose
 * bare `beat` option is still the right thing to have selected.
 */
export function migratedLegacyBeatEngine(
  engine: string,
  engines: readonly EngineCapability[],
  selection?: Readonly<EngineSelection>,
): string | null {
  if (engine.trim().toLowerCase() !== LEGACY_BEAT_ENGINE) return null;
  const variants = engines.filter((item) => isBeatBackendEngine(item.name));
  if (variants.length === 0) return null;
  const order = selection?.full3dOrder?.map((name) => name.toLowerCase()) ?? [];
  const preferred = order
    .flatMap((name) => variants.filter((item) => item.available
      && item.name.toLowerCase() === name))[0]
    ?? variants.find((item) => item.available)
    ?? variants[0];
  return preferred.name.toLowerCase();
}

function backendName(backend: BackendIdentity): string | null {
  if (backend === null) return null;
  return (typeof backend === 'string' ? backend : backend.name).trim().toLowerCase();
}

/** Whether `backend` can run `feature`. Unknown or unresolved backends pass. */
export function backendSupports(
  backend: BackendIdentity,
  feature: BackendFeature,
  plan?: readonly EngineCapability[],
): boolean {
  if (!backend) return true;
  const normalized = backendName(backend);
  if (!normalized) return true;
  // When supplied, the plan is authoritative: a full-3D backend outside a
  // forced CircSym plan must not rescue a feature the sole Axisym candidate
  // lacks (and vice versa).
  if (plan !== undefined) {
    return plan.some((item) => capabilitySupports(item, feature));
  }
  if (typeof backend !== 'string') {
    return capabilitySupports(backend, feature);
  }
  // A bare name has no capability payload. Preserve the pre-gating behaviour
  // instead of hiding a control based on client-side engine knowledge.
  return true;
}

function capabilitySupports(backend: EngineCapability, feature: BackendFeature): boolean {
  if (feature === 'infinite-baffle') return backend.mountings?.includes('infinite-baffle') ?? true;
  if (feature === 'ground-plane') return backend.mountings?.includes('ground-plane') ?? true;
  if (feature === 'meridian-fast-path') return backend.formulations?.includes('axisymmetric') ?? true;
  return backend.geometry_sources?.includes('imported') ?? true;
}

/**
 * A complete sentence pair naming the limitation and the way around it, or
 * undefined when the backend can run the feature after all.
 */
export function backendLimitation(
  backend: BackendIdentity,
  feature: BackendFeature,
  plan?: readonly EngineCapability[],
): string | undefined {
  if (backendSupports(backend, feature, plan)) return undefined;
  const name = (backendName(backend) ?? '').toUpperCase();
  return `${name} does not support ${FEATURE_LABELS[feature]}. ${FEATURE_REMEDIES[feature]}`;
}
