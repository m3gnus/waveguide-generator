import { PARAM_SCHEMA } from "../config/schema.js";
import { parseExpression } from "./expression.js";

const NUMERIC_PATTERN = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;
const PREPARED_GEOMETRY_PARAMS = Symbol("preparedGeometryParams");

const RAW_EXPRESSION_KEYS = new Set([
  "gcurveSf",
  "encFrontResolution",
  "encBackResolution",
  "sourceContours",
]);

const SCHEMA_GROUPS = [
  "GEOMETRY",
  "MORPH",
  "MESH",
  "ENCLOSURE",
  "SOURCE",
  "SIMULATION",
];

// Keys that represent physical lengths in mm and should scale with geometry.
// Mesh resolution fields (throatResolution, mouthResolution, etc.) are intentionally
// EXCLUDED because they are element SIZES (mm) that must scale with geometry,
// but scaling is handled in DesignModule OCC param preparation (not here) to ensure
// consistent single-scaling for both simulation and export pipelines.
const SCALE_LENGTH_KEYS = [
  "L",
  "R",
  "r0",
  "throatExtLength",
  "slotLength",
  "circArcRadius",
  "morphCorner",
  "morphWidth",
  "morphHeight",
  "gcurveWidth",
  "sourceRadius",
  "wallThickness",
  "verticalOffset",
];

function markPreparedGeometryParams(params) {
  if (!params || typeof params !== "object") return params;
  Object.defineProperty(params, PREPARED_GEOMETRY_PARAMS, {
    value: true,
    enumerable: false,
    configurable: true,
    writable: false,
  });
  return params;
}

export function isPreparedGeometryParams(params) {
  return Boolean(
    params &&
      typeof params === "object" &&
      params[PREPARED_GEOMETRY_PARAMS] === true,
  );
}

export function isNumericString(value) {
  if (typeof value !== "string") return false;
  const trimmed = value.trim();
  if (!trimmed) return false;
  return NUMERIC_PATTERN.test(trimmed);
}

export function isMWGConfig(content) {
  if (typeof content !== "string") return false;
  return /;\s*MWG config/i.test(content);
}

export function coerceConfigParams(params = {}) {
  const typedParams = {};
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;
    const stringValue = String(value).trim();
    typedParams[key] = isNumericString(stringValue)
      ? Number(stringValue)
      : stringValue;
  }
  return typedParams;
}

export function applyAthImportDefaults(parsed, typedParams) {
  if (
    !parsed ||
    !parsed.type ||
    !typedParams ||
    typeof typedParams !== "object"
  )
    return;

  const isOSSE = parsed.type === "OSSE";
  if (typedParams.morphTarget === undefined) {
    typedParams.morphTarget = 0;
  }

  const hasMeshEnclosure = parsed.blocks && parsed.blocks["Mesh.Enclosure"];
  if (!hasMeshEnclosure && typedParams.encDepth === undefined) {
    typedParams.encDepth = 0;
  }

  if (isOSSE) {
    if (typedParams.k === undefined) {
      typedParams.k = 1;
    }
    if (typedParams.h === undefined) {
      typedParams.h = 0;
    }
  }
}

function applySchemaToParams(params, schema) {
  if (!schema) return;
  for (const [key, def] of Object.entries(schema)) {
    const val = params[key];
    if (val === undefined || val === null) continue;

    if (def.type === "expression") {
      if (RAW_EXPRESSION_KEYS.has(key)) continue;
      if (typeof val !== "string") continue;
      const trimmed = val.trim();
      if (!trimmed) continue;
      if (isNumericString(trimmed)) {
        params[key] = Number(trimmed);
      } else {
        params[key] = parseExpression(trimmed);
      }
    } else if (
      (def.type === "number" || def.type === "range") &&
      typeof val === "string"
    ) {
      const trimmed = val.trim();
      if (!trimmed) continue;
      if (isNumericString(trimmed)) {
        params[key] = Number(trimmed);
      } else {
        params[key] = parseExpression(trimmed);
      }
    }
  }
}

export function prepareGeometryParams(
  rawParams = {},
  { type = rawParams.type, applyVerticalOffset = true } = {},
) {
  const preparedParams = { ...rawParams };
  const resolvedType = type || preparedParams.type;

  if (resolvedType) {
    applySchemaToParams(preparedParams, PARAM_SCHEMA[resolvedType] || {});
  }
  SCHEMA_GROUPS.forEach((group) => {
    applySchemaToParams(preparedParams, PARAM_SCHEMA[group] || {});
  });

  if (resolvedType) {
    preparedParams.type = resolvedType;
  }

  const rawScale = preparedParams.scale ?? preparedParams.Scale ?? 1;
  const scaleNum = typeof rawScale === "number" ? rawScale : Number(rawScale);
  const scale = Number.isFinite(scaleNum) ? scaleNum : 1;
  preparedParams.scale = scale;

  if (scale !== 1) {
    SCALE_LENGTH_KEYS.forEach((key) => {
      const value = preparedParams[key];
      if (value === undefined || value === null || value === "") return;
      if (typeof value === "function") {
        const scaledFn = (p) => scale * value(p);
        if (value._rawExpr !== undefined) {
          scaledFn._rawExpr = `(${value._rawExpr}) * ${scale}`;
        }
        preparedParams[key] = scaledFn;
      } else if (typeof value === "number" && Number.isFinite(value)) {
        preparedParams[key] = value * scale;
      } else if (typeof value === "string" && isNumericString(value)) {
        preparedParams[key] = Number(value) * scale;
      }
    });
  }

  if (!applyVerticalOffset) {
    preparedParams.verticalOffset = 0;
  }

  return markPreparedGeometryParams(preparedParams);
}
