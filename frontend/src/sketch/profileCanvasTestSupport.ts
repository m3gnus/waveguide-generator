import { vi } from 'vitest';
import { stubProfileServer } from '../testing/customProfileServer';
import type { CurvesResponse } from '../api/freeformProfile';

// A deliberately non-linear sampled curve, supplied by the fake service.
// The adapter must pass these coordinates through, with no spline or smoothing.
export const sampledCurve: [number, number][] = [[0, 12.7], [10, 14.215], [21.407, 20.125], [40, 30], [70.5, 61.37], [100, 108.42], [120, 140]];
export function profileServer(options: { refuse?: string } = {}) {
  const service = stubProfileServer(options);
  const fetcher = vi.fn<typeof fetch>(async (url, init) => {
    const response = await service(url, init);
    if (!String(url).endsWith('/curves') || !response.ok) return response;
    const curves = await response.json() as CurvesResponse;
    curves.H.samples = structuredClone(sampledCurve);
    curves.V.samples = sampledCurve.map(([z, r]) => [z, r + (z ? 5 : 0)]);
    return Response.json(curves);
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
