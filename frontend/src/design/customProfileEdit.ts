import { useCallback, useEffect, useRef, useState } from 'react';
import {
  compareFreeformProfiles, FreeformRequestError, insertFreeformAnchor, queryFreeformCurves, StaleFreeformResponse,
  type CompareResponse, type CurveAnchor, type CurvesResponse, type InsertMode,
} from '../api/freeformProfile';
import { inspectProfileSamples } from '../api/designIo';
import { compareProfileSamples, type GeneratedProfileChange } from '../api/profileSamples';
import {
  currentDocumentLoad, serializeDesign, useDesignStore,
  type CrossSectionStation, type DesignDocument, type ExprNumber, type FreeformPoint, type FreeformProfile,
} from '../stores/design';

/**
 * The one owner of Custom profile (FREEFORM) edits.
 *
 * Every shape-changing edit is built here as a complete candidate document,
 * previewed (mesher curve comparison plus generated-profile change), and only
 * then committed as one project Undo step. Curve evaluation, insertion,
 * tangent resolution and comparison stay in the mesher behind
 * `api/freeformProfile.ts`; this module only applies elementary coordinate
 * transformations to authored values (rotate a handle, scale an axis).
 *
 * Storage keeps normalized `t`; tables show and accept design mm before Scale.
 */
export type Plane = 'H' | 'V';
const PROFILE_KEY = { H: 'profile_h', V: 'profile_v' } as const;
export const MAX_FREEFORM_ANCHORS = 64;
/** The t -> mm -> t round trip a display must not disturb. */
export const POSITION_ROUND_TRIP_TOLERANCE = 1e-12;

/** A refused edit: the message says what to change; the document is untouched. */
export class ProfileEditRefusal extends Error {
  override name = 'ProfileEditRefusal';
}

export interface ProfileEditResult {
  after: DesignDocument;
  /** What the user should know before accepting: pins kept or released, rewritten values. */
  notes: string[];
}

export const isSharedProfile = (design: DesignDocument) => design.shared_profile === true;
/** Planes with their own authored table; shared mode edits H for both. */
export const activePlanes = (design: DesignDocument): Plane[] => isSharedProfile(design) ? ['H'] : ['H', 'V'];
const lengthOf = (design: DesignDocument) => design.length ?? 120;
export const positionMm = (t: number, length: number) => t * length;
const profileOf = (design: DesignDocument, plane: Plane): FreeformProfile => {
  const profile = design[PROFILE_KEY[plane]];
  if (!profile) throw new ProfileEditRefusal('Custom profile edits require a Custom profile design.');
  return profile;
};
const pointLabel = (plane: Plane, index: number, count: number) =>
  index === 0 ? `${plane} throat` : index === count - 1 ? `${plane} mouth` : `${plane} point ${index}`;
const present = <T>(value: T | null | undefined): value is T => value !== undefined && value !== null;

function expressionAt(design: DesignDocument, path: string): ExprNumber | undefined {
  const expression = design._expressions?.[path];
  return expression && typeof expression.raw === 'string' ? expression : undefined;
}

/** Refuse to silently overwrite an authored expression with a computed number. */
function requireLiteral(design: DesignDocument, path: string, what: string): void {
  const expression = expressionAt(design, path);
  if (expression) throw new ProfileEditRefusal(`${what} is the expression "${expression.raw}". Replace it with a number before this edit, so the rewritten value is not silently lost.`);
}

function pointPath(plane: Plane, index: number, field?: string): string {
  return `${PROFILE_KEY[plane]}.points.${index}${field ? `.${field}` : ''}`;
}

/** Metadata keys under a plane's point list, with `$last` resolved, re-indexed by `map` (null deletes). */
function remapPointMetadata(design: DesignDocument, plane: Plane, oldCount: number, map: (index: number) => number | null): void {
  const prefix = `${PROFILE_KEY[plane]}.points.`;
  const rekey = (key: string): string | null | undefined => {
    if (!key.startsWith(prefix)) return undefined;
    const [head, ...rest] = key.slice(prefix.length).split('.');
    const index = head === '$last' ? oldCount - 1 : Number(head);
    if (!Number.isInteger(index)) return undefined;
    const next = map(index);
    return next === null ? null : [`${prefix}${next}`, ...rest].join('.');
  };
  if (design._expressions) {
    const entries = Object.entries(design._expressions).flatMap(([key, value]) => {
      const next = rekey(key);
      return next === null ? [] : [[next ?? key, value] as const];
    });
    if (entries.length) design._expressions = Object.fromEntries(entries); else delete design._expressions;
  }
  if (design._absent) {
    const absent = design._absent.flatMap((key) => {
      const next = rekey(key);
      return next === null ? [] : [next ?? key];
    });
    if (absent.length) design._absent = absent; else delete design._absent;
  }
}

/** Remove the metadata of exactly these edited leaf paths: the user replaced them. */
function clearMetadata(design: DesignDocument, paths: string[]): void {
  const edited = new Set(paths);
  if (design._expressions) {
    for (const key of Object.keys(design._expressions)) if (edited.has(key)) delete design._expressions[key];
    if (!Object.keys(design._expressions).length) delete design._expressions;
  }
  if (design._absent) {
    design._absent = design._absent.filter((key) => !edited.has(key));
    if (!design._absent.length) delete design._absent;
  }
}

function describePins(plane: Plane, points: FreeformPoint[]): string | undefined {
  const pinned = points.flatMap((point, index) => [
    present(point.handle_in) ? `${pointLabel(plane, index, points.length)} (in)` : null,
    present(point.handle_out) ? `${pointLabel(plane, index, points.length)} (out)` : null,
  ]).filter(Boolean);
  return pinned.length ? `Handles that remain pinned in ${plane}: ${pinned.join(', ')}.` : undefined;
}

