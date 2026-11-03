import { buildLutRgba } from '../viewport/fieldPlaneColor';

/**
 * One continuous colour table per theme ramp, shared by every view that paints
 * a level as a colour: the directivity map bitmap, the Forward Beam Map and the
 * 3D balloon's vertex colours.
 *
 * The theme ramps are ten stops. Rounding a level to the nearest stop, which
 * the balloon views used to do, painted ten flat bands; interpolating between
 * the stops is what the map's visualMap already did, so a level now reads the
 * same colour in all three views. The table itself is the field plane's LUT,
 * built at a finer size because these views are drawn per pixel on the CPU.
 */
export const COLOR_TABLE_SIZE = 1024;

const tables = new Map<string, Uint8Array>();

/** RGBA texels, `COLOR_TABLE_SIZE` of them, linear between the ramp's stops. */
export function colorTable(colormap: string[]): Uint8Array {
  const key = colormap.join(',');
  const cached = tables.get(key);
  if (cached) return cached;
  const table = buildLutRgba(colormap, COLOR_TABLE_SIZE);
  tables.set(key, table);
  return table;
}

/**
 * Offset of a level's RGBA texel in a colour table. `fraction` runs from the
 * bottom of the ramp (0) to its top (1) and is clamped; a non-finite fraction
 * reads as the bottom, which is where a missing level has always been drawn.
 */
export function colorOffset(fraction: number): number {
  const clamped = Number.isFinite(fraction) ? Math.max(0, Math.min(1, fraction)) : 0;
  return Math.round(clamped * (COLOR_TABLE_SIZE - 1)) * 4;
}
