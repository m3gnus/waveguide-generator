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

/**
 * A label's colour identity: its display name without the run number.
 *
 * `runDisplayName` prefixes every run with `#<run_number> · `, and run numbers
 * are consecutive per created job. Hashing the display name therefore gave the
 * *same design, re-solved with nothing changed*, a new key, a new slot and a
 * new colour on every solve -- which is not a comparison the user asked for,
 * it is the chart forgetting what it was drawing a moment ago. Only the part
 * that identifies the design decides the colour; the run number is left to the
 * legend, where it distinguishes the runs without recolouring them.
 *
 * Labels that carry no run number -- aperture names, synthetic single-run
 * entries -- pass through untouched.
 */
export function stableColorKey(label: string): string {
  return label.replace(/^#\d+ · /, '');
}

/**
 * The run a label belongs to, without the ` · <angle>` / ` · <channel>` tail
 * that derived traces append. Used only to find the primary run's trace; slots
 * are still keyed on the whole label so a run's angles stay distinguishable.
 */
export function runOfLabel(label: string): string {
  const key = stableColorKey(label);
  const separator = key.indexOf(' · ');
  return separator === -1 ? key : key.slice(0, separator);
}

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
 *
 * `primaryRun`, when given, names the run the panel is built around -- see
 * `runOfLabel`. Its first trace takes the accent slot before any hash can
 * claim it, so the run being examined reads as the subject of its own chart
 * rather than as an arbitrary member of the palette. It is matched by run
 * rather than by position on purpose: a chart that filters its items (the
 * impedance chart drops runs with no impedance block) simply finds nothing to
 * pin, instead of promoting whichever run happens to survive first and
 * recolouring the chart around it -- the positional failure this module exists
 * to prevent.
 */
export function seriesColorsByLabel(
  labels: string[],
  palette: string[],
  fallback: string,
  primaryRun?: string,
): Map<string, string> {
  const size = Math.max(1, palette.length);
  const taken = new Set<number>();
  const colors = new Map<string, string>();
  const pinned = primaryRun === undefined
    ? undefined
    : labels.find((label) => runOfLabel(label) === primaryRun);
  if (pinned !== undefined) {
    taken.add(0);
    colors.set(pinned, palette[0] ?? fallback);
  }
  for (const label of labels) {
    if (colors.has(label)) continue;
    const preferred = labelSlot(stableColorKey(label), size);
    let slot = preferred;
    // Past `size` probes every slot is taken and the palette simply repeats,
    // deterministically, from the label's own preference.
    for (let probe = 1; probe <= size && taken.has(slot); probe += 1) slot = (preferred + probe) % size;
    taken.add(slot);
    colors.set(label, palette[slot] ?? fallback);
  }
  return colors;
}
