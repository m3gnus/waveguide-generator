/**
 * Helpers for extracting DI data from the per-plane result structure.
 *
 * The solver now returns DI per-plane:
 *   { frequencies: [...], horizontal: [...], vertical: [...], diagonal: [...] }
 *
 * Legacy format was:
 *   { frequencies: [...], di: [...] }
 *
 * These helpers bridge both formats.
 */

const PLANE_PRIORITY = ["horizontal", "vertical", "diagonal"];

/**
 * Extract a flat DI array from the result's di object.
 * Prefers horizontal, falls back to vertical, then diagonal.
 * Returns the legacy `di.di` array if present (backward compat).
 */
export function extractFlatDI(diData) {
  if (!diData) return [];

  // Legacy format
  if (Array.isArray(diData.di) && diData.di.length > 0) {
    return diData.di;
  }

  // Per-plane format: pick first available plane
  for (const plane of PLANE_PRIORITY) {
    if (Array.isArray(diData[plane]) && diData[plane].length > 0) {
      return diData[plane];
    }
  }

  return [];
}

/**
 * Extract per-plane DI data as a dict (for chart rendering).
 * Returns { horizontal: [...], vertical: [...] } etc., or falls back
 * to { horizontal: di.di } for legacy data.
 */
export function extractPerPlaneDI(diData) {
  if (!diData) return {};

  const result = {};
  let hasPerPlane = false;

  for (const plane of PLANE_PRIORITY) {
    if (Array.isArray(diData[plane]) && diData[plane].length > 0) {
      result[plane] = diData[plane];
      hasPerPlane = true;
    }
  }

  if (hasPerPlane) return result;

  // Legacy fallback
  if (Array.isArray(diData.di) && diData.di.length > 0) {
    return { horizontal: diData.di };
  }

  return {};
}
