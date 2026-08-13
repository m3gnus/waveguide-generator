import { useEffect, useMemo, useState, type ReactNode } from 'react';
import type { Preferences } from '../prefs/preferences';
import { buildChartRenderPayload, buildDirectivityRenderPayload, type ChartReference } from './exporters';
import type { ResultPayload } from './types';

export type CanonicalChartKind = 'frequency_response' | 'directivity_index' | 'impedance' | 'beam_shape';

export interface CanonicalPlotRequest {
  endpoint: '/api/render-charts' | '/api/render-directivity';
  payload: Record<string, unknown>;
  imageKey?: CanonicalChartKind;
}

const imageCache = new Map<string, Promise<string>>();
const CACHE_LIMIT = 32;

export function buildCanonicalChartRequest(
  kind: CanonicalChartKind,
  result: ResultPayload,
  preferences: Preferences,
  reference?: ChartReference,
): CanonicalPlotRequest {
  return {
    endpoint: '/api/render-charts',
    payload: buildChartRenderPayload(result, preferences, reference),
    imageKey: kind,
  };
}

export function buildCanonicalDirectivityRequest(
  result: ResultPayload,
  preferences: Preferences,
  plane?: string,
  reference?: ChartReference,
): CanonicalPlotRequest {
  return {
    endpoint: '/api/render-directivity',
    payload: buildDirectivityRenderPayload(result, preferences, plane, reference),
  };
}

function requestKey(request: CanonicalPlotRequest): string {
  return `${request.endpoint}:${request.imageKey ?? 'image'}:${JSON.stringify(request.payload)}`;
}

async function responseError(response: Response): Promise<Error> {
  let message = `${response.status} ${response.statusText}`.trim();
  try {
    const body = await response.json() as { detail?: string };
    if (body.detail) message = body.detail;
  } catch { /* retain the HTTP status */ }
  return new Error(message || 'HornLab plot rendering failed.');
}

export async function fetchCanonicalPlot(
  request: CanonicalPlotRequest,
  fetcher: typeof fetch = fetch,
): Promise<string> {
  const key = requestKey(request);
  const existing = imageCache.get(key);
  if (existing) return existing;
  const pending = (async () => {
    const response = await fetcher(request.endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request.payload),
    });
    if (!response.ok) throw await responseError(response);
    const body = await response.json() as { image?: string; charts?: Partial<Record<CanonicalChartKind, string>> };
    const image = request.imageKey ? body.charts?.[request.imageKey] : body.image;
    if (!image) throw new Error(`HornLab plots returned no ${request.imageKey ?? 'directivity'} image.`);
    return image;
  })();
  imageCache.set(key, pending);
  while (imageCache.size > CACHE_LIMIT) imageCache.delete(imageCache.keys().next().value!);
  try {
    return await pending;
  } catch (error) {
    // The bounded cache can evict this request while it is still in flight.
    // Delete only our own entry: the same key may already have a newer owner.
    if (imageCache.get(key) === pending) imageCache.delete(key);
    throw error;
  }
}

export function clearCanonicalPlotCacheForTests(): void {
  imageCache.clear();
}

export function CanonicalPlot({ request, label, fallback }: {
  request: CanonicalPlotRequest;
  label: string;
  fallback: ReactNode;
}) {
  const serialized = useMemo(() => requestKey(request), [request]);
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState<{ image: string | null; pending: boolean; error: string | null }>({ image: null, pending: true, error: null });

  useEffect(() => {
    let live = true;
    setState({ image: null, pending: true, error: null });
    void fetchCanonicalPlot(request).then((image) => {
      if (live) setState({ image, pending: false, error: null });
    }).catch((error) => {
      if (live) setState((current) => ({ ...current, pending: false, error: error instanceof Error ? error.message : String(error) }));
    });
    return () => { live = false; };
  }, [serialized, retry]);

  return <div className="canonical-plot" aria-busy={state.pending}>
    {!state.image && <div className="canonical-plot-fallback">{fallback}</div>}
    {state.image && <img className="canonical-plot-image" src={state.image} alt={label} draggable={false}/>}
    {!state.image && state.pending && <span className="canonical-plot-status" role="status">Rendering with hornlab-plots…</span>}
    {!state.image && state.error && <div className="canonical-plot-error" role="status"><span>Canonical renderer unavailable · showing interactive fallback</span><button type="button" onClick={() => setRetry((value) => value + 1)}>Retry</button></div>}
  </div>;
}