const degrees = (radians: number) => radians * 180 / Math.PI;
const radians = (deg: number) => deg * Math.PI / 180;
const norm = ([dz, dr]: readonly [number, number]) => Math.hypot(dz, dr);
/** Rotate an explicit handle to a wall direction, keeping its length; incoming handles point back. */
function rotatedHandle(handle: [number, number], angleDeg: number, side: 'in' | 'out'): [number, number] {
  const length = norm(handle); const sign = side === 'in' ? -1 : 1;
  return [sign * length * Math.cos(radians(angleDeg)), sign * length * Math.sin(radians(angleDeg))];
}

function checkInteriorAngle(value: number, endpoint: boolean): void {
  if (!Number.isFinite(value) || (endpoint ? value < -90 || value > 90 : value <= -90 || value >= 90)) {
    throw new ProfileEditRefusal(endpoint ? 'An endpoint angle must be between −90 and 90°.' : 'An interior angle must be greater than −90 and less than 90°.');
  }
}

function withPlane(design: DesignDocument, plane: Plane, points: FreeformPoint[]): DesignDocument {
  const next = structuredClone(design);
  next[PROFILE_KEY[plane]] = { ...profileOf(design, plane), points };
  return next;
}

/** Delete an interior anchor; the surviving neighbours' facing handles are released. */
export function deleteAnchor(design: DesignDocument, plane: Plane, index: number): ProfileEditResult {
  const points = profileOf(design, plane).points;
  if (index <= 0 || index >= points.length - 1) throw new ProfileEditRefusal('The throat and mouth anchors cannot be deleted.');
  const next = structuredClone(points);
  const released: string[] = [];
  const previous = next[index - 1]; const following = next[index + 1];
  if (present(previous.handle_out)) { delete previous.handle_out; released.push(`${pointLabel(plane, index - 1, points.length)} (out)`); }
  if (present(following.handle_in)) { delete following.handle_in; released.push(`${pointLabel(plane, index + 1, points.length)} (in)`); }
  next.splice(index, 1);
  const after = withPlane(design, plane, next);
  remapPointMetadata(after, plane, points.length, (old) => old === index ? null : old > index ? old - 1 : old);
  clearMetadata(after, [pointPath(plane, index - 1, 'handle_out'), pointPath(plane, index, 'handle_in')]);
  return { after, notes: [
    `Deleted ${pointLabel(plane, index, points.length)} at z = ${formatMm(positionMm(points[index].t, lengthOf(design)))} mm.`,
    released.length ? `Released the facing handles of the neighbours: ${released.join(', ')}. Those spans use automatic tangents.` : 'No neighbouring handle was pinned.',
    describePins(plane, next),
  ].filter((note): note is string => Boolean(note)) };
}

/** Edit an anchor's axial position (interior only) or radius, in design mm before Scale. */
export function setAnchorValue(design: DesignDocument, plane: Plane, index: number, key: 'z' | 'r', value: number): ProfileEditResult {
  const points = profileOf(design, plane).points;
  const length = lengthOf(design);
  const next = structuredClone(points);
  const label = pointLabel(plane, index, points.length);
  const touched = [pointPath(plane, index, key === 'z' ? 't' : 'r')];
  if (!Number.isFinite(value)) throw new ProfileEditRefusal('Enter a finite number.');
  let after: DesignDocument;
  if (key === 'z') {
    if (index === 0 || index === points.length - 1) throw new ProfileEditRefusal('The throat is at z = 0 and the mouth at Length. Change Length to move the mouth.');
    const t = value / length;
    if (!(t > points[index - 1].t && t < points[index + 1].t)) throw new ProfileEditRefusal(`${label} must stay strictly between its neighbours (no crossing or duplicate z).`);
    next[index].t = t;
    after = withPlane(design, plane, next);
  } else {
    if (!(value > 0)) throw new ProfileEditRefusal('Radius must be greater than 0 mm.');
    next[index].r = value;
    after = withPlane(design, plane, next);
    // One circular throat: a separate V follows an H throat edit and vice versa.
    // A shared design leaves its inactive V alone (Use H throat for V reconciles it).
    if (index === 0 && !isSharedProfile(design)) {
      const other: Plane = plane === 'H' ? 'V' : 'H';
      const otherPoints = structuredClone(profileOf(design, other).points);
      otherPoints[0].r = value;
      after[PROFILE_KEY[other]] = { ...profileOf(design, other), points: otherPoints };
      touched.push(pointPath(other, 0, 'r'));
    }
  }
  clearMetadata(after, touched);
  return { after, notes: [describePins(plane, next) ?? 'No handle is pinned in this plane; spans next to the anchor refit automatically.'] };
}

/**
 * Which stored angle a table cell edits. Endpoints have one meaningful side;
 * a smooth anchor shares one tangent; an interior corner keeps incoming in
 * `angle_deg` (which also sets outgoing while `angle_out_deg` is unset).
 */
export function angleField(point: FreeformPoint, index: number, count: number, side: 'in' | 'out'): 'angle_deg' | 'angle_out_deg' | null {
  if (index === 0) return side === 'out' ? 'angle_deg' : null;
  if (index === count - 1) return side === 'in' ? 'angle_deg' : null;
  if (point.join !== 'corner') return 'angle_deg';
  return side === 'in' ? 'angle_deg' : 'angle_out_deg';
}

