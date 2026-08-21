import type { EngineCapability } from '../jobs/actions';

export type BackendIdentity = string | EngineCapability | null;

/**
 * Which solver capabilities the UI offers that not every backend implements.
 *
 * WG has always let every host pick every option and then failed at solve time,
 * which on a Windows/BEMPP box meant meshing a design for a minute before being
 * told the backend cannot solve it at all. These names let the controls that
 * offer a backend-specific choice hide it up front instead.
 *
 * Each entry mirrors a real server-side refusal. Keep them in sync: a feature
 * listed here as supported that the server then rejects is worse than no gate,
 * because the user has no way left to see why the solve failed.
 */
export type BackendFeature =
  /** Coupled infinite-baffle solves — ``server/solver/infinite_baffle.py``. */
  | 'infinite-baffle'
  /** The axisymmetric meridian fast path — ``server/solver/bempp.py``. */
  | 'meridian-fast-path'
  /** Solving ingested CAD geometry — ``server/jobs/runtime.py``. */
  | 'imported-geometry';

/**
 * Backends that implement each feature, lower-cased to match ``EngineCapability``.
 *
 * Axisymmetric execution is a platform-neutral runner rather than a full-3D
 * backend. Metal and BEMPP both understand the coupled ``MOUTH_APERTURE``
 * infinite-baffle contract; imported CAD remains Metal-only.
 */
const FEATURE_BACKENDS: Record<BackendFeature, readonly string[]> = {
  'infinite-baffle': ['metal', 'bempp', 'axisym'],
  'meridian-fast-path': ['axisym'],
  'imported-geometry': ['metal'],
};

/** Human phrasing for the feature, as the object of "X does not support …". */
const FEATURE_LABELS: Record<BackendFeature, string> = {
  'infinite-baffle': 'coupled infinite-baffle simulation',
  'meridian-fast-path': 'the axisymmetric meridian fast path',
  'imported-geometry': 'solving imported CAD geometry',
};

/** What to do instead, so a warning is actionable rather than just a refusal. */
const FEATURE_REMEDIES: Record<BackendFeature, string> = {
  'infinite-baffle':
    'Use Metal or BEMPP full 3D, or the Axisymmetric meridian path for eligible circular geometry.',
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
): string | null {
  const requested = engine.trim().toLowerCase();
  if (requested && requested !== 'auto') return requested;
  const available = ['metal', 'beat', 'bempp', 'dryrun']
    .flatMap((name) => engines.filter((item) => item.available && item.name.toLowerCase() === name));
  return available[0]?.name.toLowerCase() ?? null;
}

/** The capability record for the selected/resolved full-3D backend. */
export function activeBackendCapability(
  engine: string,
  engines: readonly EngineCapability[],
): EngineCapability | null {
  const name = activeBackendName(engine, engines);
  return name === null
    ? null
    : engines.find((item) => item.name.toLowerCase() === name) ?? null;
}

/**
 * Whether the host can run `feature` regardless of the selected full-3D backend.
 *
 * The server planner routes an eligible circular design to the Axisym meridian
 * runner *before* it reaches any full-3D fallback, so a host carrying Axisym
 * solves a coupled infinite-baffle design even when the resolved backend --
 * BEAT, say -- refuses one. Gating that option on the full-3D record alone
 * removed it from designs the server would have solved, and the user had no
 * way to discover that forcing the meridian mode brought it back.
 *
 * Geometry eligibility stays the planner's call. This only decides whether the
 * option is worth offering at all.
 */
function hostCoversFeature(
  host: readonly EngineCapability[],
  feature: BackendFeature,
): boolean {
  if (feature !== 'infinite-baffle') return false;
  return host.some((item) => item.available
    && item.name.toLowerCase() === 'axisym'
    && (item.mountings?.includes('infinite-baffle') ?? true));
}

function backendName(backend: BackendIdentity): string | null {
  if (backend === null) return null;
  return (typeof backend === 'string' ? backend : backend.name).trim().toLowerCase();
}

/** Whether `backend` can run `feature`. Unknown or unresolved backends pass. */
export function backendSupports(
  backend: BackendIdentity,
  feature: BackendFeature,
  host: readonly EngineCapability[] = [],
): boolean {
  if (!backend) return true;
  const normalized = backendName(backend);
  if (!normalized) return true;
  if (hostCoversFeature(host, feature)) return true;
  if (typeof backend !== 'string') {
    if (feature === 'infinite-baffle' && backend.mountings) {
      return backend.mountings.includes('infinite-baffle');
    }
    if (feature === 'meridian-fast-path' && backend.formulations) {
      return backend.formulations.includes('axisymmetric');
    }
  }
  const known = Object.values(FEATURE_BACKENDS).some((names) => names.includes(normalized))
    || normalized === 'bempp' || normalized === 'dryrun' || normalized === 'beat';
  if (!known) return true;
  return FEATURE_BACKENDS[feature].includes(normalized);
}

/**
 * A complete sentence pair naming the limitation and the way around it, or
 * undefined when the backend can run the feature after all.
 */
export function backendLimitation(
  backend: BackendIdentity,
  feature: BackendFeature,
  host: readonly EngineCapability[] = [],
): string | undefined {
  if (backendSupports(backend, feature, host)) return undefined;
  const name = (backendName(backend) ?? '').toUpperCase();
  return `${name} does not support ${FEATURE_LABELS[feature]}. ${FEATURE_REMEDIES[feature]}`;
}
