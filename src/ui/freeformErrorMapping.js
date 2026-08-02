import { normalizeAnchorList, normalizeStations } from '../config/freeformModel.js';

export const FREEFORM_NUMERIC_TOKEN = '[+-]?(?:(?:\\d+(?:\\.\\d*)?)|(?:\\.\\d+))(?:[eE][+-]?\\d+)?';

function numericCapture(prefix) {
  return new RegExp(`${prefix}(${FREEFORM_NUMERIC_TOKEN})(?![\\w.])`, 'i');
}

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function compactReason(detail, locator) {
  const lowerDetail = String(detail || '').toLowerCase();
  const lowerLocator = String(locator || '').toLowerCase();
  const tMatch = String(detail || '').match(numericCapture('near\\s+t='));
  if (lowerDetail.includes('non-convex outline')) {
    return `Non-convex outline${tMatch ? ` near t=${tMatch[1]}.` : '.'}`;
  }
  if (lowerLocator.includes('cornerradiusmm')) {
    return lowerDetail.includes('exceeds')
      ? 'Corner radius exceeds the local limit.'
      : 'Corner radius is outside the allowed range.';
  }
  if (lowerLocator.includes('angledeg')) return 'Tangent angle is outside the allowed range.';
  if (lowerLocator.includes('strength')) return 'Tangent strength is outside the allowed range.';
  if (lowerDetail.includes('radius overshoots')) return 'Curve radius overshoots this segment.';
  if (lowerDetail.includes('folds backward') || lowerDetail.includes("z'(u)=0")) {
    return 'Curve folds backward in this segment.';
  }

  let reason = String(detail || '').trim();
  reason = reason.replace(/^FREEFORM\s+/i, '');
  if (locator) reason = reason.replace(locator, '').replace(/^[\s.:=;-]+/, '');
  reason = reason.replace(/;\s+.*$/, '').trim();
  if (!reason) return 'Invalid FREEFORM value.';
  return `${reason.charAt(0).toUpperCase()}${reason.slice(1)}`;
}

function stationTarget(index, detail, locator) {
  return {
    target: { kind: 'station', index },
    message: `Station ${index + 1}: ${compactReason(detail, locator)}`,
    detail,
  };
}

function anchorTarget(plane, anchorIndex, detail, locator, label = 'anchor') {
  return {
    target: { kind: 'anchor', plane, anchorIndex },
    message: `${plane} ${label} ${anchorIndex + 1}: ${compactReason(detail, locator)}`,
    detail,
  };
}

function nearestStationForT(detail, params, t) {
  const stations = normalizeStations(params?.crossSections);
  if (stations.length < 2 || t < stations[0].t || t > stations.at(-1).t) return null;

  const spanMatch = detail.match(
    new RegExp(
      `crossSections span\\s+(${FREEFORM_NUMERIC_TOKEN})\\.\\.(${FREEFORM_NUMERIC_TOKEN})(?![\\w.])`,
      'i'
    )
  );
  if (spanMatch) {
    const spanStart = Number(spanMatch[1]);
    const spanEnd = Number(spanMatch[2]);
    if (
      !Number.isFinite(spanStart) ||
      !Number.isFinite(spanEnd) ||
      t < Math.min(spanStart, spanEnd) ||
      t > Math.max(spanStart, spanEnd)
    ) {
      return null;
    }
    const epsilon = 1e-9;
    const firstIndex = stations.findIndex((station) => Math.abs(station.t - spanStart) <= epsilon);
    const secondIndex = stations.findIndex((station) => Math.abs(station.t - spanEnd) <= epsilon);
    if (firstIndex < 0 || secondIndex < 0 || Math.abs(secondIndex - firstIndex) !== 1) return null;
    // A station defines the transition from the preceding station, so span
    // diagnostics belong to the station at the mouth-side end of the span.
    return spanEnd >= spanStart ? secondIndex : firstIndex;
  }

  return stations
    .map((station, index) => ({ station, index }))
    .reduce((nearest, candidate) => {
      const distance = Math.abs(candidate.station.t - t);
      return !nearest || distance < nearest.distance ? { ...candidate, distance } : nearest;
    }, null)?.index;
}

/**
 * Turn the mesher's FREEFORM validation detail into one UI target. The full
 * backend detail is retained while `message` is intentionally concise.
 */
export function mapFreeformError(detailValue, params = {}) {
  const detail = String(detailValue || '').trim();
  if (!detail) return null;

  const stationMatch = detail.match(/crossSections\[(\d+)\](?:\.\w+)?/i);
  if (stationMatch) {
    const index = Number(stationMatch[1]);
    return stationTarget(index, detail, stationMatch[0]);
  }

  const anchorMatch = detail.match(/profile([HV])\.points\[(\d+)\](?:\.\w+)?/i);
  if (anchorMatch) {
    const plane = anchorMatch[1].toUpperCase();
    const anchorIndex = Number(anchorMatch[2]);
    return anchorTarget(plane, anchorIndex, detail, anchorMatch[0]);
  }

  const segmentMatch = detail.match(/profile([HV]) segment\s+(\d+)/i);
  if (segmentMatch) {
    const plane = segmentMatch[1].toUpperCase();
    const segment = Number(segmentMatch[2]);
    const length = finiteNumber(params?.length) ?? 120;
    const interior = normalizeAnchorList(params?.[`interior${plane}`], { length });
    const anchorIndex = Math.min(segment + 1, interior.length + 1);
    return anchorTarget(plane, anchorIndex, detail, segmentMatch[0], 'curve near anchor');
  }

  const nearTMatch = detail.match(numericCapture('(?:near|binding|at(?: station)?)\\s+t='));
  if (nearTMatch) {
    const t = finiteNumber(nearTMatch[1]);
    const index = t === null ? null : nearestStationForT(detail, params, t);
    if (index !== null && index !== undefined) {
      return stationTarget(index, detail, nearTMatch[0]);
    }
  }

  return null;
}
