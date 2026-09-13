/**
 * A content key for a design, as a file would carry it.
 *
 * WG has no Save, so before New, Open or a project switch replaces the design
 * on screen the question is not "does this differ from its file?" but "does
 * this design exist anywhere else?". The key answers the "is it the same
 * design?" half: two designs with equal keys would write the same `.cfg`.
 *
 * It is built once, from the file writer's own projection -- `serializeDesign`,
 * then the name, ATH polar and `WG.Solve` overlay (`composeDesignFileWire`) --
 * and canonicalized: object keys sorted, arrays left in their order, and
 * expression text and null (an omitted value) kept exactly as the file keeps
 * them. `documentSettingsSignature()` is deliberately not added on top: the
 * overlay already carries those settings, and they would count twice.
 *
 * Nothing here reads a store. Every input is an explicit snapshot, so a caller
 * keys a stored run's design and settings as easily as the ones on screen.
 */
import { serializeDesign, type DesignDocument } from './design';
import { composeDesignFileWire } from './designWire';
import { polarConfigFromUi, type PolarUiState } from './solveOptions';
import type { WgSolveSettings } from './wgSolveBlock';

/**
 * Prefixed to every key. Change it with any change to the canonical form, so a
 * key written under one form never compares equal, or unequal, by accident.
 */
export const DESIGN_CONTENT_KEY_VERSION = 'v1';

/** Everything a `.cfg` carries that does not live in the design document. */
export interface DesignFileSettings {
  /**
   * The directivity settings: either the editor's own form, resolved here, or
   * the polar config a run recorded.
   */
  polar: { ui: PolarUiState } | { config: unknown };
  /** WG's own solve settings, or null when there are none to write. */
  solve: WgSolveSettings | null;
}

/**
 * The key, or null when this design cannot be keyed.
 *
 * Null is an answer, not an error: a half-typed directivity grid makes
 * `polarConfigFromUi` throw, and a design that cannot be keyed is never the
 * same as anything, so it counts as existing nowhere else.
 */
export function designContentKey(
  design: DesignDocument,
  name: string,
  fileSettings: DesignFileSettings,
): string | null {
  try {
    const polar = 'ui' in fileSettings.polar
      ? polarConfigFromUi(fileSettings.polar.ui)
      : fileSettings.polar.config;
    const wire = composeDesignFileWire(serializeDesign(design), polar, fileSettings.solve, name);
    return `${DESIGN_CONTENT_KEY_VERSION}:${canonicalJson(wire)}`;
  } catch {
    return null;
  }
}

/**
 * JSON with object keys in one order. An undefined member is left out, as
 * JSON leaves it out; null stays, because null is how the wire says a value
 * was omitted.
 */
function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map((item) => canonicalJson(item)).join(',')}]`;
  if (value !== null && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .filter((key) => record[key] !== undefined)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
      .join(',')}}`;
  }
  return JSON.stringify(value) ?? 'null';
}
