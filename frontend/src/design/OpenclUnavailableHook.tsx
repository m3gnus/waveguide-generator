import guidance from '../../../shared/opencl-driver-guidance.v1.json';
import type { EngineCapability } from '../jobs/actions';
import { OpenClGuidance, type OpenClGuidancePlatform } from '../shell/OpenClGuidance';

/** Show shared driver guidance only for BEMPP's numba fallback on supported hosts. */
export function OpenclUnavailableHook({ platform, engine }: {
  platform: string | null | undefined;
  engine: EngineCapability | undefined;
}) {
  if (engine?.name !== 'bempp' || engine.assembly_backend !== 'numba'
    || !platform || !Object.hasOwn(guidance.platforms, platform)) return null;
  return <OpenClGuidance platform={platform as OpenClGuidancePlatform}
    reason={engine.opencl_unavailable_reason ?? undefined} />;
}
