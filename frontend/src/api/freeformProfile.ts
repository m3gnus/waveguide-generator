import { refusalText } from './httpError';
import type { components } from './generated/openapi';
import { hydrateDesignDocument } from './designIo';
import { serializeDesign, type DesignDocument } from '../stores/design';
import { stagedActivity } from '../shell/stagedActivity';

/**
 * Typed client for the Custom profile (FREEFORM) curve service in
 * `server/jobs/freeform.py`. The mesher owns every curve operation; this
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

/** Display rounding only; verdicts and limits always use the original measurements. */
export const previewMm = (value: number): string => value.toFixed(Math.abs(value) < 1 ? 3 : 1);

/** Round display measurements without changing stored or compared values. */
export function previewMeasurementWords(text: string): string {
  return text.replace(/(z = )?(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?) mm/gi, (_, z, n) => `${z ?? ''}${z ? Number(n).toFixed(1) : previewMm(Number(n))} mm`)
    .replace(/(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)°/gi, (_, n) => `${Number(n).toFixed(1)}°`);
}

export function conversionPreviewText(report: ConversionReport, verdict: ConversionVerdict) {
  const kept = report.features.some(feature => feature.name === 'Rolled-back lip removed');
  const maximum = Math.max(report.wall.max_deviation_mm, report.mouth.max_deviation_mm, report.axial_extent_deviation_mm);
  const headline = verdict === 'match' ? 'Matches the original'
    : `Close to the original — the ${kept ? 'kept inside' : 'inside'} of the horn changes by at most ${previewMm(maximum)} mm`;
  const changes = report.features.map(feature => {
    const message = feature.message;
    if (feature.name === 'Rolled-back lip removed') {
      const length = message.match(/(?:length along the horn was |axial length )([\d.e+-]+)/)?.[1];
      return `The curled-back lip at the mouth is removed${length ? ` (${previewMm(Number(length))} mm long)` : ''}.`;
    }
    if (feature.name === 'Rear construction') {
      const movement = message.match(/rear edge or rear tip: ([\d.e+-]+)/)?.[1];
      if (movement) return `The rear edge or rear tip moves by up to ${previewMm(Number(movement))} mm.`;
    }
    if (feature.name === 'Source-cap angle') return 'The source surface angle and curvature become fixed values.';
    return previewMeasurementWords(message.split(/(?<=\.)\s/)[0].replace(/^Outer wall changes/, 'The outside of the horn changes'));
  });
  if (report.source_cap_angle_before_deg !== report.source_cap_angle_after_deg) {
    changes.push(`The source surface angle changes from ${report.source_cap_angle_before_deg.toFixed(1)}° to ${report.source_cap_angle_after_deg.toFixed(1)}°.`);
  }
  const measured = (label: string, d: ConversionReport['wall']) =>
    `${label}: ${previewMm(d.max_deviation_mm)} mm change; ${d.azimuth_deg.toFixed(1)}° around the horn; z = ${d.z_mm.toFixed(1)} mm. Exact change: ${d.max_deviation_mm} mm.`;
  const details = [...new Set([
    measured('Horizontal profile (H)', report.H), measured('Vertical profile (V)', report.V),
    measured(kept ? 'Kept wall' : 'Whole wall', report.wall), measured(kept ? 'Kept mouth outline' : 'Mouth outline', report.mouth),
    `Wall start/end position change along the length: ${previewMm(report.axial_extent_deviation_mm)} mm. Exact change: ${report.axial_extent_deviation_mm} mm.`,
    'Measurements include Scale. Match requires at most 0.01 mm at the checked points; the wall conversion limit is 0.5 mm.',
    `Source surface opening angle: ${report.source_cap_angle_before_deg.toFixed(1)}° → ${report.source_cap_angle_after_deg.toFixed(1)}°. Exact angles: ${report.source_cap_angle_before_deg}° → ${report.source_cap_angle_after_deg}°.`,
    ...(report.curve_deviation_mm == null ? [] : [`Exact profile curve change: ${report.curve_deviation_mm} mm.`]),
    ...report.features.map(feature => feature.message), report.method.grid, ...report.method.limits,
  ])];
  return { headline, changes: [...new Set(changes)], details };
}
