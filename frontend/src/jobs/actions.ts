import type { DesignDocument } from '../stores/design';

export interface EngineCapability {
  name: string;
  available: boolean;
  reason: string | null;
  version: string | null;
}

export interface Capabilities { engines: EngineCapability[] }

export function toSolveDesign(design: DesignDocument): Record<string, unknown> {
  const { quadrants, enclosure, mesh, ...root } = structuredClone(design);
  return {
    ...root,
    mesh: {
      ...mesh,
      quadrants: quadrants.reduce((mask, quadrant) => mask | (1 << (quadrant - 1)), 0),
    },
    enclosure: {
      depth: enclosure.depth,
      edge_radius: enclosure.edge_radius,
      space_l: enclosure.baffle_margin,
      space_t: enclosure.baffle_margin,
      space_r: enclosure.baffle_margin,
      space_b: enclosure.baffle_margin,
    },
  };
}

async function detail(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: string };
    if (body.detail) return body.detail;
  } catch { /* fall through */ }
  return `${response.status} ${response.statusText}`.trim();
}

export async function getCapabilities(fetcher: typeof fetch = fetch): Promise<Capabilities> {
  const response = await fetcher('/api/capabilities');
  if (!response.ok) throw new Error(await detail(response));
  return response.json() as Promise<Capabilities>;
}

export async function submitDesign(design: DesignDocument, fetcher: typeof fetch = fetch): Promise<string> {
  const response = await fetcher('/api/solve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ design: toSolveDesign(design), options: { engine: 'dryrun' } }),
  });
  if (!response.ok) throw new Error(await detail(response));
  return ((await response.json()) as { job_id: string }).job_id;
}

