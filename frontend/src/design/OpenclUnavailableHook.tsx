import guidance from '../../../shared/opencl-driver-guidance.v1.json';
import type { EngineCapability } from '../jobs/actions';
import { OpenClGuidance, type OpenClGuidancePlatform, type OpenClGuidanceReason } from '../shell/OpenClGuidance';

/** Show shared driver guidance only for BEMPP's numba fallback on supported hosts. */
export function OpenclUnavailableHook({ platform, engine }: {
  platform: string | null | undefined;
  engine: EngineCapability | undefined;
}) {
  if (engine?.name !== 'bempp' || engine.assembly_backend !== 'numba'
    || !platform || !Object.hasOwn(guidance.platforms, platform)) return null;
  const reason = engine.opencl_unavailable_reason;
  return <OpenClGuidance platform={platform as OpenClGuidancePlatform}
    reason={reason && Object.hasOwn(guidance.reasons, reason)
      ? reason as OpenClGuidanceReason : undefined} />;
}
