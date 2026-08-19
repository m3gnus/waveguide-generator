import type { EngineCapability } from '../jobs/actions';

/**
 * Which solver capabilities the UI offers that not every backend implements.
 *
 * WG has always let every host pick every option and then failed at solve time,
 * which on a Windows/BEMPP box meant meshing a design for a minute before being
 * told the backend cannot solve it at all. These names let the controls that
 * offer a Metal-only choice hide it up front instead.
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
 * All three are Metal-only today. The mesher emits a coupled ``MOUTH_APERTURE``
 * group for infinite baffle that only Metal knows how to couple to the exterior
 * half-space; BEMPP and BEAT are always full 3-D and have no meridian path; and
 * imported geometry is refused outright on anything but Metal.
 */
const FEATURE_BACKENDS: Record<BackendFeature, readonly string[]> = {
  'infinite-baffle': ['metal'],
  'meridian-fast-path': ['metal'],
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
    'Solve free-standing with an enclosure to approximate a baffle, or run this design on a Mac with the Metal backend.',
  'meridian-fast-path':
    'Use Auto or Force full 3D; the meridian path needs the Metal backend.',
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
  const available = ['metal', 'bempp', 'dryrun']
    .flatMap((name) => engines.filter((item) => item.available && item.name.toLowerCase() === name));
  return available[0]?.name.toLowerCase() ?? null;
}

/** Whether `backend` can run `feature`. Unknown or unresolved backends pass. */
export function backendSupports(backend: string | null, feature: BackendFeature): boolean {
  if (!backend) return true;
  const normalized = backend.trim().toLowerCase();
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
  backend: string | null,
  feature: BackendFeature,
): string | undefined {
  if (backendSupports(backend, feature)) return undefined;
  const name = (backend ?? '').toUpperCase();
  return `${name} does not support ${FEATURE_LABELS[feature]}. ${FEATURE_REMEDIES[feature]}`;
}
