import type { components } from './generated/openapi';
import { hydrateDesignDocument } from './designIo';
import { serializeDesign, type DesignDocument } from '../stores/design';

/**
 * Typed client for the Custom profile (FREEFORM) curve service in
 * `server/preview/freeform.py`. The mesher owns every curve operation; this
 * module only sends validated designs and checks that each answer belongs to
 * the request that asked for it.
 */
export type CurvesResponse = components['schemas']['CurvesResponse'];
export type PlaneCurve = components['schemas']['PlaneCurve'];
export type CurveAnchor = components['schemas']['CurveAnchor'];
export type CurveDeviation = components['schemas']['Deviation'];
export type CompareResponse = components['schemas']['CompareResponse'];
type InsertWire = components['schemas']['InsertResponse'];
type FreeformErrorWire = components['schemas']['FreeformError'];
export type ProfilePlane = components['schemas']['InsertRequest']['plane'];
export type InsertMode = NonNullable<components['schemas']['InsertRequest']['mode']>;

export interface InsertResult {
  content_key: string;
  design: DesignDocument;
  deviation: CurveDeviation;
}

/** The server refused the design or edit; `message` is ready to show. */
export class FreeformRequestError extends Error {
  override name = 'FreeformRequestError';
  constructor(message: string, readonly status: number) { super(message); }
}

/** An answer for a different request: never shown, never applied. */
export class StaleFreeformResponse extends Error {
  override name = 'StaleFreeformResponse';
}

function errorText(detail: FreeformErrorWire['detail']): string {
  if (typeof detail === 'string') return detail;
  return detail.map((item) => {
    const location = Array.isArray(item.loc) ? item.loc.filter((part) => part !== 'body').join('.') : '';
    const message = typeof item.msg === 'string' ? item.msg : 'Invalid value';
    return location ? `${location}: ${message}` : message;
  }).join('; ');
}

async function post<T extends { content_key: string }>(
  path: string, body: Record<string, unknown>, contentKey: string, signal: AbortSignal | undefined, fetcher: typeof fetch,
): Promise<T> {
  const response = await fetcher(`/api/design/freeform/${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
    body: JSON.stringify({ ...body, content_key: contentKey }),
  });
  let payload: unknown;
  try { payload = await response.json(); } catch { payload = undefined; }
  if (!response.ok) {
    const error = payload as Partial<FreeformErrorWire> | undefined;
    // A refusal names its request; one for another (or no) request is stale.
    if (error?.content_key !== contentKey) {
      if (error?.content_key == null && error?.detail !== undefined && response.status === 422) {
        throw new FreeformRequestError(errorText(error.detail), response.status);
      }
      throw new StaleFreeformResponse(`Discarded a response for another profile request (${response.status}).`);
    }
    throw new FreeformRequestError(error.detail !== undefined ? errorText(error.detail) : `Profile request failed (${response.status})`, response.status);
  }
  const result = payload as T | undefined;
  if (!result || result.content_key !== contentKey) throw new StaleFreeformResponse('Discarded a response for another profile request.');
  return result;
}

/** Resolved anchors, spans and samples in design mm before Scale; also validates the design. */
export function queryFreeformCurves(
  design: DesignDocument, contentKey: string, signal?: AbortSignal, fetcher: typeof fetch = fetch,
): Promise<CurvesResponse> {
  return post<CurvesResponse>('curves', { design: serializeDesign(design) }, contentKey, signal, fetcher);
}

/** Insert an anchor at z (design mm). Exact keeps the curve; refit releases the plane's handles. */
export async function insertFreeformAnchor(
  design: DesignDocument, edit: { plane: ProfilePlane; z_mm: number; mode: InsertMode },
  contentKey: string, signal?: AbortSignal, fetcher: typeof fetch = fetch,
): Promise<InsertResult> {
  const result = await post<InsertWire>('insert', { design: serializeDesign(design), ...edit }, contentKey, signal, fetcher);
  return { content_key: result.content_key, design: hydrateDesignDocument(result.design as Record<string, unknown>), deviation: result.deviation };
}

/** The mesher's per-plane curve comparison (no sample grid), in design mm before Scale. */
export function compareFreeformProfiles(
  before: DesignDocument, after: DesignDocument, contentKey: string, signal?: AbortSignal, fetcher: typeof fetch = fetch,
): Promise<CompareResponse> {
  return post<CompareResponse>('compare', { before: serializeDesign(before), after: serializeDesign(after) }, contentKey, signal, fetcher);
}