/** Set or clear an anchor angle; explicit handles on the affected sides rotate, keeping their lengths. */
export function setAnchorAngle(design: DesignDocument, plane: Plane, index: number, side: 'in' | 'out', value: number | undefined): ProfileEditResult {
  const points = profileOf(design, plane).points;
  const point = points[index];
  const field = angleField(point, index, points.length, side);
  if (!field) throw new ProfileEditRefusal(index === 0 ? 'The throat has no incoming side.' : 'The mouth has no outgoing side.');
  const endpoint = index === 0 || index === points.length - 1;
  const next = structuredClone(points);
  const target = next[index];
  const notes: string[] = [];
  if (value === undefined) {
    delete target[field];
    const kept = [present(target.handle_in) ? 'in' : null, present(target.handle_out) ? 'out' : null].filter(Boolean);
    if (kept.length) notes.push(`Cleared the angle; the explicit ${kept.join(' and ')} handle${kept.length > 1 ? 's stay' : ' stays'} pinned and still set${kept.length > 1 ? '' : 's'} the direction.`);
  } else {
    checkInteriorAngle(value, endpoint);
    target[field] = value;
    // Sides governed by this value: both on a smooth anchor, both on a corner
    // while it has no separate outgoing angle, otherwise only the edited side.
    const sides: ('in' | 'out')[] = endpoint ? [index === 0 ? 'out' : 'in']
      : target.join !== 'corner' || (field === 'angle_deg' && !present(target.angle_out_deg)) ? ['in', 'out']
        : [field === 'angle_deg' ? 'in' : 'out'];
    for (const handleSide of sides) {
      const key = handleSide === 'in' ? 'handle_in' : 'handle_out';
      const handle = target[key];
      if (!present(handle)) continue;
      if (norm(handle) === 0) throw new ProfileEditRefusal(`The ${handleSide} handle of ${pointLabel(plane, index, points.length)} has zero length, so it has no direction to rotate.`);
      target[key] = rotatedHandle(handle, value, handleSide);
      notes.push(`Rotated the pinned ${handleSide} handle of ${pointLabel(plane, index, points.length)} to ${formatAngle(value)}, keeping its length ${formatMm(norm(handle))} mm.`);
    }
  }
  const after = withPlane(design, plane, next);
  clearMetadata(after, [pointPath(plane, index, field), pointPath(plane, index, 'handle_in'), pointPath(plane, index, 'handle_out')]);
  notes.push(describePins(plane, next) ?? 'No handle is pinned in this plane.');
  return { after, notes };
}

/** Wall direction (radians) of one side: explicit handle, then authored angle, then the resolved curve. */
function sideDirection(point: FreeformPoint, side: 'in' | 'out', resolved: CurveAnchor | undefined): { angle: number; explicit: boolean } | null {
  const handle = side === 'in' ? point.handle_in : point.handle_out;
  if (present(handle) && norm(handle) > 0) {
    const sign = side === 'in' ? -1 : 1;
    return { angle: Math.atan2(sign * handle[1], sign * handle[0]), explicit: true };
  }
  const authored = side === 'out' && present(point.angle_out_deg) ? point.angle_out_deg : point.angle_deg;
  if (present(authored)) return { angle: radians(authored), explicit: true };
  const resolvedDeg = side === 'in' ? resolved?.angleInDeg : resolved?.angleOutDeg;
  return present(resolvedDeg) ? { angle: radians(resolvedDeg), explicit: false } : null;
}

/**
 * Change an anchor's join. Corner to smooth makes pinned directions collinear
 * on their averaged direction; with nothing pinned the anchor simply takes an
 * automatic tangent. Needs the current server curve query for resolved sides.
 */
export function setAnchorJoin(design: DesignDocument, plane: Plane, index: number, join: 'smooth' | 'corner', curves?: CurvesResponse): ProfileEditResult {
  const points = profileOf(design, plane).points;
  const next = structuredClone(points);
  const target = next[index];
  const label = pointLabel(plane, index, points.length);
  const endpoint = index === 0 || index === points.length - 1;
  const notes: string[] = [];
  if ((target.join ?? 'smooth') === join) throw new ProfileEditRefusal(`${label} is already ${join}.`);
  if (join === 'corner') {
    target.join = 'corner';
    notes.push(endpoint
      ? `${label} becomes a corner: unless a handle or point angle pins it, its angle now follows the chord to the neighbouring anchor.`
      : `${label} becomes a corner. Its current angle and handles are kept; set an outgoing angle to break the tangent.`);
  } else if (endpoint) {
    target.join = 'smooth';
    notes.push(`${label} becomes smooth: the endpoint angle follows the explicit handle, point angle or profile endpoint angle.`);
  } else {
    const resolved = curves?.[plane].anchors[index];
    const pinned = present(target.handle_in) || present(target.handle_out) || present(target.angle_deg) || present(target.angle_out_deg);
    target.join = 'smooth';
    if (pinned) {
      const incoming = sideDirection(target, 'in', resolved);
      const outgoing = sideDirection(target, 'out', resolved);
      if (!incoming || !outgoing) throw new ProfileEditRefusal(`The resolved direction of ${label} is not available yet. Wait for the curve query, or fix the profile errors first.`);
      const sum: [number, number] = [Math.cos(incoming.angle) + Math.cos(outgoing.angle), Math.sin(incoming.angle) + Math.sin(outgoing.angle)];
      if (norm(sum) < 1e-9) throw new ProfileEditRefusal(`${label}'s incoming and outgoing directions are opposed, so there is no averaged smooth direction. Set an angle first.`);
      const average = degrees(Math.atan2(sum[1], sum[0]));
      if (!(average > -90 && average < 90)) throw new ProfileEditRefusal(`The averaged direction of ${label} (${formatAngle(average)}) points backwards; set an angle between −90 and 90° first.`);
      if (present(target.angle_deg) || present(target.angle_out_deg)) target.angle_deg = average;
      delete target.angle_out_deg;
      for (const side of ['in', 'out'] as const) {
        const key = side === 'in' ? 'handle_in' : 'handle_out';
        const handle = target[key];
        if (present(handle)) target[key] = rotatedHandle(handle, average, side);
      }
      notes.push(`${label} becomes smooth on the averaged direction ${formatAngle(average)}; pinned handles keep their lengths.`);
    } else {
      notes.push(`${label} becomes smooth with an automatic tangent.`);
    }
  }
  const after = withPlane(design, plane, next);
  clearMetadata(after, ['join', 'angle_deg', 'angle_out_deg', 'handle_in', 'handle_out'].map((field) => pointPath(plane, index, field)));
  notes.push(describePins(plane, next) ?? 'No handle is pinned in this plane.');
  return { after, notes };
}

