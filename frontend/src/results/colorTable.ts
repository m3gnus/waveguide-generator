/**
 * One continuous colour table per theme ramp, shared by every view that paints
 * a level as a colour: the directivity map bitmap, the Forward Beam Map and the
 * 3D balloon's vertex colours.
 *
 * The theme ramps are ten stops. Rounding a level to the nearest stop, which
 * the balloon views used to do, painted ten flat bands; interpolating between
 * the stops is what the map's visualMap already did, so a level now reads the
 * same colour in all three views.
 */
export const COLOR_TABLE_SIZE = 1024;

const tables = new Map<string, Uint8ClampedArray>();

function hexToRgb(hex: string): [number, number, number] {
  const digits = hex.replace('#', '');
  const full = digits.length === 3 ? digits.split('').map((digit) => digit + digit).join('') : digits;
  const value = Number.parseInt(full, 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

/** RGB triplets, `COLOR_TABLE_SIZE` of them, linear between the ramp's stops. */
export function colorTable(colormap: string[]): Uint8ClampedArray {
  const key = colormap.join(',');
  const cached = tables.get(key);
  if (cached) return cached;
  const stops = colormap.map(hexToRgb);
  const table = new Uint8ClampedArray(COLOR_TABLE_SIZE * 3);
  for (let entry = 0; entry < COLOR_TABLE_SIZE; entry += 1) {
    const position = stops.length > 1 ? (entry / (COLOR_TABLE_SIZE - 1)) * (stops.length - 1) : 0;
    const left = Math.min(Math.max(0, stops.length - 2), Math.floor(position));
    const right = Math.min(stops.length - 1, left + 1);
    const t = position - left;
    for (let channel = 0; channel < 3; channel += 1) {
      table[entry * 3 + channel] = Math.round(stops[left][channel] + (stops[right][channel] - stops[left][channel]) * t);
    }
  }
  tables.set(key, table);
  return table;
}

/**
 * Offset of a level's RGB triplet in a colour table. `fraction` runs from the
 * bottom of the ramp (0) to its top (1) and is clamped; a non-finite fraction
 * reads as the bottom, which is where a missing level has always been drawn.
 */
export function colorOffset(fraction: number): number {
  const clamped = Number.isFinite(fraction) ? Math.max(0, Math.min(1, fraction)) : 0;
  return Math.round(clamped * (COLOR_TABLE_SIZE - 1)) * 3;
}

