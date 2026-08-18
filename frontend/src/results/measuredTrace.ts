/**
 * Read a measured frequency response so it can be drawn beside a solve.
 *
 * This is the return leg of the FRD export in `frd.ts`: the user exports a
 * simulated response, measures the built waveguide in REW, and wants the two
 * curves on one chart without leaving the app. The accepted dialect is
 * therefore the union of what this app writes and what REW writes, which is
 * also what VituixCAD and every other FRD consumer reads:
 *
 *   - comment lines introduced by `*`, `;`, `#` or `//`
 *   - blank lines anywhere
 *   - rows of two or three numbers: freq_hz, spl_db and an optional phase_deg
 *
 * Nothing here interprets the header. A measurement's distance, gain and
 * smoothing are recorded by the person taking it (see
 * docs/validation/MEASUREMENT-TEMPLATE.md); guessing them from a free-text
 * comment would silently shift a curve the user believes is raw.
 */

export interface MeasuredPoint {
  frequencyHz: number;
  splDb: number;
  /** Present only when the file carries a third column. */
  phaseDeg: number | null;
}

export interface MeasuredTrace {
  label: string;
  points: MeasuredPoint[];
}

const COMMENT_PREFIXES = ['*', ';', '#', '//'];

/**
 * Whitespace, tab and comma. Comma is on neither REW's nor VituixCAD's list of
 * accepted separators, but files carrying it do exist, so it is split on --
 * with the field-count guard below standing in for the ambiguity: a
 * comma-decimal export splits into four or six fields and is rejected rather
 * than read as a curve with its digits in the wrong places.
 */
const DELIMITER = /[\s,]+/;

/** Deliberately stricter than `Number`, which reads '', '0x10' and 'Infinity'. */
const NUMBER = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;

function isComment(line: string): boolean {
  return COMMENT_PREFIXES.some((prefix) => line.startsWith(prefix));
}

/** The file's own name is the only label a measurement reliably carries. */
export function measuredTraceLabel(filename: string): string {
  const base = filename.split(/[\\/]/).pop() ?? filename;
  const stem = base.replace(/\.[^.]+$/, '').trim();
  return stem || 'Measured';
}

/**
 * Parse an FRD/REW-style response.
 *
 * Unreadable rows are skipped rather than fatal: real exports carry trailing
 * marker lines, and one of them should not cost the whole measurement. A file
 * that yields fewer than two usable points cannot be drawn as a curve at all,
 * and that is where this throws -- the caller surfaces the message.
 */
export function parseMeasuredTrace(text: string, filename: string): MeasuredTrace {
  const points: MeasuredPoint[] = [];
  let skipped = 0;
  let lastFrequency = 0;

  // Strip a BOM: a UTF-8 file saved by Windows tooling otherwise turns its
  // first character into part of the first token.
  for (const rawLine of text.replace(/^\ufeff/, '').split(/\r\n|\r|\n/)) {
    const line = rawLine.trim();
    if (!line || isComment(line)) continue;

    const fields = line.split(DELIMITER);
    if (fields.length < 2 || fields.length > 3 || !fields.every((field) => NUMBER.test(field))) {
      skipped += 1;
      continue;
    }

    const [frequencyHz, splDb, phaseDeg] = fields.map(Number);
    // Frequencies must ascend strictly: the chart's x axis is logarithmic, so a
    // zero or negative frequency has no position on it, and a repeated or
    // descending one means the file is not a single swept response.
    if (!Number.isFinite(frequencyHz) || !Number.isFinite(splDb) || frequencyHz <= lastFrequency) {
      skipped += 1;
      continue;
    }
    if (fields.length === 3 && !Number.isFinite(phaseDeg)) {
      skipped += 1;
      continue;
    }

    lastFrequency = frequencyHz;
    points.push({ frequencyHz, splDb, phaseDeg: fields.length === 3 ? phaseDeg : null });
  }

  if (points.length < 2) {
    throw new Error(
      `${measuredTraceLabel(filename)} holds ${points.length} usable measurement point${points.length === 1 ? '' : 's'}`
      + `${skipped ? ` and ${skipped} unreadable row${skipped === 1 ? '' : 's'}` : ''}.`
      + ' Expected an FRD or REW export with rows of frequency, SPL and optional phase in ascending frequency.',
    );
  }

  return { label: measuredTraceLabel(filename), points };
}
