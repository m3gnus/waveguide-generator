import { refusalText } from './httpError';
import type { components } from './generated/openapi';
import { hydrateDesignDocument } from './designIo';
import { serializeDesign, type DesignDocument } from '../stores/design';
import { stagedActivity } from '../shell/stagedActivity';

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
type ConvertWire = components['schemas']['ConvertResponse'];
export type ConversionReport = components['schemas']['GeneratedComparison'];
export type ConversionVerdict = ConvertWire['verdict'];
type FreeformErrorWire = components['schemas']['FreeformError'];
export type ProfilePlane = components['schemas']['InsertRequest']['plane'];
export type InsertMode = NonNullable<components['schemas']['InsertRequest']['mode']>;

export interface InsertResult {
  content_key: string;
  design: DesignDocument;
  deviation: CurveDeviation;
}

export interface ConversionResult {
  content_key: string;
  design: DesignDocument;
  report: ConversionReport;
  verdict: ConversionVerdict;
}

/** Explicit conversion: the server reports kept-wall deviations and any removed lip separately. */
export function convertToCustomProfile(
  design: DesignDocument, contentKey: string, fetcher: typeof fetch = fetch, signal?: AbortSignal,
): Promise<ConversionResult> {
  return stagedActivity({ kind: 'export', label: 'Converting to Custom profile' }, fetcher, signal, async (scoped, signal) => {
    const result = await post<ConvertWire>('convert', { design: serializeDesign(design) }, contentKey, signal, scoped);
    return { ...result, design: hydrateDesignDocument(result.design as Record<string, unknown>) };
  });
}

/** A request failed; only a keyed validation response proves the draft was refused. */
export class FreeformRequestError extends Error {
  override name = 'FreeformRequestError';
  constructor(message: string, readonly status: number, readonly refused = false) { super(message); }
}

/** An answer for a different request: never shown, never applied. */
export class StaleFreeformResponse extends Error {
  override name = 'StaleFreeformResponse';
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
    // Only an explicitly different key identifies an obsolete refusal. Proxy
    // and unexpected server failures may have no keyed JSON body at all.
    if (error?.content_key != null && error.content_key !== contentKey) {
      throw new StaleFreeformResponse(`Discarded a response for another profile request (${response.status}).`);
    }
    const fallback = path === 'convert' ? 'WG could not convert this design to Custom profile. Try again.' : 'WG could not check this profile. Try again.';
    throw new FreeformRequestError(error?.detail !== undefined ? refusalText(error.detail, path === 'convert' ? 'conversion to Custom profile' : 'profile edit', fallback) : fallback, response.status,
      response.status === 422 && error?.content_key === contentKey && error?.detail !== undefined);
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
