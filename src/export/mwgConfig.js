import { toWireFreeform } from '../config/freeformModel.js';

/**
 * Generate parameter config file content from parameters.
 * @param {Object} params - The parameter object.
 * @returns {string} The formatted config file content.
 */
export function generateMWGConfigContent(params) {
  let content = '; Parameter config\n';
  // Use local time format to match system clock
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  const hour = String(now.getHours()).padStart(2, '0');
  const minute = String(now.getMinutes()).padStart(2, '0');
  content += `; Generated: ${year}-${month}-${day} ${hour}:${minute}\n`;

  const formatValue = (value) => {
    if (value === undefined || value === null) return '';
    if (Array.isArray(value)) return value.join(',');
    if (typeof value === 'boolean') return value ? '1' : '0';
    return String(value);
  };

  const isNonZero = (value) => {
    if (value === undefined || value === null) return false;
    if (typeof value === 'boolean') return value;
    const num = Number(value);
    if (Number.isFinite(num)) return num !== 0;
    return String(value).trim() !== '' && String(value).trim() !== '0';
  };

  // Helper to get raw value if it's a string expression, or check params
  // Since we receive the already-parsed params object here, we assume 'params' holds the current values.
  // However, the original code used `getRawVal` from DOM to preserve expressions (e.g. "45 + 10").
  // If 'params' contains the evaluated number, we lose the expression.
  // Constraint: The 'params' object passed here should ideally contain the raw strings for expression fields.
  // If the UI updates 'params' with numbers, we might need a separate 'rawParams' object or
  // we accept that we export the evaluated values.
  // FOR PHASING 0: We will assume 'params' contains values (mixed string/number).

  // NOTE: The original code fetched directly from DOM to get the string value.
  // In the refactor, we will eventually bindings that update the params object with the raw string.

  const hasScale = params.Scale !== undefined || params.scale !== undefined;
  if (hasScale) {
    const scaleValue = params.scale ?? params.Scale;
    const scaleNum = Number(scaleValue);
    if (!Number.isFinite(scaleNum) || scaleNum !== 1 || params.Scale !== undefined) {
      content += `Scale = ${formatValue(scaleValue)}\n`;
    }
  }

  if (params.type === 'FREEFORM') {
    const wire = toWireFreeform(params);
    const horizontal = wire.profile_h;
    const vertical = wire.profile_v;
    const writeProfile = (name, profile) => {
      content += `Freeform.${name} = {\n`;
      content += `MouthRadius = ${profile.points.at(-1)[1]}\n`;
      content += `MouthAngle = ${profile.mouth_angle_deg}\n`;
      content += `ThroatTangentScale = ${profile.throat_tangent_scale}\n`;
      content += `MouthTangentScale = ${profile.mouth_tangent_scale}\n`;
      content += '}\n';
      content += `Freeform.${name}.Points = {\n`;
      for (const row of profile.points.slice(1, -1)) content += `${row.join(' ')}\n`;
      content += '}\n';
    };

    // FREEFORM .mwg syntax (all numeric values are millimetres/degrees as named):
    //   Freeform.H.Points rows: z r [angleDeg [strength]]
    //   Freeform.CrossSections rows: t shape [exponent|cornerRadiusMm]
    // The optional station value is an exponent for superellipse and an absolute
    // corner radius for rounded_rectangle. Legacy ratios use the tagged ratio:<n> form.
    content += '; FREEFORM point rows: z r [angleDeg [strength]]\n';
    content += '; FREEFORM station rows: t shape [exponent|cornerRadiusMm]\n';
    content += `Freeform.Length = ${horizontal.points.at(-1)[0]}\n`;
    content += `Freeform.ThroatRadius = ${horizontal.points[0][1]}\n`;
    content += `Freeform.ThroatAngle = ${horizontal.throat_angle_deg}\n`;
    content += `Freeform.OvershootPolicy = ${wire.overshoot_policy}\n`;
    content += `Freeform.InflectionPolicy = ${wire.inflection_policy}\n`;
    writeProfile('H', horizontal);
    writeProfile('V', vertical);
    content += 'Freeform.CrossSections = {\n';
    for (const station of wire.cross_sections) {
      const row = [station.t, station.shape];
      if (station.shape === 'superellipse' && station.exponent !== undefined) {
        row.push(station.exponent);
      } else if (station.shape === 'rounded_rectangle' && station.corner_radius_mm !== undefined) {
        row.push(station.corner_radius_mm);
      } else if (station.shape === 'rounded_rectangle' && station.corner_ratio !== undefined) {
        row.push(`ratio:${station.corner_ratio}`);
      }
      content += `${row.join(' ')}\n`;
    }
    content += '}\n';

    if (params.encDepth > 0) {
      content += 'Mesh.Enclosure = {\n';
      content += `Depth = ${params.encDepth}\n`;
      content += `EdgeRadius = ${params.encEdge}\n`;
      content += `EdgeType = ${params.encEdgeType}\n`;
      content += `Spacing = ${params.encSpaceL || 25},${params.encSpaceT || 25},${params.encSpaceR || 25},${params.encSpaceB || 25}\n`;
      if (isNonZero(params.encFrontResolution))
        content += `FrontResolution = ${formatValue(params.encFrontResolution)}\n`;
      if (isNonZero(params.encBackResolution))
        content += `BackResolution = ${formatValue(params.encBackResolution)}\n`;
      content += '}\n';
    }
  } else if (params.type === 'R-OSSE') {
    content += 'R-OSSE = {\n';
    content += `R = ${params.R}\n`;
    content += `a = ${params.a}\n`;
    content += `a0 = ${params.a0}\n`;
    content += `b = ${params.b}\n`;
    content += `k = ${params.k}\n`;
    content += `m = ${params.m}\n`;
    content += `q = ${params.q}\n`;
    content += `r = ${params.r}\n`;
    content += `r0 = ${params.r0}\n`;
    if (params.tmax !== 1.0) content += `tmax = ${params.tmax}\n`;
    content += '}\n';
  } else {
    if (params.throatProfile !== undefined) {
      content += `Throat.Profile = ${formatValue(params.throatProfile)}\n`;
    }
    if (isNonZero(params.throatExtAngle))
      content += `Throat.Ext.Angle = ${formatValue(params.throatExtAngle)}\n`;
    if (isNonZero(params.throatExtLength))
      content += `Throat.Ext.Length = ${formatValue(params.throatExtLength)}\n`;
    if (isNonZero(params.slotLength))
      content += `Slot.Length = ${formatValue(params.slotLength)}\n`;
    content += `Coverage.Angle = ${params.a}\n`;
    content += `Length = ${params.L}\n`;
    content += `Term.n = ${params.n}\n`;
    content += `Term.q = ${params.q}\n`;
    content += `Term.s = ${params.s}\n`;
    content += `Throat.Angle = ${params.a0}\n`;
    content += `Throat.Diameter = ${params.r0 * 2}\n`;
    if (params.throatProfile === undefined) content += `Throat.Profile = 1\n`;
    content += `OS.k = ${params.k}\n`;
    if (params.h !== undefined && params.h !== 0) content += `OS.h = ${params.h}\n`;
    if (isNonZero(params.rot)) content += `Rot = ${formatValue(params.rot)}\n`;

    if (params.gcurveType && Number(params.gcurveType) !== 0) {
      content += `GCurve.Type = ${formatValue(params.gcurveType)}\n`;
      if (isNonZero(params.gcurveDist))
        content += `GCurve.Dist = ${formatValue(params.gcurveDist)}\n`;
      if (isNonZero(params.gcurveWidth))
        content += `GCurve.Width = ${formatValue(params.gcurveWidth)}\n`;
      if (isNonZero(params.gcurveAspectRatio))
        content += `GCurve.AspectRatio = ${formatValue(params.gcurveAspectRatio)}\n`;
      if (isNonZero(params.gcurveSeN))
        content += `GCurve.SE.n = ${formatValue(params.gcurveSeN)}\n`;
      if (isNonZero(params.gcurveSf)) content += `GCurve.SF = ${formatValue(params.gcurveSf)}\n`;
      if (isNonZero(params.gcurveSfA))
        content += `GCurve.SF.a = ${formatValue(params.gcurveSfA)}\n`;
      if (isNonZero(params.gcurveSfB))
        content += `GCurve.SF.b = ${formatValue(params.gcurveSfB)}\n`;
      if (isNonZero(params.gcurveSfM1))
        content += `GCurve.SF.m1 = ${formatValue(params.gcurveSfM1)}\n`;
      if (isNonZero(params.gcurveSfM2))
        content += `GCurve.SF.m2 = ${formatValue(params.gcurveSfM2)}\n`;
      if (isNonZero(params.gcurveSfN1))
        content += `GCurve.SF.n1 = ${formatValue(params.gcurveSfN1)}\n`;
      if (isNonZero(params.gcurveSfN2))
        content += `GCurve.SF.n2 = ${formatValue(params.gcurveSfN2)}\n`;
      if (isNonZero(params.gcurveSfN3))
        content += `GCurve.SF.n3 = ${formatValue(params.gcurveSfN3)}\n`;
      if (isNonZero(params.gcurveRot)) content += `GCurve.Rot = ${formatValue(params.gcurveRot)}\n`;
    }

    if (isNonZero(params.circArcRadius))
      content += `CircArc.Radius = ${formatValue(params.circArcRadius)}\n`;
    if (isNonZero(params.circArcTermAngle))
      content += `CircArc.TermAngle = ${formatValue(params.circArcTermAngle)}\n`;

    const morphAllow = params.morphAllowShrinkage !== undefined;
    if ((params.morphTarget !== undefined && params.morphTarget !== 0) || morphAllow) {
      if (params.morphCorner !== undefined && params.morphCorner > 0) {
        content += `Morph.CornerRadius = ${params.morphCorner}\n`;
      }
      if (params.morphFixed !== undefined) content += `Morph.FixedPart = ${params.morphFixed}\n`;
      if (params.morphRate !== undefined) content += `Morph.Rate = ${params.morphRate}\n`;
      if (params.morphTarget !== undefined)
        content += `Morph.TargetShape = ${params.morphTarget}\n`;
      if (params.morphWidth !== undefined && params.morphWidth > 0) {
        content += `Morph.TargetWidth = ${params.morphWidth}\n`;
      }
      if (params.morphHeight !== undefined && params.morphHeight > 0) {
        content += `Morph.TargetHeight = ${params.morphHeight}\n`;
      }
      if (params.morphAllowShrinkage !== undefined) {
        content += `Morph.AllowShrinkage = ${formatValue(params.morphAllowShrinkage)}\n`;
      }
    }

    // Enclosure plan feature removed - only standard enclosure is supported
    if (params.encDepth > 0) {
      content += `Mesh.Enclosure = {\n`;
      content += `Depth = ${params.encDepth}\n`;
      content += `EdgeRadius = ${params.encEdge}\n`;
      content += `EdgeType = ${params.encEdgeType}\n`;
      content += `Spacing = ${params.encSpaceL || 25},${params.encSpaceT || 25},${params.encSpaceR || 25},${params.encSpaceB || 25}\n`;
      if (isNonZero(params.encFrontResolution))
        content += `FrontResolution = ${formatValue(params.encFrontResolution)}\n`;
      if (isNonZero(params.encBackResolution))
        content += `BackResolution = ${formatValue(params.encBackResolution)}\n`;
      content += `}\n`;
    }
  }

  content += `Mesh.AngularSegments = ${params.angularSegments}\n`;
  if (
    params.cornerSegments !== undefined &&
    (params.type === 'FREEFORM' || params.morphTarget === 1)
  ) {
    content += `Mesh.CornerSegments = ${Math.max(0, Math.round(Number(params.cornerSegments)))}\n`;
  }
  if (isNonZero(params.throatSegments))
    content += `Mesh.ThroatSegments = ${formatValue(params.throatSegments)}\n`;
  content += `Mesh.LengthSegments = ${params.lengthSegments}\n`;
  if (isNonZero(params.throatResolution))
    content += `Mesh.ThroatResolution = ${formatValue(params.throatResolution)}\n`;
  if (isNonZero(params.mouthResolution))
    content += `Mesh.MouthResolution = ${formatValue(params.mouthResolution)}\n`;
  if (params.throatSliceDensity !== undefined && params.throatSliceDensity !== null)
    content += `Mesh.ThroatSliceDensity = ${formatValue(params.throatSliceDensity)}\n`;
  if (params.samplingMode) content += `Mesh.SamplingMode = ${formatValue(params.samplingMode)}\n`;
  if (isNonZero(params.verticalOffset))
    content += `Mesh.VerticalOffset = ${formatValue(params.verticalOffset)}\n`;
  if (params.quadrants !== undefined)
    content += `Mesh.Quadrants = ${formatValue(params.quadrants)}\n`;
  if (params.wallThickness > 0) content += `Mesh.WallThickness = ${params.wallThickness}\n`;
  if (isNonZero(params.rearResolution))
    content += `Mesh.RearResolution = ${formatValue(params.rearResolution)}\n`;
  if (isNonZero(params.apertureResolutionScale))
    content += `Mesh.ApertureResolutionScale = ${formatValue(params.apertureResolutionScale)}\n`;
  if (params.maxTriangles !== undefined)
    content += `Mesh.MaxTriangles = ${formatValue(params.maxTriangles)}\n`;
  if (params.allowLargeMesh !== undefined)
    content += `Mesh.AllowLargeMesh = ${formatValue(params.allowLargeMesh)}\n`;

  if (params.outputSTL !== undefined) {
    content += `Output.STL = ${formatValue(params.outputSTL)}\n`;
  }
  if (params.outputMSH !== undefined) {
    content += `Output.MSH = ${formatValue(params.outputMSH)}\n`;
  }

  if (params.sourceShape !== undefined) content += `Source.Shape = ${params.sourceShape}\n`;
  if (params.sourceRadius !== undefined && params.sourceRadius !== -1) {
    content += `Source.Radius = ${params.sourceRadius}\n`;
  }
  if (params.sourceCurv !== undefined)
    content += `Source.Curv = ${formatValue(params.sourceCurv)}\n`;
  if (params.sourceVelocity !== undefined)
    content += `Source.Velocity = ${params.sourceVelocity}\n`;
  if (params.sourceContours) content += `Source.Contours = ${formatValue(params.sourceContours)}\n`;

  if (params.freqStart !== undefined) content += `Simulation.F1 = ${params.freqStart}\n`;
  if (params.freqEnd !== undefined) content += `Simulation.F2 = ${params.freqEnd}\n`;
  if (params.numFreqs !== undefined) content += `Simulation.NumFrequencies = ${params.numFreqs}\n`;
  if (params.simType !== undefined)
    content += `Simulation.SimType = ${formatValue(params.simType)}\n`;
  if (params.solverMode !== undefined)
    content += `Simulation.SolverMode = ${formatValue(params.solverMode)}\n`;

  const blocks = params._blocks || {};
  for (const [blockName, block] of Object.entries(blocks)) {
    if (blockName === 'Mesh.Enclosure' || blockName.startsWith('Freeform.')) continue;
    if (!block) continue;
    content += `${blockName} = {\n`;
    if (block._lines && block._lines.length > 0) {
      content += `${block._lines.join('\n')}\n`;
    }
    if (block._items) {
      for (const [key, value] of Object.entries(block._items)) {
        content += `${key} = ${value}\n`;
      }
    }
    content += `}\n`;
  }

  return content;
}
