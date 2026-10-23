import type { SymmetryMode } from '../stores/solveOptions';
import { plannedEngineNames, type EngineCapability, type EngineSelection } from '../jobs/actions';

export { isBeatBackendEngine, LEGACY_BEAT_ENGINE } from '../jobs/engineIdentity';

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
  /** Solving ingested CAD geometry — ``server/jobs/runtime/``. */
  | 'imported-geometry';

/** Human phrasing for the feature, as the object of "X does not support …". */
const FEATURE_LABELS: Record<BackendFeature, string> = {
  'infinite-baffle': 'coupled infinite-baffle simulation',
  'ground-plane': 'a rigid ground plane',
  'imported-geometry': 'solving imported CAD geometry',
};

/** What to do instead, so a warning is actionable rather than just a refusal. */
const FEATURE_REMEDIES: Record<BackendFeature, string> = {
  'infinite-baffle':
    'Use Metal, or BEMPP with coupled infinite-baffle support.',
  'ground-plane':
    'Ground-plane solves need BEMPP full 3D on this build. Note that an infinite baffle is a different boundary, not a substitute.',
  'imported-geometry':
    'Choose AUTO, or an engine the solver list marks as solving imported CAD geometry.',
};

/**
 * Whether an engine declares imported CAD geometry among what it solves.
 *
 * A payload from a server older than `geometry_sources` is read as it behaved
 * then: Metal alone solved imported geometry.
 */
export function declaresImportedGeometry(engine: EngineCapability): boolean {
  return engine.geometry_sources?.includes('imported') ?? engine.name.toLowerCase() === 'metal';
}

/**
 * Whether the CAD engine select may offer an engine, before a return has been
 * prepared and the server's per-engine verdicts exist. Beyond what an engine
 * declares, an explicitly picked BEAT · Metal solves imported geometry in Fast
 * (AUTO does not choose it); the server's verdict still decides once prepared.
 */
export function choosableForImportedGeometry(engine: EngineCapability): boolean {
  return declaresImportedGeometry(engine) || engine.name.toLowerCase() === 'beat-metal';
}

/** Look up a concrete engine returned by the server's request plan. */
export function activeBackendCapability(
  resolvedEngine: string,
  engines: readonly EngineCapability[],
): EngineCapability | null {
  return engines.find((item) => item.name.toLowerCase() === resolvedEngine.toLowerCase()) ?? null;
}

/**
 * Available capabilities the server may plan for the requested engine.
 *
 * Full 3D walks the server-advertised backend order when the engine is AUTO.
 * Keeping the whole AUTO-engine plan matters on a GPU host:
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
): readonly EngineCapability[] {
  let advertised: readonly string[];
  try {
    advertised = plannedEngineNames(engine, { engines, engineSelection: selection });
  } catch {
    // Gating is deliberately total while capabilities/local settings settle;
    // Submission is validated by the server plan.
    return [];
  }
  const planned: EngineCapability[] = [];
  for (const name of advertised) {
    const capability = engines.find((item) => item.available && item.name.toLowerCase() === name);
    if (capability && !planned.includes(capability)) planned.push(capability);
  }
  return planned;
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
  // Declared candidates can rule a feature out before routing has settled.
  if (plan !== undefined && plan.length > 0) {
    return plan.some((item) => capabilitySupports(item, feature));
  }
  if (!backend) return true;
  const normalized = backendName(backend);
  if (!normalized) return true;
  if (plan !== undefined) return false;
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
  const name = (backendName(backend) ?? 'auto').toUpperCase();
  return `${name} does not support ${FEATURE_LABELS[feature]}. ${FEATURE_REMEDIES[feature]}`;
}

/**
 * Why `feature` is unavailable on this whole host, or undefined when some
 * available engine can run it (or the capability list has not loaded yet).
 *
 * Unlike `backendLimitation` this is judged over every engine the host has, not
 * the ones the current selection would plan, so it says "nothing here can",
 * never "the engine you picked cannot".
 */
export function hostLimitation(
  feature: BackendFeature,
  engines?: readonly EngineCapability[],
): string | undefined {
  if (!engines || engines.length === 0) return undefined;
  if (engines.some((item) => item.available && capabilitySupports(item, feature))) return undefined;
  return `No engine on this host supports ${FEATURE_LABELS[feature]}. ${FEATURE_REMEDIES[feature]}`;
}

/** The name to show for an engine wire identifier. */
export function engineDisplayName(name: string): string {
  switch (name.toLowerCase()) {
    case 'hornlab-metal-bem':
    case 'metal': return 'Metal';
    case 'hornlab-bempp-bem':
    case 'bempp': return 'BEMPP';
    case 'beat-engine':
    case 'beat': return 'BEAT';
    case 'beat-metal': return 'BEAT Metal';
    case 'beat-cuda': return 'BEAT CUDA';
    case 'beat-rocm': return 'BEAT ROCm';
    case 'beat-cpu': return 'BEAT CPU';
    case 'dryrun': return 'Dry run';
    default: return name;
  }
}

/**
 * Why the *current selection* cannot run `feature` although the host could,
 * naming the engine picked and what to switch to. Undefined when the selection
 * can run it, when the host cannot either (that is `hostLimitation`'s case), or
 * when the capability list has not loaded.
 */
export function selectionLimitation(
  feature: BackendFeature,
  backend: BackendIdentity,
  plan: readonly EngineCapability[] | undefined,
  engines?: readonly EngineCapability[],
): string | undefined {
  if (!engines || engines.length === 0) return undefined;
  if (backendSupports(backend, feature, plan)) return undefined;
  const capable = engines.filter((item) => item.available && capabilitySupports(item, feature));
  if (capable.length === 0) return undefined;
  const selected = typeof backend === 'string' || backend === null
    ? (backend ?? '')
    : backend.name;
  const own = engines.find((item) => item.name.toLowerCase() === backendName(backend));
  const others = capable.filter((item) => item !== own);
  const names = others.map((item) => engineDisplayName(item.name));
  const switchTo = ['AUTO', ...names].join(', ');
  // An engine that can run it in principle but is not installed or enabled here
  // is unavailable, not incapable; the remedy is the same but the reason is not.
  const problem = own && capabilitySupports(own, feature) && !own.available
    ? `${engineDisplayName(selected)} is not available on this host`
    : `${engineDisplayName(selected)} cannot solve ${FEATURE_LABELS[feature]}`;
  return `${problem}. Switch the engine to ${switchTo}.`;
}

/** Capability vocabulary allows either a general half or a specific mirror plane. */
export function symmetryDomainLimitation(backend: EngineCapability | null, mode: SymmetryMode): string | undefined {
  if (!backend || !backend.symmetry_domains || mode === 'auto') return undefined;
  const domain = mode.replaceAll('_', '-');
  const supported = backend.symmetry_domains.includes(domain)
    || (mode.startsWith('half_') && backend.symmetry_domains.includes('half'));
  return supported ? undefined : `${backend.label || engineDisplayName(backend.name)} does not support ${domain.replaceAll('-', ' ')} domains.`;
}