/** Drop wire nulls (an omitted optional value) so a hydrated point has the store's shape. */
function storePoint(point: FreeformPoint): FreeformPoint {
  return Object.fromEntries(Object.entries(point).filter(([, value]) => present(value))) as unknown as FreeformPoint;
}

/** Take the edited plane from a server insertion, keeping everything else from `design`. */
export function withInsertedProfile(design: DesignDocument, inserted: DesignDocument, plane: Plane): DesignDocument {
  const before = profileOf(design, plane).points;
  const points = profileOf(inserted, plane).points.map(storePoint);
  if (points.length !== before.length + 1) throw new ProfileEditRefusal('The insertion did not return exactly one new anchor.');
  const index = points.findIndex((point, item) => item >= before.length || point.t !== before[item].t);
  const after = withPlane(design, plane, points);
  // Surviving points keep their authored expressions and omissions at their new indices.
  remapPointMetadata(after, plane, before.length, (old) => old >= index ? old + 1 : old);
  return after;
}

function scaledAngle(angle: number, factor: number): number {
  // Direction (cos a, sin a) under z -> k z becomes (k cos a, sin a).
  return degrees(Math.atan2(Math.sin(radians(angle)), factor * Math.cos(radians(angle))));
}

/**
 * Stretch: an exact affine axial transform. Stored t stays, radii stay, handle
 * dz scales by new/old, and every explicit angle becomes its stretched direction.
 *
 * The automatic tangent solver is not affine-invariant, so an automatic
 * handle of an active plane is pinned to its resolved handle from the
 * current curve query (`curves`, for this exact design), scaled the same way.
 * An inactive V has no resolved curve of its own; its authored values are
 * transformed and its automatic spans refit when it is separated again.
 */
export function stretchLength(design: DesignDocument, newLength: number, curves: CurvesResponse): ProfileEditResult {
  const oldLength = lengthOf(design);
  checkLength(newLength, oldLength);
  const factor = newLength / oldLength;
  const after = structuredClone(design);
  after.length = newLength;
  const rewritten: string[] = [];
  const pinned: string[] = [];
  const active = activePlanes(design);
  for (const plane of ['H', 'V'] as const) {
    const profile = after[PROFILE_KEY[plane]]!;
    const anchors = active.includes(plane) ? curves[plane].anchors : undefined;
    if (anchors && anchors.length !== profile.points.length) throw new ProfileEditRefusal('The resolved curve is out of date. Wait for the curve check and try again.');
    for (const field of ['throat_angle_deg', 'mouth_angle_deg'] as const) {
      const angle = profile[field];
      if (!present(angle)) continue;
      requireLiteral(design, `${PROFILE_KEY[plane]}.${field}`, `${plane} ${field === 'throat_angle_deg' ? 'throat' : 'mouth'} angle`);
      profile[field] = scaledAngle(angle, factor);
      rewritten.push(`${PROFILE_KEY[plane]}.${field}`);
    }
    profile.points.forEach((point, index) => {
      for (const field of ['angle_deg', 'angle_out_deg'] as const) {
        const angle = point[field];
        if (!present(angle)) continue;
        requireLiteral(design, pointPath(plane, index, field), `${pointLabel(plane, index, profile.points.length)} angle`);
        point[field] = scaledAngle(angle, factor);
        rewritten.push(pointPath(plane, index, field));
      }
      for (const [key, resolvedKey] of [['handle_in', 'handleIn'], ['handle_out', 'handleOut']] as const) {
        const handle = point[key] ?? anchors?.[index][resolvedKey] ?? undefined;
        if (!present(handle)) continue;
        if (!present(point[key])) pinned.push(`${pointLabel(plane, index, profile.points.length)} (${key === 'handle_in' ? 'in' : 'out'})`);
        point[key] = [handle[0] * factor, handle[1]];
      }
    });
  }
  clearMetadata(after, ['length', ...rewritten]);
  return { after, notes: [
    `Stretch profile: every anchor, handle and station keeps its fraction of Length (${formatMm(oldLength)} → ${formatMm(newLength)} mm); radii are unchanged.`,
    rewritten.length ? `${rewritten.length} explicit angle${rewritten.length === 1 ? ' is' : 's are'} rewritten to the stretched direction.` : 'No explicit angle needed rewriting.',
    pinned.length ? `To keep the stretch exact, these automatic handles are pinned to the stretched resolved curve and no longer refit automatically: ${pinned.join(', ')}.` : 'Every handle was already pinned.',
    ...(isSharedProfile(design) ? ['The inactive vertical profile is stretched by its authored values; its automatic spans refit if it is separated again.'] : []),
  ] };
}

/** A paste that adopts its own length stretches the rest, so it needs the resolved curve too. */
export function pasteBuilder(pasted: PastedProfile, choice: PasteChoice, fetcher: typeof fetch = fetch): EditBuilder {
  return async (design, { origin, signal }) => {
    const adopting = choice.adoptLength && pasted.importedLength !== lengthOf(design);
    const curves = adopting ? await queryFreeformCurves(design, profileRequestKey(origin, design), signal, fetcher) : undefined;
    return applyPastedProfile(design, pasted, choice, curves);
  };
}

/** Stretch needs the resolved curve of the exact design it transforms. */
export function stretchLengthBuilder(newLength: number, fetcher: typeof fetch = fetch): EditBuilder {
  return async (design, { origin, signal }) => {
    const curves = await queryFreeformCurves(design, profileRequestKey(origin, design), signal, fetcher);
    return stretchLength(design, newLength, curves);
  };
}

/**
 * Move mouth: interior anchors, stations and handles keep their absolute mm;
 * only the mouth moves. Refused when an interior position would reach the new mouth.
 *
 * Automatic spans are fitted globally, so the earlier spans of an active plane
 * are pinned to their resolved handles (`curves`, for this exact design) and
 * only the last span, into the moved mouth, is refitted.
 */
