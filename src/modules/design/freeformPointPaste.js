import { MAX_INTERIOR_ANCHORS, normalizeAnchorList } from '../../config/freeformModel.js';
import { decimateMeridian } from './convertToFreeform.js';

const PROFILE_HEADER = '# z_cm;r_h_cm;r_v_cm';
const FUSION_HEADER = '# x_cm;y_cm;z_cm';
const SLICES_HEADER = '# slices: x_cm;y_cm;z_cm';
function endpointEpsilonMm(length) {
  return Math.max(1e-3, 1e-6 * Math.abs(Number(length) || 0));
}

function withinEndpoint(value, endpoint, length) {
  return Math.abs(value - endpoint) <= endpointEpsilonMm(length) + 1e-9;
}

function finiteColumns(tokens, lineNumber) {
  const emptyIndex = tokens.findIndex((token) => String(token).trim() === '');
  if (emptyIndex >= 0) {
    return {
      error: `Line ${lineNumber}: column ${emptyIndex + 1} must be a finite number.`,
    };
  }
  const values = tokens.map((token) => Number(String(token).trim()));
  const invalidIndex = values.findIndex((value) => !Number.isFinite(value));
  if (invalidIndex >= 0) {
    return {
      error: `Line ${lineNumber}: column ${invalidIndex + 1} must be a finite number.`,
    };
  }
  return { values };
}

