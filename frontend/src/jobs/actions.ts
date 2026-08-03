import { serializeDesign, type DesignDocument } from '../stores/design';
import { useSolveOptionsStore, type SolveOptions } from '../stores/solveOptions';

export interface EngineCapability {
  name: string;
  available: boolean;
  reason: string | null;
  version: string | null;
}

export interface Capabilities { engines: EngineCapability[] }

export function toSolveDesign(design: DesignDocument): Record<string, unknown> {
  return serializeDesign(design);
}

export function formatApiDetail(value: unknown): string | null {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) {
    const items = value.map((item) => formatApiDetail(item)).filter((item): item is string => Boolean(item));
    return items.length ? items.join('; ') : null;
  }
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    const message = typeof record.msg === 'string' ? record.msg : typeof record.message === 'string' ? record.message : null;
    const location = Array.isArray(record.loc) ? record.loc.map(String).join('.') : null;
    if (message) return location ? `${location}: ${message}` : message;
    const entries = Object.entries(record).flatMap(([key, item]) => {
      const formatted = formatApiDetail(item);
      return formatted ? [`${key}: ${formatted}`] : [];
    });
    return entries.length ? entries.join('; ') : null;
  }
  return value === null || value === undefined ? null : String(value);
}

async function detail(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: unknown };
    const formatted = formatApiDetail(body.detail);
    if (formatted) return formatted;
  } catch { /* fall through */ }
  return `${response.status} ${response.statusText}`.trim();
}

export async function getCapabilities(fetcher: typeof fetch = fetch): Promise<Capabilities> {
  const response = await fetcher('/api/capabilities');
  if (!response.ok) throw new Error(await detail(response));
  return response.json() as Promise<Capabilities>;
}

export function resolveEngine(engine: string, capabilities: Capabilities, solverMode = 'auto'): string {
  if (engine.toLowerCase() !== 'auto') return engine.toLowerCase();
  const order = solverMode.toLowerCase().replace('-', '_') === 'circsym'
    ? ['circsym']
    : ['metal', 'bempp', 'dryrun'];
  const available = order.flatMap((name) => capabilities.engines.filter((item) => item.available && item.name.toLowerCase() === name))[0];
  if (!available) throw new Error('No solver backend is currently available');
  return available.name.toLowerCase();
}

export async function submitDesign(
  design: DesignDocument,
  requestedOptions: SolveOptions = useSolveOptionsStore.getState().options(),
  fetcher: typeof fetch = fetch,
): Promise<string> {
  const options = structuredClone(requestedOptions);
  const response = await fetcher('/api/solve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ design: toSolveDesign(design), options }),
  });
  if (!response.ok) throw new Error(await detail(response));
  return ((await response.json()) as { job_id: string }).job_id;
}
