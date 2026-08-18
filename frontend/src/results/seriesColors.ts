/**
 * Series colours keyed by label rather than by position.
 *
 * A run's colour used to be its index in the series list, so anything that
 * changed the list changed the colours. Adding a comparison run recoloured
 * every run behind it. The impedance chart drops runs with no impedance block
 * *before* assigning colours, so the survivors were recoloured whenever a run
 * without one entered or left the selection. Directivity index was worse
 * still: it keys on the run while every run draws one metric and on the metric
 * as soon as any run draws several, so a single run gaining a second DI curve
 * reassigned the entire chart. Reading a comparison by colour was therefore
 * only reliable while the selection held perfectly still -- which is exactly
 * when nobody needs to read it.
 *
 * The label picks the slot instead, so a run keeps its colour for as long as it
 * keeps its name, no matter what else joins or leaves the chart.
 *
 * Two labels can prefer the same slot. The second probes forward to the next
 * free one, which preserves the palette's guarantee that the first
 * `palette.length` series are visually distinct -- worth more than perfect
 * stability, since a comparison whose runs share a colour cannot be read at
 * all. That probe is the one remaining way a colour can move, and only between
 * labels that collide, which is strictly narrower than the old behaviour where
 * any change at all shifted everything after it.
 */

/** FNV-1a: a short, well-mixed slot from a short label. */
function labelSlot(label: string, size: number): number {
  let hash = 0x811c9dc5;
  for (let index = 0; index < label.length; index += 1) {
    hash ^= label.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0) % size;
}

/**
 * Map each label to a palette entry, stably and without repeating a colour
 * until the palette runs out.
 *
 * Duplicate labels collapse onto one colour, which is what a caller expanding
 * the same run into several traces wants.
 */
export function seriesColorsByLabel(labels: string[], palette: string[], fallback: string): Map<string, string> {
  const size = Math.max(1, palette.length);
  const taken = new Set<number>();
  const colors = new Map<string, string>();
  for (const label of labels) {
    if (colors.has(label)) continue;
    const preferred = labelSlot(label, size);
    let slot = preferred;
    // Past `size` probes every slot is taken and the palette simply repeats,
    // deterministically, from the label's own preference.
    for (let probe = 1; probe <= size && taken.has(slot); probe += 1) slot = (preferred + probe) % size;
    taken.add(slot);
    colors.set(label, palette[slot] ?? fallback);
  }
  return colors;
}
