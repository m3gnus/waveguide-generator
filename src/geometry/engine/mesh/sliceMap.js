import { resolveOsseLengthConfig } from '../profiles/osse.js';

function buildResolutionMap(lengthSteps, resT, resM) {
  if (!Number.isFinite(resT) || !Number.isFinite(resM) || resT <= 0 || resM <= 0) return null;
  if (Math.abs(resT - resM) <= 0.01) return null;

  const avgRes = 0.5 * (resT + resM);
  return Array.from({ length: lengthSteps + 1 }, (_, j) => {
    const t = j / lengthSteps;
    return (resT * t + 0.5 * (resM - resT) * t * t) / avgRes;
  });
}

function buildThroatSegmentMap(lengthSteps, throatSegments, extLen, slotLen, totalLength) {
  if (throatSegments <= 0 || throatSegments >= lengthSteps) return null;

  const extFraction = (extLen + slotLen) / totalLength;

  if (extFraction <= 0 || extFraction >= 1) return null;

  return Array.from({ length: lengthSteps + 1 }, (_, j) => {
    if (j <= throatSegments) {
      return extFraction * (j / throatSegments);
    }
    const t = (j - throatSegments) / (lengthSteps - throatSegments);
    return extFraction + (1 - extFraction) * t;
  });
}

export function buildSliceMap(params, lengthSteps) {
  // If an explicit slice density override is provided, use it directly.
  // This decouples viewport axial distribution from the BEM element-size params.
  const density = parseFloat(params.throatSliceDensity);
  if (Number.isFinite(density) && density > 0 && density < 1) {
    return buildResolutionMap(lengthSteps, density, 1.0 - density);
  }

  const resT = Number(params.throatResolution);
  const resM = Number(params.mouthResolution);
  const resolutionMap = buildResolutionMap(lengthSteps, resT, resM);
  if (resolutionMap) return resolutionMap;

  const throatSegments = Number(params.throatSegments || 0);
  const { extLen, slotLen, totalLength } = resolveOsseLengthConfig(params, 0);

  return buildThroatSegmentMap(lengthSteps, throatSegments, extLen, slotLen, totalLength);
}