function headerTokens(line) {
  return line
    .replace(/^#\s*/, '')
    .split(/[;,\s]+/)
    .map((token) => token.trim().toLowerCase())
    .filter(Boolean);
}

function headerKind(line) {
  const normalized = line.replace(/^#\s*/, '').trim().toLowerCase();
  if (/^slices\s*:/.test(normalized)) return 'slices';
  const tokens = headerTokens(line);
  if (tokens.join('|') === 'x_cm|y_cm|z_cm') return 'fusion-angular';
  if (tokens.join('|') === 'z_cm|r_h_cm|r_v_cm') return 'compact-hv';
  if (tokens.length === 0) return false;
  return tokens.every((token) =>
    /^(?:z|x|y|r|rh|rv|r_h|r_v|depth|radius|angle|angledeg|angle_deg|strength)(?:_(?:cm|mm|deg))?$/.test(
      token
    )
  )
    ? 'generic'
    : null;
}

function errorResult(message, rowCount = 0) {
  return {
    ok: false,
    error: message,
    rowCount,
    format: null,
    layout: null,
    points: [],
    pointsByPlane: null,
    supportsBoth: false,
  };
}

function chooseAxisSection(sections, axis) {
  let best = null;
  let bestCrossRatio = Number.POSITIVE_INFINITY;
  let bestAxisMagnitude = Number.NEGATIVE_INFINITY;
  for (const section of sections) {
    if (section.length === 0) continue;
    const axisMagnitude =
      section.reduce((sum, row) => sum + Math.abs(axis === 'H' ? row[0] : row[1]), 0) /
      section.length;
    const crossMagnitude =
      section.reduce((sum, row) => sum + Math.abs(axis === 'H' ? row[1] : row[0]), 0) /
      section.length;
    const crossRatio = crossMagnitude / Math.max(axisMagnitude, Number.EPSILON);
    if (
      crossRatio < bestCrossRatio - 1e-9 ||
      (Math.abs(crossRatio - bestCrossRatio) <= 1e-9 && axisMagnitude > bestAxisMagnitude)
    ) {
      best = section;
      bestCrossRatio = crossRatio;
      bestAxisMagnitude = axisMagnitude;
    }
  }
  return best || [];
}

function parseFusionSections(sections, plane) {
  if (sections.length < 2) {
    return errorResult('Fusion profile CSV must contain at least two angular profile sections.');
  }
  const horizontal = chooseAxisSection(sections, 'H');
  const vertical = chooseAxisSection(sections, 'V');
  if (horizontal.length === 0 || vertical.length === 0) {
    return errorResult('Fusion profile CSV does not contain usable H and V profile sections.');
  }
  const toPoints = (section) =>
    section.map(([x, y, z]) => ({
      z: z * 10,
      r: Math.hypot(x, y) * 10,
      angleDeg: null,
      strength: null,
    }));
  const pointsByPlane = { H: toPoints(horizontal), V: toPoints(vertical) };
  const normalizedPlane = String(plane).toUpperCase() === 'V' ? 'V' : 'H';
  return {
    ok: true,
    error: null,
    rowCount: Math.min(pointsByPlane.H.length, pointsByPlane.V.length),
    format: 'profile-csv',
    layout: 'fusion-angular',
    points: pointsByPlane[normalizedPlane],
    pointsByPlane,
    supportsBoth: true,
  };
}

/** Parse the point formats accepted by the FREEFORM H/V table paste control. */
export function parseFreeformPointPaste(text, { plane = 'H' } = {}) {
  const source = String(text ?? '');
  const lines = source.split(/\r?\n/);
  let expectedColumns = null;
  let format = null;
  let layout = null;
  let sawFusionHeader = false;
  const rows = [];
  const fusionSections = [[]];

  for (let index = 0; index < lines.length; index += 1) {
    const lineNumber = index + 1;
    const line = lines[index].trim();
    if (!line) {
      if (sawFusionHeader && fusionSections.at(-1).length > 0) fusionSections.push([]);
      continue;
    }
    if (line.startsWith('#')) {
      const kind = headerKind(line);
      if (kind === 'slices') {
        return errorResult('This is a slices export; paste a profiles export instead.');
      }
      if (kind === 'fusion-angular') {
        sawFusionHeader = true;
        format = 'profile-csv';
        layout = 'fusion-angular';
      } else if (kind === 'compact-hv') {
        format = 'profile-csv';
        layout = 'compact-hv';
      }
      continue;
    }
    const kind = headerKind(line);
    if (kind) {
      if (kind === 'compact-hv') {
        format = 'profile-csv';
        layout = 'compact-hv';
      }
      continue;
    }

    const semicolon = line.includes(';');
    const tokens = semicolon
      ? line.split(';').map((token) => token.trim())
      : line.includes(',')
        ? line.split(',').map((token) => token.trim())
        : line.split(/\s+/);
    const parsed = finiteColumns(tokens, lineNumber);
    if (parsed.error) return errorResult(parsed.error, rows.length);

    if (sawFusionHeader) {
      if (!semicolon || parsed.values.length !== 3) {
        return errorResult(`Line ${lineNumber}: expected 3 semicolon-separated CSV columns.`);
      }
      fusionSections.at(-1).push(parsed.values);
      continue;
    }

    const columnCount = parsed.values.length;
    if (expectedColumns === null) {
      if (semicolon && columnCount === 3) {
        expectedColumns = 3;
        format = 'profile-csv';
        layout = 'compact-hv';
      } else if (!semicolon && [2, 3, 4].includes(columnCount)) {
        expectedColumns = columnCount;
        format =
          columnCount === 2 ? 'two-column' : columnCount === 3 ? 'three-column' : 'four-column';
      } else {
        return errorResult(
          `Line ${lineNumber}: expected 2, 3, or 4 numeric columns, or 3 semicolon-separated CSV columns.`
        );
      }
    }
    const expectsSemicolon = format === 'profile-csv' && layout === 'compact-hv';
    if (columnCount !== expectedColumns || expectsSemicolon !== semicolon) {
      return errorResult(
        `Line ${lineNumber}: expected ${expectedColumns} ${
          expectsSemicolon ? 'semicolon-separated ' : ''
        }columns to match the first data row.`,
        rows.length
      );
    }

    if (expectedColumns === 3) {
      if (semicolon) {
        const [z, rH, rV] = parsed.values.map((value) => value * 10);
        if (z < 0) return errorResult(`Line ${lineNumber}: z must be at least 0.`, rows.length);
        if (!(rH > 0) || !(rV > 0)) {
          return errorResult(`Line ${lineNumber}: radii must be greater than 0.`, rows.length);
        }
        rows.push({ z, rH, rV, lineNumber });
        continue;
      }
    }

    const [z, r, angleDeg = null, strength = null] = parsed.values;
    if (z < 0) return errorResult(`Line ${lineNumber}: z must be at least 0.`, rows.length);
    if (!(r > 0)) {
      return errorResult(`Line ${lineNumber}: radius must be greater than 0.`, rows.length);
    }
    if (angleDeg !== null && (angleDeg < -90 || angleDeg > 90)) {
      return errorResult(
        `Line ${lineNumber}: angle must be between -90 and 90 degrees.`,
        rows.length
      );
    }
    if (strength !== null && (strength <= 0 || strength > 3)) {
      return errorResult(
        `Line ${lineNumber}: strength must be greater than 0 and at most 3.`,
        rows.length
      );
    }
    rows.push({ z, r, angleDeg, strength: angleDeg === null ? null : strength, lineNumber });
  }

  if (sawFusionHeader) {
    return parseFusionSections(
      fusionSections.filter((section) => section.length > 0),
      plane
    );
  }
  if (rows.length === 0) return errorResult('Paste at least one numeric point row.');

  if (format !== 'profile-csv') {
    const hasThroat = rows.some((row) => withinEndpoint(row.z, 0, 0));
    const mouthZ = hasThroat && rows.length > 1 ? Math.max(...rows.map((row) => row.z)) : null;
    const invalidInteriorAngle = rows.find(
      (row) =>
        row.angleDeg !== null &&
        Math.abs(row.angleDeg) === 90 &&
        !withinEndpoint(row.z, 0, 0) &&
        !(mouthZ !== null && withinEndpoint(row.z, mouthZ, mouthZ))
    );
    if (invalidInteriorAngle) {
      return errorResult(
        `Line ${invalidInteriorAngle.lineNumber}: interior angle must be greater than -90 and less than 90 degrees.`,
        rows.indexOf(invalidInteriorAngle)
      );
    }
  }

  if (format === 'profile-csv') {
    const pointsByPlane = {
      H: rows.map(({ z, rH }) => ({ z, r: rH, angleDeg: null, strength: null })),
      V: rows.map(({ z, rV }) => ({ z, r: rV, angleDeg: null, strength: null })),
    };
    const normalizedPlane = String(plane).toUpperCase() === 'V' ? 'V' : 'H';
    return {
      ok: true,
      error: null,
      rowCount: rows.length,
      format,
      layout,
      points: pointsByPlane[normalizedPlane],
      pointsByPlane,
      supportsBoth: true,
    };
  }

  return {
    ok: true,
    error: null,
    rowCount: rows.length,
    format,
    layout: null,
    points: rows.map(({ lineNumber: _lineNumber, ...point }) => point),
    pointsByPlane: null,
    supportsBoth: false,
  };
}

function preparePlane(points, params, plane) {
  const lengthValue = Number(params?.length);
  const length = Number.isFinite(lengthValue) && lengthValue > 0 ? lengthValue : 120;
  const sorted = [...points].sort((left, right) => left.z - right.z);
  const hasThroat = withinEndpoint(sorted[0]?.z ?? Infinity, 0, length);
  const hasMouth = withinEndpoint(sorted.at(-1)?.z ?? -Infinity, length, length);
  const throat = hasThroat ? sorted.shift() : null;
  const mouth = hasMouth ? sorted.pop() : null;
  for (const point of sorted) {
    if (!(Number(point.r) > 0)) throw new Error(`${plane} profile radius must be greater than 0.`);
    if (!(Number(point.z) > 0 && Number(point.z) < length)) {
      throw new Error(`${plane} profile z must be strictly between 0 and ${length} mm.`);
    }
  }
  const originalInteriorCount = sorted.length;
  const normalizedAll = normalizeAnchorList(sorted, {
    length,
    maxAnchors: Number.MAX_SAFE_INTEGER,
  });
  let interior = normalizedAll;
  if (normalizedAll.length > MAX_INTERIOR_ANCHORS) {
    const currentThroat = Number(params?.throatRadius);
    const currentMouth = Number(params?.[`mouthRadius${plane}`]);
    const structural = [
      { z: 0, r: throat?.r ?? (Number.isFinite(currentThroat) ? currentThroat : 12.7) },
      ...normalizedAll,
      { z: length, r: mouth?.r ?? (Number.isFinite(currentMouth) ? currentMouth : 140) },
    ];
    // A negative tolerance disables the converter's early tolerance stop so an
    // oversized explicit anchor list uses the complete 62-anchor budget.
    interior = decimateMeridian(structural, -1, MAX_INTERIOR_ANCHORS).slice(1, -1);
  }

  return {
    interior: normalizeAnchorList(interior, { length }),
    throat,
    mouth,
    originalInteriorCount,
  };
}

/** Build one atomic GlobalState patch from a successful paste parse. */
export function prepareFreeformPointPastePatch(
  parsed,
  params,
  { plane = 'H', applyBoth = false, adjustLength = null } = {}
) {
  if (!parsed?.ok) throw new Error(parsed?.error || 'The pasted points are invalid.');
  const selectedPlane = String(plane).toUpperCase() === 'V' ? 'V' : 'H';
  const planes =
    applyBoth && parsed.supportsBoth
      ? [selectedPlane, selectedPlane === 'H' ? 'V' : 'H']
      : [selectedPlane];
  const patch = {};
  const reports = [];
  const lengthValue = Number(params?.length);
  const currentLength = Number.isFinite(lengthValue) && lengthValue > 0 ? lengthValue : 120;
  const fullProfileExtents = planes
    .map((targetPlane) => {
      const sourcePoints = parsed.pointsByPlane?.[targetPlane] || parsed.points;
      const sorted = [...sourcePoints].sort((left, right) => left.z - right.z);
      if (!withinEndpoint(sorted[0]?.z ?? Infinity, 0, currentLength)) return null;
      const extent = Number(sorted.at(-1)?.z);
      return Number.isFinite(extent) && !withinEndpoint(extent, currentLength, currentLength)
        ? { plane: targetPlane, extent }
        : null;
    })
    .filter(Boolean);

  if (fullProfileExtents.length > 0 && adjustLength === null) {
    const suggestedLength = fullProfileExtents[0].extent;
    if (
      fullProfileExtents.some(
        ({ extent }) => !withinEndpoint(extent, suggestedLength, suggestedLength)
      )
    ) {
      throw new Error('Pasted H and V full profiles must share the same terminal depth.');
    }
    return {
      patch: null,
      reports: [],
      message: `Full profile ends at ${suggestedLength} mm; model length is ${currentLength} mm.`,
      decision: {
        type: 'length-mismatch',
        currentLength,
        suggestedLength,
        planes: fullProfileExtents.map(({ plane: targetPlane }) => targetPlane),
      },
    };
  }

  const suggestedLength = fullProfileExtents[0]?.extent;
  const targetParams =
    adjustLength === true && Number.isFinite(suggestedLength)
      ? { ...params, length: suggestedLength }
      : params;
  if (targetParams !== params) patch.length = suggestedLength;

  for (const targetPlane of planes) {
    const sourcePoints = parsed.pointsByPlane?.[targetPlane] || parsed.points;
    const prepared = preparePlane(sourcePoints, targetParams, targetPlane);
    patch[`interior${targetPlane}`] = prepared.interior;
    if (prepared.throat && !Object.hasOwn(patch, 'throatRadius')) {
      patch.throatRadius = prepared.throat.r;
    }
    if (prepared.throat?.angleDeg !== null && prepared.throat?.angleDeg !== undefined) {
      patch.throatAngle = prepared.throat.angleDeg;
    }
    if (prepared.throat?.strength !== null && prepared.throat?.strength !== undefined) {
      patch[`throatTangentScale${targetPlane}`] = prepared.throat.strength;
    }
    if (prepared.mouth) {
      patch[`mouthRadius${targetPlane}`] = prepared.mouth.r;
      if (prepared.mouth.angleDeg !== null && prepared.mouth.angleDeg !== undefined) {
        patch[`mouthAngle${targetPlane}`] = prepared.mouth.angleDeg;
      }
      if (prepared.mouth.strength !== null && prepared.mouth.strength !== undefined) {
        patch[`mouthTangentScale${targetPlane}`] = prepared.mouth.strength;
      }
    }
    reports.push({
      plane: targetPlane,
      kept: prepared.interior.length,
      original: prepared.originalInteriorCount,
    });
  }

  const decimated = reports.filter((report) => report.original > MAX_INTERIOR_ANCHORS);
  return {
    patch,
    reports,
    message: decimated.length
      ? decimated
          .map((report) => `${report.plane}: kept ${report.kept} of ${report.original}`)
          .join(' · ')
      : '',
    decision: null,
  };
}

export { FUSION_HEADER, PROFILE_HEADER, SLICES_HEADER };