export function moveMouth(design: DesignDocument, newLength: number, curves: CurvesResponse): ProfileEditResult {
  const oldLength = lengthOf(design);
  checkLength(newLength, oldLength);
  const ratio = oldLength / newLength;
  const after = structuredClone(design);
  after.length = newLength;
  const rewritten: string[] = [];
  const pinned: string[] = [];
  const active = activePlanes(design);
  for (const plane of ['H', 'V'] as const) {
    const profile = after[PROFILE_KEY[plane]]!;
    const anchors = active.includes(plane) ? curves[plane].anchors : undefined;
    if (anchors && anchors.length !== profile.points.length) throw new ProfileEditRefusal('The resolved curve is out of date. Wait for the curve check and try again.');
    const last = profile.points.length - 1;
    profile.points.forEach((point, index) => {
      if (anchors) {
        // Every span before the last keeps its resolved shape.
        const sides = [index > 0 && index < last ? 'in' : null, index < last - 1 ? 'out' : null].filter(Boolean) as ('in' | 'out')[];
        for (const side of sides) {
          const key = side === 'in' ? 'handle_in' : 'handle_out';
          const resolved = anchors[index][side === 'in' ? 'handleIn' : 'handleOut'];
          if (present(point[key]) || !present(resolved)) continue;
          point[key] = [resolved[0], resolved[1]];
          pinned.push(`${pointLabel(plane, index, profile.points.length)} (${side})`);
        }
      }
      if (index === 0 || index === last) return;
      const z = point.t * oldLength;
      if (z >= newLength) throw new ProfileEditRefusal(`${pointLabel(plane, index, profile.points.length)} is at z = ${formatMm(z)} mm, at or beyond the new mouth (${formatMm(newLength)} mm). Delete or move it first, or use Stretch profile.`);
      requireLiteral(design, pointPath(plane, index, 't'), `${pointLabel(plane, index, profile.points.length)} position`);
      point.t = point.t * ratio;
      rewritten.push(pointPath(plane, index, 't'));
    });
  }
  const stations = after.cross_sections ?? [];
  stations.forEach((station: CrossSectionStation, index) => {
    if (index === 0 || index === stations.length - 1) return;
    const z = station.t * oldLength;
    if (z >= newLength) throw new ProfileEditRefusal(`Cross-section station ${index + 1} is at z = ${formatMm(z)} mm, at or beyond the new mouth. Remove or move it first, or use Stretch profile.`);
    requireLiteral(design, `cross_sections.${index}.t`, `Cross-section station ${index + 1} position`);
    station.t = station.t * ratio;
    rewritten.push(`cross_sections.${index}.t`);
  });
  clearMetadata(after, ['length', ...rewritten]);
  return { after, notes: [
    `Move mouth: interior anchors, handles and stations keep their positions in mm; only the mouth moves (${formatMm(oldLength)} → ${formatMm(newLength)} mm).`,
    pinned.length ? `So that only the last span changes, these automatic handles are pinned to the current curve and no longer refit automatically: ${pinned.join(', ')}.` : 'The spans before the mouth were already pinned or are straight.',
    ...(isSharedProfile(design) ? ['The inactive vertical profile keeps its positions too; its automatic spans refit if it is separated again.'] : []),
  ] };
}

/** Move mouth needs the resolved curve of the exact design it transforms. */
export function moveMouthBuilder(newLength: number, fetcher: typeof fetch = fetch): EditBuilder {
  return async (design, { origin, signal }) => {
    const curves = await queryFreeformCurves(design, profileRequestKey(origin, design), signal, fetcher);
    return moveMouth(design, newLength, curves);
  };
}

function checkLength(newLength: number, oldLength: number): void {
  if (!Number.isFinite(newLength) || newLength < 20 || newLength > 1_000) throw new ProfileEditRefusal('Length must be between 20 and 1000 mm.');
  if (newLength === oldLength) throw new ProfileEditRefusal('Length is unchanged.');
}

function sameProfile(left: FreeformProfile | undefined, right: FreeformProfile | undefined): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

/** Why V cannot be separated yet, or undefined. The throat must stay one circle. */
export function separateVerticalBlocker(design: DesignDocument): string | undefined {
  if (!isSharedProfile(design)) return undefined;
  const h = profileOf(design, 'H').points[0].r; const v = profileOf(design, 'V').points[0].r;
  return h === v ? undefined
    : `The stored vertical profile has throat radius ${formatMm(v)} mm but the horizontal throat is ${formatMm(h)} mm. Use H throat for V first; the throat is one circle.`;
}

/**
 * Turn the separate vertical profile on or off. Off keeps V stored and
 * inactive. On restores the stored V; `seed` instead starts V from H, which
 * is what the first separation of a new design does (they are still equal).
 */
export function setSeparateVertical(design: DesignDocument, separate: boolean, seed = false): ProfileEditResult {
  const after = structuredClone(design);
  if (!separate) {
    after.shared_profile = true;
    return { after, notes: ['The vertical profile is kept but inactive; the horizontal profile now shapes both planes.'] };
  }
  // Seeding replaces V with H, so only a restore needs the throats to agree.
  const blocker = seed ? undefined : separateVerticalBlocker(design);
  if (blocker) throw new ProfileEditRefusal(blocker);
  after.shared_profile = false;
  if (seed) {
    after.profile_v = structuredClone(profileOf(design, 'H'));
    remapVerticalFromHorizontal(after);
    return { after, notes: ['The vertical profile starts as a copy of the horizontal profile, so the geometry does not change.'] };
  }
  return { after, notes: [sameProfile(design.profile_h, design.profile_v)
    ? 'The stored vertical profile equals the horizontal profile, so the geometry does not change.'
    : 'Restores the stored vertical profile. The preview shows how the vertical plane changes.'] };
}

