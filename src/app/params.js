import { parseExpression } from '../geometry/index.js';
import { PARAM_SCHEMA } from '../config/schema.js';
import { GlobalState } from '../state.js';

const NUMERIC_PATTERN = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;

export function isNumericString(value) {
  if (typeof value !== 'string') return false;
  const trimmed = value.trim();
  if (!trimmed) return false;
  return NUMERIC_PATTERN.test(trimmed);
}

export function prepareParamsForMesh(
  { forceFullQuadrants = false, applyVerticalOffset = true } = {}
) {
  const state = GlobalState.get();
  const preparedParams = { ...state.params };
  const type = state.type;

  const rawExpressionKeys = new Set([
    'zMapPoints',
    'subdomainSlices',
    'interfaceOffset',
    'interfaceDraw',
    'gcurveSf',
    'encFrontResolution',
    'encBackResolution',
    'outputSubDir',
    'outputDestDir',
    'sourceContours'
  ]);

  const applySchema = (schema) => {
    if (!schema) return;
    for (const [key, def] of Object.entries(schema)) {
      const val = preparedParams[key];
      if (val === undefined || val === null) continue;

      if (def.type === 'expression') {
        if (rawExpressionKeys.has(key)) continue;
        if (typeof val !== 'string') continue;
        const trimmed = val.trim();
        if (!trimmed) continue;
        if (isNumericString(trimmed)) {
          preparedParams[key] = Number(trimmed);
        } else {
          preparedParams[key] = parseExpression(trimmed);
        }
      } else if ((def.type === 'number' || def.type === 'range') && typeof val === 'string') {
        const trimmed = val.trim();
        if (!trimmed) continue;
        if (isNumericString(trimmed)) {
          preparedParams[key] = Number(trimmed);
        }
      }
    }
  };

  applySchema(PARAM_SCHEMA[type] || {});
  ['GEOMETRY', 'MORPH', 'MESH', 'ROLLBACK', 'ENCLOSURE', 'SOURCE', 'ABEC', 'OUTPUT'].forEach(
    (group) => {
      applySchema(PARAM_SCHEMA[group] || {});
    }
  );

  preparedParams.type = type;

  const rawScale = preparedParams.scale ?? preparedParams.Scale ?? 1;
  const scaleNum = typeof rawScale === 'number' ? rawScale : Number(rawScale);
  const scale = Number.isFinite(scaleNum) ? scaleNum : 1;
  preparedParams.scale = scale;
  preparedParams.useAthZMap = scale !== 1;

  if (scale !== 1) {
    const lengthKeys = [
      'L',
      'r0',
      'throatExtLength',
      'slotLength',
      'circArcRadius',
      'morphCorner',
      'morphWidth',
      'morphHeight',
      'throatResolution',
      'mouthResolution',
      'verticalOffset',
      'encDepth',
      'encEdge',
      'encSpaceL',
      'encSpaceT',
      'encSpaceR',
      'encSpaceB',
      'wallThickness',
      'rearResolution',
      'interfaceOffset',
      'interfaceDraw'
    ];

    lengthKeys.forEach((key) => {
      const value = preparedParams[key];
      if (value === undefined || value === null || value === '') return;
      if (typeof value === 'function') {
        preparedParams[key] = (p) => scale * value(p);
      } else if (typeof value === 'number' && Number.isFinite(value)) {
        preparedParams[key] = value * scale;
      }
    });
  }

  if (!applyVerticalOffset) {
    preparedParams.verticalOffset = 0;
  }

  if (forceFullQuadrants) {
    preparedParams.quadrants = '1234';
  }

  return preparedParams;
}
