import guidance from '../../../shared/opencl-driver-guidance.v1.json';
import type { EngineCapability } from '../jobs/actions';
import { OpenClGuidance, type OpenClGuidanceHost, type OpenClGuidanceNotice, type OpenClGuidanceReason } from '../shell/OpenClGuidance';

/** Show shared driver guidance only for BEMPP's numba fallback on supported hosts. */
export function OpenclUnavailableHook({ platform, engine, notice }: {
  platform: string | null | undefined;
  engine: EngineCapability | undefined;
  notice?: OpenClGuidanceNotice;
}) {
  const host: OpenClGuidanceHost | undefined = platform === 'darwin' ? 'macos'
    : platform === 'windows' || platform === 'linux' ? platform : undefined;
  if (engine?.name !== 'bempp' || engine.assembly_backend !== 'numba' || !host) return null;
  const reason = engine.opencl_unavailable_reason;
  return <OpenClGuidance platform={host} notice={notice}
    reason={reason && Object.hasOwn(guidance.reasons, reason)
      ? reason as OpenClGuidanceReason : undefined} />;
}
