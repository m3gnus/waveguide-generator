import { fromWireFreeform } from './freeformModel.js';

const SHARED_FLAT_KEY_MAP = Object.freeze({
  'Morph.TargetShape': 'morphTarget',
  'Morph.TargetWidth': 'morphWidth',
  'Morph.TargetHeight': 'morphHeight',
  'Morph.CornerRadius': 'morphCorner',
  'Morph.Rate': 'morphRate',
  'Morph.FixedPart': 'morphFixed',
  'Mesh.AngularSegments': 'angularSegments',
  'Mesh.LengthSegments': 'lengthSegments',
  'Mesh.CornerSegments': 'cornerSegments',
  'Mesh.ThroatSegments': 'throatSegments',
  'Mesh.ThroatResolution': 'throatResolution',
  'Mesh.MouthResolution': 'mouthResolution',
  'Mesh.ThroatSliceDensity': 'throatSliceDensity',
  'Mesh.SamplingMode': 'samplingMode',
  'Mesh.VerticalOffset': 'verticalOffset',
  'Mesh.Quadrants': 'quadrants',
  'Mesh.WallThickness': 'wallThickness',
  'Mesh.RearResolution': 'rearResolution',
  'Mesh.ApertureResolutionScale': 'apertureResolutionScale',
  'Mesh.MaxTriangles': 'maxTriangles',
  'Mesh.AllowLargeMesh': 'allowLargeMesh',
  'Source.Shape': 'sourceShape',
  'Source.Radius': 'sourceRadius',
  'Source.Curv': 'sourceCurv',
  'Source.Velocity': 'sourceVelocity',
  'Source.Contours': 'sourceContours',
  'ABEC.NumFrequencies': 'numFreqs',
  'ABEC.SimType': 'simType',
  'Simulation.F1': 'freqStart',
  'Simulation.F2': 'freqEnd',
  'Simulation.NumFrequencies': 'numFreqs',
  'Simulation.SimType': 'simType',
  'Simulation.SolverMode': 'solverMode',
  'Output.STL': 'outputSTL',
  'Output.MSH': 'outputMSH',
});

function normalizeSharedFlatKeys(params) {
  for (const [sourceKey, targetKey] of Object.entries(SHARED_FLAT_KEY_MAP)) {
    if (params[sourceKey] !== undefined) params[targetKey] = params[sourceKey];
  }
  if (params['Mesh.ZMapPoints'] !== undefined) params.zMapPoints = params['Mesh.ZMapPoints'];
  if (params['Mesh.ZMap'] !== undefined) params.zMapPoints = params['Mesh.ZMap'];
  if (params['ABEC.f1'] !== undefined) params.freqStart = params['ABEC.f1'];
  if (params['ABEC.F1'] !== undefined) params.freqStart = params['ABEC.F1'];
  if (params['ABEC.f2'] !== undefined) params.freqEnd = params['ABEC.f2'];
  if (params['ABEC.F2'] !== undefined) params.freqEnd = params['ABEC.F2'];
  if (params['Morph.AllowShrinkage'] !== undefined) {
    params.morphAllowShrinkage =
      params['Morph.AllowShrinkage'] === '1' || params['Morph.AllowShrinkage'] === 1;
  }
}

function parseFreeformRows(block) {
  return (block?._lines || []).map((line) => line.trim().split(/\s+/));
}

function consumeFreeformSection(result) {
  const p = result.params;
  const horizontalBlock = result.blocks['Freeform.H'];
  const verticalBlock = result.blocks['Freeform.V'];
  const length = p['Freeform.Length'];
  const throatRadius = p['Freeform.ThroatRadius'];
  const throatAngle = p['Freeform.ThroatAngle'];
  const profile = (name, block) => ({
    points: [
      [0, throatRadius],
      ...parseFreeformRows(result.blocks[`Freeform.${name}.Points`]),
      [length, block?._items?.MouthRadius],
    ],
    throat_angle_deg: throatAngle,
    mouth_angle_deg: block?._items?.MouthAngle,
    throat_tangent_scale: block?._items?.ThroatTangentScale,
    mouth_tangent_scale: block?._items?.MouthTangentScale,
  });
  const crossSections = parseFreeformRows(result.blocks['Freeform.CrossSections']).map(
    ([t, shape, optional]) => {
      const station = { t, shape };
      if (shape === 'superellipse' && optional !== undefined) station.exponent = optional;
      if (shape === 'rounded_rectangle' && optional !== undefined) {
        if (optional.startsWith('ratio:')) station.corner_ratio = optional.slice(6);
        else station.corner_radius_mm = optional;
      }
      return station;
    }
  );
  const canonical = fromWireFreeform({
    profile_h: profile('H', horizontalBlock),
    profile_v: profile('V', verticalBlock),
    cross_sections: crossSections,
    overshoot_policy: p['Freeform.OvershootPolicy'],
    inflection_policy: p['Freeform.InflectionPolicy'],
  });

  for (const key of Object.keys(p)) {
    if (key.startsWith('Freeform.')) delete p[key];
  }
  for (const key of Object.keys(result.blocks)) {
    if (key.startsWith('Freeform.')) delete result.blocks[key];
  }
  Object.assign(p, canonical);
}

