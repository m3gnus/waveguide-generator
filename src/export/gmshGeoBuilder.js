import { parseNumberList } from '../geometry/common.js';
import { transformVerticesToAth } from '../geometry/transforms.js';

const DEFAULTS = Object.freeze({
  throatResolution: 5,
  mouthResolution: 8,
  rearResolution: 10,
  mshVersion: '2.2'
});

const TAG_NAMES = Object.freeze({
  1: 'SD1G0',
  2: 'SD1D1001',
  3: 'SD2G0',
  4: 'I1-2'
});

function toFinite(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function toPositive(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

function parseResolutionValue(value, fallback = null) {
  const numeric = Number(value);
  if (Number.isFinite(numeric) && numeric > 0) return numeric;

  const list = parseNumberList(value);
  if (!list || list.length === 0) return fallback;

  const positive = list.filter((v) => Number.isFinite(v) && v > 0);
  if (positive.length === 0) return fallback;
  return Math.min(...positive);
}

function gatherTriangleVertexUsage(indices, surfaceTags) {
  const usage = new Map();
  for (let i = 0; i < indices.length; i += 3) {
    const triTag = Number(surfaceTags[i / 3] || 1);
    const a = indices[i];
    const b = indices[i + 1];
    const c = indices[i + 2];

    [a, b, c].forEach((idx) => {
      let tagSet = usage.get(idx);
      if (!tagSet) {
        tagSet = new Set();
        usage.set(idx, tagSet);
      }
      tagSet.add(triTag);
    });
  }
  return usage;
}

function gatherSourceVertexIds(indices, surfaceTags) {
  const source = new Set();
  for (let i = 0; i < indices.length; i += 3) {
    if (Number(surfaceTags[i / 3]) !== 2) continue;
    source.add(indices[i]);
    source.add(indices[i + 1]);
    source.add(indices[i + 2]);
  }
  return source;
}

function gatherBandVertexIds(zList, lower, upper, allowedIndices = null) {
  const out = [];
  const eps = 1e-6;
  for (let i = 0; i < zList.length; i += 1) {
    if (allowedIndices && !allowedIndices.has(i)) continue;
    const z = zList[i];
    if (z >= lower - eps && z <= upper + eps) out.push(i);
  }
  return out;
}

function selectEnclosureVertices(vertexUsage) {
  const out = new Set();
  for (const [idx, tags] of vertexUsage.entries()) {
    if (tags.has(3) || tags.has(4)) out.add(idx);
  }
  return out;
}

function formatNumber(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '0';
  if (Math.abs(n) < 1e-12) return '0';
  return `${n}`;
}

function appendMeshOptions(lines, mshVersion) {
  lines.push('// Gmsh-authoritative mesh options');
  lines.push('Mesh.Algorithm = 6;');
  lines.push('Mesh.Algorithm3D = 5;');
  lines.push('Mesh.RecombinationAlgorithm = 1;');
  lines.push('Mesh.SubdivisionAlgorithm = 0;');
  lines.push('Mesh.Smoothing = 1;');
  lines.push('Mesh.MeshSizeFactor = 1;');
  lines.push('Mesh.MeshSizeMin = 0;');
  lines.push('Mesh.MeshSizeMax = 1e22;');
  lines.push('Mesh.ElementOrder = 1;');
  lines.push('Mesh.MeshSizeFromPoints = 1;');
  lines.push('Mesh.MeshSizeFromCurvature = 1;');
  lines.push('Mesh.MinimumCirclePoints = 12;');
  lines.push('Mesh.MeshSizeExtendFromBoundary = 1;');
  lines.push('Mesh.RecombineAll = 0;');
  lines.push(`Mesh.MshFileVersion = ${mshVersion};`);
  lines.push('General.Verbosity = 2;');
  lines.push('');
}

function appendPoints(lines, vertices, pointSizes) {
  lines.push('// Points');
  for (let i = 0; i < vertices.length; i += 3) {
    const pointId = i / 3 + 1;
    const x = formatNumber(vertices[i]);
    const y = formatNumber(vertices[i + 1]);
    const z = formatNumber(vertices[i + 2]);
    const lc = formatNumber(pointSizes[i / 3]);
    lines.push(`Point(${pointId}) = {${x}, ${y}, ${z}, ${lc}};`);
  }
  lines.push('');
}

function createGeoSurfaceBuilder() {
  const lineDefs = [];
  const surfaceDefs = [];
  const edgeMap = new Map();
  let nextLineId = 1;
  let nextLoopId = 1;
  let nextSurfaceId = 1;

  const getOrCreateLineRef = (from, to) => {
    const lo = Math.min(from, to);
    const hi = Math.max(from, to);
    const key = `${lo}_${hi}`;

    let line = edgeMap.get(key);
    if (!line) {
      line = {
        id: nextLineId++,
        from: lo + 1,
        to: hi + 1,
        canonicalFrom: lo,
        canonicalTo: hi
      };
      edgeMap.set(key, line);
      lineDefs.push(`Line(${line.id}) = {${line.from}, ${line.to}};`);
    }

    const sameDirection = from === line.canonicalFrom && to === line.canonicalTo;
    return sameDirection ? line.id : -line.id;
  };

  const addSurfaceFromCycle = (pointCycle, surfaceType = 'Plane Surface') => {
    if (!Array.isArray(pointCycle) || pointCycle.length < 3) return null;
    const refs = [];
    for (let i = 0; i < pointCycle.length; i += 1) {
      const from = pointCycle[i];
      const to = pointCycle[(i + 1) % pointCycle.length];
      refs.push(getOrCreateLineRef(from, to));
    }

    const loopId = nextLoopId++;
    const surfaceId = nextSurfaceId++;
    surfaceDefs.push(`Curve Loop(${loopId}) = {${refs.join(', ')}};`);
    surfaceDefs.push(`${surfaceType}(${surfaceId}) = {${loopId}};`);
    return surfaceId;
  };

  const appendTo = (lines) => {
    lines.push('// Curves');
    for (let i = 0; i < lineDefs.length; i += 1) lines.push(lineDefs[i]);
    lines.push('');
    lines.push('// Surfaces');
    for (let i = 0; i < surfaceDefs.length; i += 1) lines.push(surfaceDefs[i]);
    lines.push('');
  };

  return {
    addSurfaceFromCycle,
    appendTo,
    getCounts() {
      return {
        curveCount: nextLineId - 1,
        surfaceCount: nextSurfaceId - 1
      };
    }
  };
}

function formatGeoEntitySet(entries, perLine = 24) {
  if (!entries || entries.length === 0) return '';
  if (entries.length <= perLine) return entries.join(', ');

  const chunks = [];
  for (let i = 0; i < entries.length; i += perLine) {
    chunks.push(`  ${entries.slice(i, i + perLine).join(', ')}`);
  }
  return `\n${chunks.join(',\n')}\n`;
}

function encodeEntityRanges(ids) {
  const uniqueSorted = Array.from(new Set(
    ids
      .map((value) => Number(value))
      .filter((value) => Number.isFinite(value) && value > 0)
      .map((value) => Math.trunc(value))
  )).sort((a, b) => a - b);

  if (uniqueSorted.length === 0) return '';

  const entries = [];
  let start = uniqueSorted[0];
  let end = uniqueSorted[0];

  for (let i = 1; i < uniqueSorted.length; i += 1) {
    const current = uniqueSorted[i];
    if (current === end + 1) {
      end = current;
      continue;
    }
    entries.push(start === end ? `${start}` : `${start}:${end}`);
    start = current;
    end = current;
  }
  entries.push(start === end ? `${start}` : `${start}:${end}`);

  return formatGeoEntitySet(entries);
}

function appendTaggedSurface(taggedSurfaces, tag, surfaceId) {
  const cleanTag = Number(tag);
  if (!TAG_NAMES[cleanTag] || !Number.isFinite(surfaceId)) return;
  if (!taggedSurfaces.has(cleanTag)) taggedSurfaces.set(cleanTag, []);
  taggedSurfaces.get(cleanTag).push(surfaceId);
}

function findSourceCenterVertex(indices, surfaceTags) {
  const counts = new Map();
  let sourceTriCount = 0;
  for (let i = 0; i < indices.length; i += 3) {
    if (Number(surfaceTags[i / 3]) !== 2) continue;
    sourceTriCount += 1;
    const a = indices[i];
    const b = indices[i + 1];
    const c = indices[i + 2];
    counts.set(a, (counts.get(a) || 0) + 1);
    counts.set(b, (counts.get(b) || 0) + 1);
    counts.set(c, (counts.get(c) || 0) + 1);
  }

  if (sourceTriCount === 0) return null;
  let best = null;
  let bestCount = -1;
  for (const [vertexId, count] of counts.entries()) {
    if (count === sourceTriCount && count > bestCount) {
      best = vertexId;
      bestCount = count;
    }
  }
  return best;
}

function emitSurfaceGeometry(lines, context) {
  const {
    vertices,
    indices,
    surfaceTags,
    ringCount,
    lengthSteps,
    fullCircle
  } = context;

  const taggedSurfaces = new Map();
  const surfaceBuilder = createGeoSurfaceBuilder();
  const vertexCount = Math.floor(vertices.length / 3);
  const cleanRingCount = Math.max(0, Math.round(Number(ringCount)));
  const cleanLengthSteps = Math.max(0, Math.round(Number(lengthSteps)));
  const hornVertexCount = cleanRingCount * (cleanLengthSteps + 1);
  const radialSteps = fullCircle ? cleanRingCount : Math.max(0, cleanRingCount - 1);

  const hasStructuredHorn = cleanRingCount >= 2
    && cleanLengthSteps >= 1
    && radialSteps >= 1
    && hornVertexCount <= vertexCount;

  let hasStructuredSource = false;

  if (hasStructuredHorn) {
    for (let j = 0; j < cleanLengthSteps; j += 1) {
      const row1 = j * cleanRingCount;
      const row2 = (j + 1) * cleanRingCount;
      for (let i = 0; i < radialSteps; i += 1) {
        const i2 = fullCircle ? (i + 1) % cleanRingCount : i + 1;
        const p00 = row1 + i;
        const p01 = row1 + i2;
        const p11 = row2 + i2;
        const p10 = row2 + i;
        const surfaceId = surfaceBuilder.addSurfaceFromCycle([p00, p01, p11, p10], 'Ruled Surface');
        appendTaggedSurface(taggedSurfaces, 1, surfaceId);
      }
    }

    const sourceCenter = findSourceCenterVertex(indices, surfaceTags);
    const sourceSegments = fullCircle ? cleanRingCount : Math.max(0, cleanRingCount - 1);
    if (Number.isInteger(sourceCenter) && sourceCenter >= 0 && sourceCenter < vertexCount && sourceSegments > 0) {
      for (let i = 0; i < sourceSegments; i += 1) {
        const a = fullCircle ? (i + 1) % cleanRingCount : i + 1;
        const b = i;
        const surfaceId = surfaceBuilder.addSurfaceFromCycle([sourceCenter, a, b], 'Plane Surface');
        appendTaggedSurface(taggedSurfaces, 2, surfaceId);
      }
      hasStructuredSource = true;
    }
  }

  for (let i = 0; i < indices.length; i += 3) {
    const triTag = Number(surfaceTags[i / 3] || 1);
    const a = indices[i];
    const b = indices[i + 1];
    const c = indices[i + 2];

    const triangleIsInHornVertexGrid = hasStructuredHorn
      && a < hornVertexCount
      && b < hornVertexCount
      && c < hornVertexCount;
    if (triangleIsInHornVertexGrid) continue;
    if (hasStructuredSource && triTag === 2) continue;

    const surfaceId = surfaceBuilder.addSurfaceFromCycle([a, b, c], 'Plane Surface');
    appendTaggedSurface(taggedSurfaces, triTag, surfaceId);
  }

  surfaceBuilder.appendTo(lines);
  const counts = surfaceBuilder.getCounts();
  return {
    taggedSurfaces,
    curveCount: counts.curveCount,
    surfaceCount: counts.surfaceCount
  };
}

function appendPhysicalGroups(lines, taggedSurfaces) {
  lines.push('// Physical groups (canonical tag contract)');
  const orderedTags = [1, 2, 3, 4];
  orderedTags.forEach((tag) => {
    const surfaces = taggedSurfaces.get(tag);
    if (!surfaces || surfaces.length === 0) return;
    lines.push(`Physical Surface("${TAG_NAMES[tag]}", ${tag}) = {${encodeEntityRanges(surfaces)}};`);
  });
  lines.push('');
}

function appendResolutionFields(lines, context) {
  const {
    sourcePointIds,
    frontPointIds,
    backPointIds,
    rearPointIds,
    throatResolution,
    mouthResolution,
    frontResolution,
    backResolution,
    rearResolution,
    span,
    zMin,
    zMax
  } = context;

  let fieldId = 1;
  const sizeFieldIds = [];

  const addDistanceThreshold = (pointIds, sizeMin, sizeMax, distMax) => {
    if (!Array.isArray(pointIds) || pointIds.length === 0) return null;
    const cleanMin = toPositive(sizeMin, 1);
    const cleanMax = Math.max(cleanMin, toPositive(sizeMax, cleanMin));
    const cleanDistMax = Math.max(1e-6, Number(distMax));

    const distanceFieldId = fieldId++;
    const thresholdFieldId = fieldId++;
    lines.push(`Field[${distanceFieldId}] = Distance;`);
    lines.push(`Field[${distanceFieldId}].PointsList = {${pointIds.join(', ')}};`);
    lines.push(`Field[${thresholdFieldId}] = Threshold;`);
    lines.push(`Field[${thresholdFieldId}].InField = ${distanceFieldId};`);
    lines.push(`Field[${thresholdFieldId}].SizeMin = ${formatNumber(cleanMin)};`);
    lines.push(`Field[${thresholdFieldId}].SizeMax = ${formatNumber(cleanMax)};`);
    lines.push(`Field[${thresholdFieldId}].DistMin = 0;`);
    lines.push(`Field[${thresholdFieldId}].DistMax = ${formatNumber(cleanDistMax)};`);
    return thresholdFieldId;
  };

  lines.push('// Mesh size fields');
  const axialSpan = Math.max(1e-6, zMax - zMin);
  const axialFieldId = fieldId++;
  const axialBase = toPositive(throatResolution, 1);
  const axialDelta = mouthResolution - axialBase;
  lines.push(`Field[${axialFieldId}] = MathEval;`);
  lines.push(
    `Field[${axialFieldId}].F = "${formatNumber(axialBase)} + (${formatNumber(axialDelta)}) * ((z - (${formatNumber(zMin)})) / ${formatNumber(axialSpan)})";`
  );
  sizeFieldIds.push(axialFieldId);

  const sourceRefine = addDistanceThreshold(
    sourcePointIds,
    throatResolution,
    mouthResolution,
    Math.max(1, span * 0.2)
  );
  if (sourceRefine) sizeFieldIds.push(sourceRefine);

  if (Number.isFinite(frontResolution) && frontResolution > 0) {
    const frontSize = addDistanceThreshold(
      frontPointIds,
      frontResolution,
      mouthResolution,
      Math.max(1, span * 0.3)
    );
    if (frontSize) sizeFieldIds.push(frontSize);
  }

  if (Number.isFinite(backResolution) && backResolution > 0) {
    const backSize = addDistanceThreshold(
      backPointIds,
      backResolution,
      mouthResolution,
      Math.max(1, span * 0.3)
    );
    if (backSize) sizeFieldIds.push(backSize);
  }

  if (Number.isFinite(rearResolution) && rearResolution > 0) {
    const rearSize = addDistanceThreshold(
      rearPointIds,
      rearResolution,
      mouthResolution,
      Math.max(1, span * 0.2)
    );
    if (rearSize) sizeFieldIds.push(rearSize);
  }

  if (sizeFieldIds.length > 0) {
    const minFieldId = fieldId++;
    lines.push(`Field[${minFieldId}] = Min;`);
    lines.push(`Field[${minFieldId}].FieldsList = {${sizeFieldIds.join(', ')}};`);
    lines.push(`Background Field = ${minFieldId};`);
  }

  lines.push('');
  return sizeFieldIds.length;
}

function buildPointSizes(vertices, zMin, zMax, throatResolution, mouthResolution) {
  const out = new Array(vertices.length / 3).fill(mouthResolution);
  const span = Math.max(1e-6, zMax - zMin);

  for (let i = 0; i < vertices.length; i += 3) {
    const idx = i / 3;
    const z = vertices[i + 2];
    const t = Math.max(0, Math.min(1, (z - zMin) / span));
    out[idx] = throatResolution + (mouthResolution - throatResolution) * t;
  }
  return out;
}

export function buildGmshGeo(preparedParams, mesh, simulation, options = {}) {
  if (!mesh || !simulation) {
    throw new Error('buildGmshGeo requires mesh and simulation artifacts.');
  }

  // The simulation payload is canonical for export (it may filter split-plane faces).
  const sourceVertices = Array.from(simulation.vertices || mesh.vertices || []);
  const indices = Array.from(simulation.indices || mesh.indices || []);
  const surfaceTags = Array.from(simulation.surfaceTags || []);

  if (sourceVertices.length === 0 || indices.length === 0) {
    throw new Error('Cannot generate .geo: geometry is empty.');
  }
  if (indices.length % 3 !== 0) {
    throw new Error('Cannot generate .geo: indices length must be divisible by 3.');
  }
  if (surfaceTags.length !== indices.length / 3) {
    throw new Error('Cannot generate .geo: surfaceTags length mismatch.');
  }

  const verticalOffset = toFinite(simulation.metadata?.verticalOffset ?? preparedParams.verticalOffset, 0);
  const vertices = transformVerticesToAth(sourceVertices, { verticalOffset, offsetSign: 1 });

  const zValues = [];
  for (let i = 2; i < vertices.length; i += 3) zValues.push(vertices[i]);
  const zMin = Math.min(...zValues);
  const zMax = Math.max(...zValues);
  const span = Math.max(1e-6, zMax - zMin);

  const throatResolution = toPositive(preparedParams.throatResolution, DEFAULTS.throatResolution);
  const mouthResolution = toPositive(preparedParams.mouthResolution, DEFAULTS.mouthResolution);
  const rearResolution = toPositive(preparedParams.rearResolution, DEFAULTS.rearResolution);
  const frontResolution = parseResolutionValue(preparedParams.encFrontResolution, null);
  const backResolution = parseResolutionValue(preparedParams.encBackResolution, null);

  const pointSizes = buildPointSizes(vertices, zMin, zMax, throatResolution, mouthResolution);
  const vertexUsage = gatherTriangleVertexUsage(indices, surfaceTags);
  const enclosureVertices = selectEnclosureVertices(vertexUsage);
  const sourceVertexIds = gatherSourceVertexIds(indices, surfaceTags);
  const ringCount = toFinite(simulation.metadata?.ringCount ?? mesh.ringCount, 0);
  const fullCircle = Boolean(simulation.metadata?.fullCircle ?? mesh.fullCircle);
  const lengthSteps = toPositive(simulation.metadata?.lengthSteps ?? preparedParams.lengthSegments, 0);

  const frontBand = Math.max(1, span * 0.05);
  const backBand = Math.max(1, span * 0.05);
  const rearBand = Math.max(1, span * 0.03);

  const frontVertexIds = gatherBandVertexIds(
    zValues,
    zMax - frontBand,
    zMax,
    enclosureVertices.size > 0 ? enclosureVertices : null
  );

  const backVertexIds = gatherBandVertexIds(
    zValues,
    zMin,
    zMin + backBand,
    enclosureVertices.size > 0 ? enclosureVertices : null
  );

  const rearVertexIds = gatherBandVertexIds(zValues, zMin, zMin + rearBand, null);

  const lines = [];
  appendMeshOptions(lines, options.mshVersion || DEFAULTS.mshVersion);
  appendPoints(lines, vertices, pointSizes);
  const surfaceGeo = emitSurfaceGeometry(lines, {
    vertices,
    indices,
    surfaceTags,
    ringCount,
    lengthSteps,
    fullCircle
  });
  appendPhysicalGroups(lines, surfaceGeo.taggedSurfaces);

  const fieldCount = appendResolutionFields(lines, {
    sourcePointIds: Array.from(sourceVertexIds).map((v) => v + 1),
    frontPointIds: frontVertexIds.map((v) => v + 1),
    backPointIds: backVertexIds.map((v) => v + 1),
    rearPointIds: rearVertexIds.map((v) => v + 1),
    throatResolution,
    mouthResolution,
    frontResolution,
    backResolution,
    rearResolution,
    span,
    zMin,
    zMax
  });

  lines.push('Mesh 2;');

  return {
    geoText: `${lines.join('\n')}\n`,
    geoStats: {
      pointCount: vertices.length / 3,
      curveCount: surfaceGeo.curveCount,
      surfaceCount: surfaceGeo.surfaceCount,
      fieldCount,
      zMin,
      zMax,
      throatResolution,
      mouthResolution,
      frontResolution,
      backResolution,
      rearResolution
    }
  };
}
