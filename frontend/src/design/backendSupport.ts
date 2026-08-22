import type { EngineCapability, EngineSelection } from '../jobs/actions';

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
  /** The axisymmetric meridian fast path — ``server/solver/bempp.py``. */
  | 'meridian-fast-path'
  /** Solving ingested CAD geometry — ``server/jobs/runtime.py``. */
  | 'imported-geometry';

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
 * Explicit selection has exactly one candidate. AUTO can first select the
 * advertised meridian runner for eligible geometry and otherwise walks the
 * server-advertised full-3D order, skipping candidates whose capability does
 * not cover the requested mounting. Keeping the whole plan matters on a GPU
 * host: BEAT can be the resolved free-standing default while BEMPP later in
 * the plan handles coupled infinite-baffle solves.
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
  const requested = engine.trim().toLowerCase();
  if (requested && requested !== 'auto') {
    return engines.filter((item) => item.available && item.name.toLowerCase() === requested);
  }
  const advertised = [
    selection?.axisymmetricRunner,
    ...(selection?.full3dOrder ?? engines.map((item) => item.name)),
  ].map((name) => name?.trim().toLowerCase()).filter((name): name is string => Boolean(name));
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
  plan: readonly EngineCapability[] = [],
): boolean {
  if (!backend) return true;
  const normalized = backendName(backend);
  if (!normalized) return true;
  if (plan.some((item) => capabilitySupports(item, feature))) return true;
  if (typeof backend !== 'string') {
    return capabilitySupports(backend, feature);
  }
  // A bare name has no capability payload. Preserve the pre-gating behaviour
  // instead of hiding a control based on client-side engine knowledge.
  return true;
}

function capabilitySupports(backend: EngineCapability, feature: BackendFeature): boolean {
  if (feature === 'infinite-baffle') return backend.mountings?.includes('infinite-baffle') ?? true;
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
  plan: readonly EngineCapability[] = [],
): string | undefined {
  if (backendSupports(backend, feature, plan)) return undefined;
  const name = (backendName(backend) ?? '').toUpperCase();
  return `${name} does not support ${FEATURE_LABELS[feature]}. ${FEATURE_REMEDIES[feature]}`;
}