export class MWGConfigParser {
  static parse(content) {
    const result = { type: null, params: {}, blocks: {} };
    const lines = content
      .split('\n')
      .map((line) => {
        const commentIdx = line.indexOf(';');
        return (commentIdx !== -1 ? line.substring(0, commentIdx) : line).trim();
      })
      .filter((line) => line.length > 0);

    let currentBlock = null;
    for (const line of lines) {
      // Block start: "Name = {" or "Name:Sub = {"
      const blockStartMatch = line.match(/^([\w.:-]+)\s*=\s*\{/);
      if (blockStartMatch) {
        const currentBlockName = blockStartMatch[1];
        if (currentBlockName.startsWith('Freeform.')) {
          result.type = 'FREEFORM';
          currentBlock = currentBlockName;
          result.blocks[currentBlockName] = { _items: {}, _lines: [] };
        } else if (currentBlockName === 'R-OSSE') {
          result.type = 'R-OSSE';
          currentBlock = 'R-OSSE';
        } else if (currentBlockName === 'OSSE') {
          result.type = 'OSSE';
          currentBlock = 'OSSE';
        } else {
          currentBlock = currentBlockName;
          result.blocks[currentBlockName] = { _items: {}, _lines: [] };
        }
        continue;
      }

      // Block end
      if (line === '}') {
        currentBlock = null;
        continue;
      }

      // Key = Value (split on first = only, to handle expressions with =)
      const eqIdx = line.indexOf('=');
      if (eqIdx > 0) {
        const key = line.substring(0, eqIdx).trim();
        const value = line.substring(eqIdx + 1).trim();

        if (currentBlock === 'R-OSSE' || currentBlock === 'OSSE') {
          result.params[key] = value;
        } else if (currentBlock && result.blocks[currentBlock]) {
          result.blocks[currentBlock]._items[key] = value;
        } else {
          // Flat top-level key — detect OSSE by known flat keys
          result.params[key] = value;
        }
      } else if (currentBlock && result.blocks[currentBlock]) {
        result.blocks[currentBlock]._lines.push(line);
      }
    }

    // Auto-detect OSSE from flat-key format (no OSSE = { } block)
    if (!result.type) {
      if (
        result.params['Freeform.Length'] !== undefined ||
        Object.keys(result.blocks).some((key) => key.startsWith('Freeform.'))
      ) {
        result.type = 'FREEFORM';
      } else if (
        result.params['Coverage.Angle'] ||
        result.params['Length'] ||
        result.params['Term.n']
      ) {
        result.type = 'OSSE';
      }
    }

    if (result.type === 'FREEFORM') consumeFreeformSection(result);

    // Normalize OSSE flat-key names to internal parameter names
    if (result.type === 'OSSE') {
      const p = result.params;
      const assignLegacyValue = (targetKey, sourceKey, transform = (value) => value) => {
        if (p[targetKey] !== undefined || p[sourceKey] === undefined) return;
        p[targetKey] = transform(p[sourceKey]);
      };

      assignLegacyValue('a', 'Coverage.Angle');
      assignLegacyValue('a0', 'Throat.Angle');
      assignLegacyValue('r0', 'Throat.Diameter', (value) => {
        const radius = parseFloat(value) / 2;
        return Number.isFinite(radius) ? String(radius) : value;
      });
      assignLegacyValue('L', 'Length');
      assignLegacyValue('s', 'Term.s');
      assignLegacyValue('n', 'Term.n');
      assignLegacyValue('q', 'Term.q');
      assignLegacyValue('h', 'OS.h');
      assignLegacyValue('k', 'OS.k');

      if (p['Throat.Profile']) {
        p.throatProfile = p['Throat.Profile'];
      }
      if (p['Throat.Ext.Angle']) {
        p.throatExtAngle = p['Throat.Ext.Angle'];
      }
      if (p['Throat.Ext.Length']) {
        p.throatExtLength = p['Throat.Ext.Length'];
      }
      if (p['Slot.Length']) {
        p.slotLength = p['Slot.Length'];
      }
      if (p['Rot']) {
        p.rot = p['Rot'];
      }
      if (p['CircArc.TermAngle']) {
        p.circArcTermAngle = p['CircArc.TermAngle'];
      }
      if (p['CircArc.Radius']) {
        p.circArcRadius = p['CircArc.Radius'];
      }
      if (p['GCurve.Type']) {
        p.gcurveType = p['GCurve.Type'];
      }
      if (p['GCurve.Dist']) {
        p.gcurveDist = p['GCurve.Dist'];
      }
      if (p['GCurve.Width']) {
        p.gcurveWidth = p['GCurve.Width'];
      }
      if (p['GCurve.AspectRatio']) {
        p.gcurveAspectRatio = p['GCurve.AspectRatio'];
      }
      if (p['GCurve.SE.n']) {
        p.gcurveSeN = p['GCurve.SE.n'];
      }
      if (p['GCurve.SF']) {
        p.gcurveSf = p['GCurve.SF'];
      }
      if (p['GCurve.SF.a']) {
        p.gcurveSfA = p['GCurve.SF.a'];
      }
      if (p['GCurve.SF.b']) {
        p.gcurveSfB = p['GCurve.SF.b'];
      }
      if (p['GCurve.SF.m1']) {
        p.gcurveSfM1 = p['GCurve.SF.m1'];
      }
      if (p['GCurve.SF.m2']) {
        p.gcurveSfM2 = p['GCurve.SF.m2'];
      }
      if (p['GCurve.SF.n1']) {
        p.gcurveSfN1 = p['GCurve.SF.n1'];
      }
      if (p['GCurve.SF.n2']) {
        p.gcurveSfN2 = p['GCurve.SF.n2'];
      }
      if (p['GCurve.SF.n3']) {
        p.gcurveSfN3 = p['GCurve.SF.n3'];
      }
      if (p['GCurve.Rot']) {
        p.gcurveRot = p['GCurve.Rot'];
      }
    }

    // OSSE, R-OSSE, and FREEFORM share one flat-key normalization path.
    normalizeSharedFlatKeys(result.params);

    // Normalize params (both types, flat keys)
    {
      const p = result.params;
      if (p['Scale'] !== undefined) {
        const scaleNum = Number(p['Scale']);
        p.scale = Number.isFinite(scaleNum) ? scaleNum : p['Scale'];
      }
    }

    normalizeStraightSlotExtension(result.params);

    // Parse Mesh.Enclosure block if present
    const encBlock = result.blocks['Mesh.Enclosure'];
    if (encBlock && encBlock._items) {
      const p = result.params;
      if (encBlock._items.Depth) {
        p.encDepth = encBlock._items.Depth;
      }
      if (encBlock._items.EdgeRadius) {
        p.encEdge = encBlock._items.EdgeRadius;
      }
      if (encBlock._items.EdgeType) {
        p.encEdgeType = encBlock._items.EdgeType;
      }
      if (encBlock._items.FrontResolution) {
        p.encFrontResolution = encBlock._items.FrontResolution;
      }
      if (encBlock._items.BackResolution) {
        p.encBackResolution = encBlock._items.BackResolution;
      }
      if (encBlock._items.Spacing) {
        const parts = encBlock._items.Spacing.split(',').map((s) => s.trim());
        if (parts.length >= 4) {
          p.encSpaceL = parts[0];
          p.encSpaceT = parts[1];
          p.encSpaceR = parts[2];
          p.encSpaceB = parts[3];
        }
      }
    }

    return result;
  }
}

function finiteNumberOrNull(value) {
  if (value === undefined || value === null || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function normalizeStraightSlotExtension(params) {
  if (!params || typeof params !== 'object') return;

  const angle = finiteNumberOrNull(params.throatExtAngle ?? params['Throat.Ext.Angle'] ?? 0);
  const slotLength = finiteNumberOrNull(params.slotLength ?? params['Slot.Length'] ?? 0);
  if (angle === null || Math.abs(angle) > 1e-12 || slotLength === null || slotLength <= 0) {
    return;
  }

  const extLength = finiteNumberOrNull(params.throatExtLength ?? params['Throat.Ext.Length'] ?? 0);
  if (extLength === null) return;

  params.throatExtAngle = '0';
  params.throatExtLength = String(extLength + slotLength);
  params.slotLength = '0';
}

// Default values for model parameters (from schema)
export const CONFIG_DEFAULTS = {
  OSSE: {
    scale: 1.0,
    L: 120,
    a: '48.5 - 5.6*cos(2*p)^5 - 31*sin(p)^12',
    a0: 15.5,
    r0: 12.7,
    k: 7.0,
    s: '0.58 + 0.2*cos(p)^2',
    n: 4.158,
    q: 0.991,
    h: 0.0,
    throatProfile: 1,
    throatExtAngle: '0',
    throatExtLength: '0',
    slotLength: '0',
    rot: '0',
    gcurveType: 0,
    gcurveDist: '0.5',
    gcurveWidth: '0',
    gcurveAspectRatio: '1',
    gcurveSeN: '3',
    gcurveSf: '',
    gcurveSfA: '',
    gcurveSfB: '',
    gcurveSfM1: '',
    gcurveSfM2: '',
    gcurveSfN1: '',
    gcurveSfN2: '',
    gcurveSfN3: '',
    gcurveRot: '0',
    circArcTermAngle: '1',
    circArcRadius: '0',
    morphTarget: 1,
    morphWidth: 0,
    morphHeight: 0,
    morphCorner: 0,
    morphRate: 3.0,
    morphFixed: 0.0,
    morphAllowShrinkage: 0,
    angularSegments: 120,
    lengthSegments: 40,
    cornerSegments: 4,
    throatSegments: 0,
    throatResolution: 6.0,
    mouthResolution: 15.0,
    verticalOffset: 0.0,
    quadrants: '1234',
    wallThickness: 5.0,
    rearResolution: 40.0,
    apertureResolutionScale: 1.5,
    maxTriangles: 50000,
    allowLargeMesh: 0,
    encDepth: 280,
    encEdge: 18,
    encEdgeType: 1,
    encSpaceL: 25,
    encSpaceT: 25,
    encSpaceR: 25,
    encSpaceB: 25,
    encFrontResolution: '25,25,25,25',
    encBackResolution: '40,40,40,40',
    sourceShape: 1,
    sourceRadius: -1,
    sourceCurv: 0,
    sourceVelocity: 1,
    sourceContours: '',
    freqStart: 400,
    freqEnd: 16000,
    numFreqs: 40,
  },
  'R-OSSE': {
    scale: 1.0,
    R: '140 * (abs(cos(p)/1.6)^3 + abs(sin(p)/1)^4)^(-1/4.5)',
    a: '25 * (abs(cos(p)/1.2)^4 + abs(sin(p)/1)^3)^(-1/2.5)',
    a0: 15.5,
    r0: 12.7,
    k: 2.0,
    m: 0.85,
    b: '0.2',
    r: 0.4,
    q: 3.4,
    tmax: 1.0,
    throatProfile: 1,
    throatExtAngle: '0',
    throatExtLength: '0',
    slotLength: '0',
    rot: '0',
    gcurveType: 0,
    gcurveDist: '0.5',
    gcurveWidth: '0',
    gcurveAspectRatio: '1',
    gcurveSeN: '3',
    gcurveSf: '',
    gcurveSfA: '',
    gcurveSfB: '',
    gcurveSfM1: '',
    gcurveSfM2: '',
    gcurveSfN1: '',
    gcurveSfN2: '',
    gcurveSfN3: '',
    gcurveRot: '0',
    circArcTermAngle: '1',
    circArcRadius: '0',
    morphTarget: 1,
    morphWidth: 0,
    morphHeight: 0,
    morphCorner: 0,
    morphRate: 3.0,
    morphFixed: 0.0,
    morphAllowShrinkage: 0,
    angularSegments: 120,
    lengthSegments: 40,
    cornerSegments: 4,
    throatSegments: 0,
    throatResolution: 6.0,
    mouthResolution: 15.0,
    verticalOffset: 0.0,
    quadrants: '1234',
    wallThickness: 5.0,
    rearResolution: 40.0,
    apertureResolutionScale: 1.5,
    maxTriangles: 50000,
    allowLargeMesh: 0,
    encDepth: 280,
    encEdge: 18,
    encEdgeType: 1,
    encSpaceL: 25,
    encSpaceT: 25,
    encSpaceR: 25,
    encSpaceB: 25,
    encFrontResolution: '25,25,25,25',
    encBackResolution: '40,40,40,40',
    sourceShape: 1,
    sourceRadius: -1,
    sourceCurv: 0,
    sourceVelocity: 1,
    sourceContours: '',
    freqStart: 400,
    freqEnd: 16000,
    numFreqs: 40,
  },
};

/**
 * Get default parameters for a model type
 * @param {string} modelType - 'OSSE' or 'R-OSSE'
 * @returns {Object} Default parameter values
 */
export function getDefaults(modelType) {
  return CONFIG_DEFAULTS[modelType] || {};
}

/**
 * Parse a config string into structured data
 * @param {string} content - Config file content
 * @returns {{ type: string|null, params: Object, blocks: Object }}
 */
export function parseConfig(content) {
  return MWGConfigParser.parse(content);
}

/**
 * Validate parsed config against schema
 * @param {{ type: string, params: Object }} config - Parsed config
 * @returns {{ valid: boolean, errors: string[] }}
 */
export function validateConfig(config) {
  const errors = [];
  if (!config.type) {
    errors.push('Missing model type (OSSE or R-OSSE)');
  }
  // Add more validation as needed
  return { valid: errors.length === 0, errors };
}
