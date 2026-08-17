/**
 * Parsing for an explicitly typed frequency sweep.
 *
 * Kept apart from the solve-options store so the `.cfg` writer can validate a
 * stored list without importing the store that imports the writer.
 */

/** Server-side ceiling in ``SolveOptions.frequencies_hz``; mirrored for fast feedback. */
export const MAX_FREQUENCY_POINTS = 401;

export interface FrequencyListParse {
  frequencies: number[] | null;
  error: string | null;
}

/**
 * Parse a free-typed sweep into the ``frequencies_hz`` contract.
 *
 * The rules mirror the server's validator exactly, so the dialog can reject a
 * bad list before a solve is queued. Nothing here repairs input: a list that
 * cannot be read as written is an error, never a quietly sorted or truncated
 * sweep the user did not ask for.
 */
export function parseFrequencyList(text: string): FrequencyListParse {
  const tokens = text.split(/[\s,;]+/).filter((token) => token.length > 0);
  if (tokens.length === 0) return { frequencies: null, error: 'Enter at least one frequency.' };
  if (tokens.length > MAX_FREQUENCY_POINTS) {
    return { frequencies: null, error: `At most ${MAX_FREQUENCY_POINTS} frequencies (got ${tokens.length}).` };
  }
  const frequencies: number[] = [];
  for (const token of tokens) {
    const value = Number(token);
    if (!Number.isFinite(value)) return { frequencies: null, error: `"${token}" is not a number.` };
    if (value <= 0) return { frequencies: null, error: `Frequencies must be above 0 Hz ("${token}").` };
    frequencies.push(value);
  }
  for (let index = 1; index < frequencies.length; index += 1) {
    if (frequencies[index] <= frequencies[index - 1]) {
      return {
        frequencies: null,
        error: `Frequencies must ascend: ${frequencies[index]} follows ${frequencies[index - 1]}.`,
      };
    }
  }
  return { frequencies, error: null };
}