function remapVerticalFromHorizontal(design: DesignDocument): void {
  const prefix = 'profile_v.';
  const copy = (key: string) => key.startsWith('profile_h.') ? `profile_v.${key.slice('profile_h.'.length)}` : null;
  if (design._expressions) {
    const kept = Object.entries(design._expressions).filter(([key]) => !key.startsWith(prefix));
    const copied = Object.entries(design._expressions).flatMap(([key, value]) => { const next = copy(key); return next ? [[next, structuredClone(value)] as const] : []; });
    design._expressions = Object.fromEntries([...kept, ...copied]);
    if (!Object.keys(design._expressions).length) delete design._expressions;
  }
  if (design._absent) {
    design._absent = [...design._absent.filter((key) => !key.startsWith(prefix)), ...design._absent.flatMap((key) => copy(key) ?? [])];
    if (!design._absent.length) delete design._absent;
  }
}

/**
 * A new shared design's V is only a hidden seed until it is first separated.
 * The file format has no marker for that, so this remembers, for the open
 * document only, the V a new Custom profile started with. A reopened file's
 * V has unknown provenance and is treated as authored (the user chooses).
 */
const unseparatedVertical = new Map<number, string>();

export function rememberUnseparatedVertical(design: DesignDocument): void {
  if (isSharedProfile(design)) unseparatedVertical.set(currentDocumentLoad(), JSON.stringify(design.profile_v));
}

/** True while the stored V is still the untouched seed of a design created in this session. */
export function isUnseparatedVertical(design: DesignDocument): boolean {
  return isSharedProfile(design) && unseparatedVertical.get(currentDocumentLoad()) === JSON.stringify(design.profile_v);
}

/** Explicit, undoable reconciliation of a retained V throat with H. */
export function horizontalThroatForVertical(design: DesignDocument): ProfileEditResult {
  const after = structuredClone(design);
  after.profile_v!.points[0].r = profileOf(design, 'H').points[0].r;
  clearMetadata(after, ['profile_v.points.0.r']);
  return { after, notes: [`The stored vertical throat radius becomes ${formatMm(after.profile_v!.points[0].r)} mm; the rest of the vertical profile is unchanged.`] };
}

/**
 * Pasted rows as a complete table: a pasted z = 0 row is the throat and a
 * t = 1 row the mouth; otherwise the current endpoint is kept. Never truncates.
 */
export function normalizedImportedPoints(imported: FreeformPoint[], current: FreeformPoint[], plane: Plane = 'H'): FreeformPoint[] {
  const sorted = structuredClone(imported).sort((a, b) => a.t - b.t);
  const throat = sorted[0]?.t === 0 ? sorted.shift()! : structuredClone(current[0]);
  const mouth = sorted.at(-1)?.t === 1 ? sorted.pop()! : structuredClone(current.at(-1)!);
  const rows = [{ ...throat, t: 0 }, ...sorted.filter((point) => point.t > 0 && point.t < 1), { ...mouth, t: 1 }];
  if (rows.length > MAX_FREEFORM_ANCHORS) throw new ProfileEditRefusal(`The ${plane} profile would have ${rows.length} anchors; a Custom profile supports at most ${MAX_FREEFORM_ANCHORS} including the throat and mouth.`);
  return rows;
}

export interface PasteChoice {
  target: Plane | 'both';
  adoptLength: boolean;
  join: 'corner' | 'smooth';
}

export interface PastedProfile {
  points: FreeformPoint[];
  pointsByPlane?: { H: FreeformPoint[]; V: FreeformPoint[] };
  importedLength: number;
}

/** Replace one or both tables with pasted rows as one atomic edit. */
export function applyPastedProfile(design: DesignDocument, pasted: PastedProfile, choice: PasteChoice, curves?: CurvesResponse): ProfileEditResult {
  const shared = isSharedProfile(design);
  const target = shared ? 'H' : pasted.pointsByPlane ? 'both' : choice.target;
  const length = choice.adoptLength ? pasted.importedLength : lengthOf(design);
  if (!(length >= 20 && length <= 1_000)) throw new ProfileEditRefusal('Length must be between 20 and 1000 mm.');
  // Adopting the pasted length is a Length change for everything the paste
  // does not replace (the other plane, stations): it is the explicit Stretch.
  const adopting = choice.adoptLength && length !== lengthOf(design);
  if (adopting && !curves) throw new ProfileEditRefusal('Adopting the pasted length stretches the rest of the profile, which needs the resolved curve. Wait for the curve check and try again.');
  const stretched = adopting ? stretchLength(design, length, curves!) : undefined;
  const base = stretched?.after ?? design;
  const after = structuredClone(base);
  const notes: string[] = [];
  const replace = (plane: Plane, imported: FreeformPoint[]) => {
    const current = profileOf(base, plane).points;
    const rows = normalizedImportedPoints(imported, current, plane);
    const points = rows.map((point) => {
      const row: FreeformPoint = { t: point.t, r: point.r };
      if (present(point.angle_deg)) row.angle_deg = point.angle_deg;
      if (choice.join === 'corner') row.join = 'corner';
      return row;
    });
    after[PROFILE_KEY[plane]] = { ...profileOf(base, plane), points };
    remapPointMetadata(after, plane, current.length, () => null);
    notes.push(`${plane}: ${points.length} anchors as ${choice.join === 'corner' ? 'corners (adjacent corners without angles join with straight spans)' : 'smooth anchors with automatic tangents'}.`);
  };
  if (target === 'H' || target === 'both') replace('H', pasted.pointsByPlane?.H ?? pasted.points);
  if (target === 'V' || target === 'both') replace('V', pasted.pointsByPlane?.V ?? pasted.points);
  // One circular throat: the plane the paste set wins, and a separate other plane follows.
  if (!shared) {
    const radius = target === 'V' ? after.profile_v!.points[0].r : after.profile_h!.points[0].r;
    if (pasted.pointsByPlane && pasted.pointsByPlane.H[0]?.t === 0 && pasted.pointsByPlane.H[0].r !== pasted.pointsByPlane.V[0].r) {
      throw new ProfileEditRefusal('The throat must be one circle. Use the same throat radius in H and V at z = 0.');
    }
    if (after.profile_h!.points[0].r !== radius || after.profile_v!.points[0].r !== radius) notes.push(`Both planes use throat radius ${formatMm(radius)} mm.`);
    after.profile_h!.points[0].r = radius;
    after.profile_v!.points[0].r = radius;
  }
  if (adopting) {
    notes.push(`Length becomes ${formatMm(length)} mm (pasted). Everything the paste does not replace is stretched with it (Stretch profile): ${stretched!.notes.join(' ')}`);
  } else if (pasted.importedLength !== lengthOf(design)) {
    notes.push(`Pasted positions are stretched from ${formatMm(pasted.importedLength)} mm to the current Length ${formatMm(lengthOf(design))} mm.`);
  }
  return { after, notes };
}

