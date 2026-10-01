import type { ResultPayload } from './types';

/** Present only for flagged results; legacy artifacts keep their exact bytes. */
export function samplingProvenance(result: ResultPayload) {
  if (!result.frequency_status) return null;
  const solved = result.frequencies.filter((_, i) => result.frequency_status![i] === 'solved');
  return {
    warning: 'Adaptive sampling: interpolated rows are reconstructed estimates; narrow resonances can be missed.',
    solved_count: solved.length,
    interpolated_count: result.frequency_status.filter((value) => value === 'interpolated').length,
    solved_frequencies_hz: solved,
    frequency_status: result.frequency_status,
  };
}

export function samplingNotes(result: ResultPayload): string[] {
  const sampling = samplingProvenance(result);
  return sampling ? [
    sampling.warning,
    `Sampling counts: ${sampling.solved_count} solved, ${sampling.interpolated_count} interpolated`,
    `Solved requested frequencies (Hz): ${sampling.solved_frequencies_hz.join(', ')}`,
    'All other requested frequency rows are interpolated.',
  ] : [];
}

export function samplingComments(result: ResultPayload, prefix = '#'): string {
  return samplingNotes(result).map((note) => `${prefix} ${note}\n`).join('');
}
