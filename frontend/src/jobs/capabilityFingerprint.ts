import type { EngineCapability } from './actions';

/** Eligibility can change without availability changing during BEMPP recovery. */
export function capabilityFingerprint(engines: readonly EngineCapability[]): string {
  return JSON.stringify(engines.map((engine) => {
    const device = engine.assembly_device;
    return [engine.name, engine.available, engine.assembly_backend ?? null,
      device ? Object.entries(device).sort(([a], [b]) => a.localeCompare(b)) : null,
      engine.qualification ?? null, [...(engine.geometry_sources ?? [])].sort()];
  }).sort((a, b) => String(a[0]).localeCompare(String(b[0]))));
}
