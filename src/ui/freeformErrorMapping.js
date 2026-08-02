import { normalizeAnchorList, normalizeStations } from '../config/freeformModel.js';

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function compactReason(detail, locator) {
  const lowerDetail = String(detail || '').toLowerCase();
  const lowerLocator = String(locator || '').toLowerCase();
  const tMatch = String(detail || '').match(/near\s+t=([+-]?(?:\d+\.?\d*|\.\d+))/i);
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

  const spanMatch = detail.match(/crossSections span\s+(\d+)\.\.(\d+)/i);
  let candidates = stations.map((station, index) => ({ station, index }));
  if (spanMatch) {
    const first = Number(spanMatch[1]);
    const second = Number(spanMatch[2]);
    const spanStart = stations[first]?.t;
    const spanEnd = stations[second]?.t;
    if (
      !Number.isFinite(spanStart) ||
      !Number.isFinite(spanEnd) ||
      t < Math.min(spanStart, spanEnd) ||
      t > Math.max(spanStart, spanEnd)
    ) {
      return null;
    }
    candidates = candidates.filter(({ index }) => index === first || index === second);
  }

  return candidates.reduce((nearest, candidate) => {
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

  const nearTMatch = detail.match(
    /(?:near|binding|at(?: station)?)\s+t=([+-]?(?:\d+\.?\d*|\.\d+))/i
  );
  if (nearTMatch) {
    const t = finiteNumber(nearTMatch[1]);
    const index = t === null ? null : nearestStationForT(detail, params, t);
    if (index !== null && index !== undefined) {
      return stationTarget(index, detail, nearTMatch[0]);
    }
  }

  return null;
}