export function formatMm(value: number): string {
  return Number(value.toPrecision(10)).toString();
}

export function formatAngle(value: number): string {
  return `${Number(value.toFixed(4))}°`;
}

/* ------------------------------------------------------------------ */
/* Keyed requests, preview and the single commit path                  */
/* ------------------------------------------------------------------ */

/** Document identity and revision an edit was proposed against. */
export interface EditOrigin { load: number; generation: number; revision: number }

export function currentEditOrigin(): EditOrigin {
  const state = useDesignStore.getState();
  return { load: currentDocumentLoad(), generation: state.loadGeneration, revision: state.designRevision };
}

export function isCurrentOrigin(origin: EditOrigin): boolean {
  const now = currentEditOrigin();
  return now.load === origin.load && now.generation === origin.generation && now.revision === origin.revision;
}

function hashText(text: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

let requestSequence = 0;
/** Content plus document identity/revision plus a per-request nonce. */
export function profileRequestKey(origin: EditOrigin, ...designs: DesignDocument[]): string {
  requestSequence += 1;
  const content = hashText(designs.map((design) => JSON.stringify(serializeDesign(design))).join('\u0000'));
  return `cp:${origin.load}.${origin.generation}.${origin.revision}:${requestSequence}:${content}`;
}

const COMMITTED_FIELDS = ['length', 'profile_h', 'profile_v', 'shared_profile', 'cross_sections'] as const;

/**
 * Commit a previewed candidate as one project Undo step.
 *
 * The values go in one step; the expression/absent sidecars, which ordinary
 * edits clear under every edited path, are then restored to the candidate's
 * re-keyed metadata without a history entry of their own.
 */
export function commitProfileEdit(origin: EditOrigin, before: DesignDocument, after: DesignDocument): void {
  if (!isCurrentOrigin(origin)) throw new ProfileEditRefusal('The design changed before this edit was accepted. Review it and try again.');
  const store = useDesignStore.getState();
  if (JSON.stringify(serializeDesign(store.design)) !== JSON.stringify(serializeDesign(before))) {
    throw new ProfileEditRefusal('The design changed before this edit was accepted. Review it and try again.');
  }
  const updates = Object.fromEntries(COMMITTED_FIELDS
    .filter((field) => JSON.stringify(before[field]) !== JSON.stringify(after[field]))
    .map((field) => [field, structuredClone(after[field])]));
  if (!Object.keys(updates).length) return;
  store.commitEmbedded(updates, 'step');
  const committed = useDesignStore.getState().design;
  if (JSON.stringify(committed._expressions) !== JSON.stringify(after._expressions)
    || JSON.stringify(committed._absent) !== JSON.stringify(after._absent)) {
    store.commitEmbedded({ _expressions: structuredClone(after._expressions), _absent: structuredClone(after._absent) }, 'amend');
  }
}

export type Pending<T> = { status: 'pending' } | { status: 'ready'; value: T } | { status: 'error'; message: string };

export interface ProfileEditPreview {
  label: string;
  origin: EditOrigin;
  status: 'building' | 'refused' | 'ready';
  message?: string;
  before?: DesignDocument;
  after?: DesignDocument;
  notes: string[];
  /** The candidate's own curve query: validation, straight spans, warnings. */
  validation: Pending<CurvesResponse>;
  curve: Pending<CompareResponse>;
  generated: Pending<GeneratedProfileChange>;
}

export type EditBuilder = (design: DesignDocument, context: { origin: EditOrigin; signal: AbortSignal }) => ProfileEditResult | Promise<ProfileEditResult>;

export interface ProfileEditFetchers {
  fetcher?: typeof fetch;
}

function messageOf(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

/** Preview, cancel and accept one edit at a time; stale results are dropped. */
export function useProfileEdit({ fetcher = fetch }: ProfileEditFetchers = {}) {
  const [preview, setPreview] = useState<ProfileEditPreview | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const active = useRef<ProfileEditPreview | null>(null);
  active.current = preview;
  const generation = useDesignStore((state) => state.loadGeneration);
  const revision = useDesignStore((state) => state.designRevision);

  const stop = useCallback(() => { controller.current?.abort(); controller.current = null; }, []);
  useEffect(() => stop, [stop]);
  // Any edit, Undo, Redo or Open after the proposal makes it stale.
  useEffect(() => {
    const current = active.current;
    if (current && !isCurrentOrigin(current.origin)) {
      stop();
      setPreview(null);
      setNotice('The preview was discarded because the design changed. Nothing was applied.');
    }
  }, [generation, revision, stop]);

  const propose = useCallback((label: string, build: EditBuilder) => {
    stop();
    const abort = new AbortController();
    controller.current = abort;
    const origin = currentEditOrigin();
    const before = structuredClone(useDesignStore.getState().design);
    const live = () => !abort.signal.aborted && isCurrentOrigin(origin);
    const update = (patch: Partial<ProfileEditPreview>) => { if (live()) setPreview((current) => current && current.origin === origin ? { ...current, ...patch } : current); };
    setNotice(null);
    setPreview({ label, origin, status: 'building', notes: [], validation: { status: 'pending' }, curve: { status: 'pending' }, generated: { status: 'pending' } });
    void (async () => {
      let result: ProfileEditResult;
      try { result = await build(before, { origin, signal: abort.signal }); }
      catch (reason) {
        if (reason instanceof StaleFreeformResponse) return;
        update({ status: 'refused', message: messageOf(reason) });
        return;
      }
      if (!live()) return;
      const { after, notes } = result;
      update({ status: 'ready', before, after, notes });
      const failed = <K extends 'validation' | 'curve' | 'generated'>(key: K) => (reason: unknown) => {
        if (reason instanceof StaleFreeformResponse || abort.signal.aborted) return;
        update({ [key]: { status: 'error', message: reason instanceof FreeformRequestError ? reason.message : messageOf(reason) } } as Partial<ProfileEditPreview>);
      };
      queryFreeformCurves(after, profileRequestKey(origin, after), abort.signal, fetcher)
        .then((value) => update({ validation: { status: 'ready', value } }), failed('validation'));
      compareFreeformProfiles(before, after, profileRequestKey(origin, before, after), abort.signal, fetcher)
        .then((value) => update({ curve: { status: 'ready', value } }), failed('curve'));
      Promise.all([
        inspectProfileSamples(before, origin.revision, abort.signal, fetcher),
        inspectProfileSamples(after, origin.revision, abort.signal, fetcher),
      ]).then(([first, second]) => update({ generated: { status: 'ready', value: compareProfileSamples(first, second) } }), failed('generated'));
    })();
  }, [fetcher, stop]);

  const cancel = useCallback(() => { stop(); setPreview(null); setNotice(null); }, [stop]);
  const accept = useCallback(() => {
    const current = active.current;
    if (!current || !previewAcceptable(current)) return;
    try {
      commitProfileEdit(current.origin, current.before!, current.after!);
      stop();
      setPreview(null);
      setNotice(`${current.label}: applied. Undo restores the previous profile.`);
    } catch (reason) {
      stop();
      setPreview(null);
      setNotice(messageOf(reason));
    }
  }, [stop]);
  return { preview, notice, propose, cancel, accept, dismissNotice: () => setNotice(null) };
}

/** Accept needs a valid candidate and both measurements; otherwise the old design stays. */
export function previewAcceptable(preview: ProfileEditPreview): boolean {
  return preview.status === 'ready' && preview.validation.status === 'ready'
    && preview.curve.status === 'ready' && preview.generated.status === 'ready' && isCurrentOrigin(preview.origin);
}

interface CurvesState { origin: EditOrigin; curves?: CurvesResponse; error?: string }
let sharedCurves: { origin: EditOrigin; promise: Promise<CurvesResponse> } | null = null;

/** One keyed curve query per document revision, shared by every table that shows it. */
function curvesFor(design: DesignDocument, origin: EditOrigin, fetcher: typeof fetch): Promise<CurvesResponse> {
  const same = sharedCurves && sharedCurves.origin.load === origin.load && sharedCurves.origin.generation === origin.generation && sharedCurves.origin.revision === origin.revision;
  if (!same || fetcher !== fetch) sharedCurves = { origin, promise: queryFreeformCurves(design, profileRequestKey(origin, design), undefined, fetcher) };
  return sharedCurves!.promise;
}

/**
 * The resolved curves of the design on screen (debounced). A previous answer
 * stays visible, marked stale, until the current revision's answer arrives;
 * answers and refusals for an older revision or another document are dropped.
 */
export function useCurrentProfileCurves(fetcher: typeof fetch = fetch): { curves?: CurvesResponse; error?: string; stale: boolean } {
  const design = useDesignStore((state) => state.design);
  const revision = useDesignStore((state) => state.designRevision);
  const generation = useDesignStore((state) => state.loadGeneration);
  const [state, setState] = useState<CurvesState | null>(null);
  useEffect(() => {
    if (design.formula !== 'FREEFORM') return undefined;
    const origin = currentEditOrigin();
    let cancelled = false;
    const timer = setTimeout(() => {
      curvesFor(design, origin, fetcher).then((curves) => {
        if (!cancelled && isCurrentOrigin(origin)) setState({ origin, curves });
      }, (reason: unknown) => {
        if (!cancelled && isCurrentOrigin(origin) && !(reason instanceof StaleFreeformResponse)) setState((previous) => ({ origin, curves: previous?.origin.generation === generation ? previous.curves : undefined, error: messageOf(reason) }));
      });
    }, 250);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [design, revision, generation, fetcher]);
  const sameDocument = state?.origin.generation === generation;
  const current = sameDocument && state?.origin.revision === revision;
  return { curves: sameDocument ? state?.curves : undefined, error: current ? state?.error : undefined, stale: !current };
}

/** Exact (default) or refit insertion through the server's insertion route. */
export function insertAnchorBuilder(plane: Plane, zMm: number, mode: InsertMode, fetcher: typeof fetch = fetch): EditBuilder {
  return async (design, { origin, signal }) => {
    const edited = isSharedProfile(design) ? 'H' : plane;
    const length = lengthOf(design);
    if (!(zMm > 0 && zMm < length)) throw new ProfileEditRefusal(`Insert z must be strictly between the throat (0) and the mouth (${formatMm(length)} mm).`);
    if (profileOf(design, edited).points.length >= MAX_FREEFORM_ANCHORS) throw new ProfileEditRefusal(`The ${edited} profile already has ${MAX_FREEFORM_ANCHORS} anchors, the most a Custom profile supports.`);
    const result = await insertFreeformAnchor(design, { plane: edited, z_mm: zMm, mode }, profileRequestKey(origin, design), signal, fetcher);
    if (!isCurrentOrigin(origin)) throw new StaleFreeformResponse('Discarded an insertion for an older design.');
    const after = withInsertedProfile(design, result.design, edited);
    const notes = mode === 'exact'
      ? [`Exact insertion at z = ${formatMm(zMm)} mm in ${edited}: the curve is split where it is, and the new anchor and any neighbouring automatic spans that would move get explicit handles.`]
      : [`Refit insertion at z = ${formatMm(zMm)} mm in ${edited}: every explicit handle in ${edited} is released and the plane is fitted automatically through its anchors.`];
    const pins = describePins(edited, profileOf(after, edited).points);
    return { after, notes: pins ? [...notes, pins] : notes };
  };
}
