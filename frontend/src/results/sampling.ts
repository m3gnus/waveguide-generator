import type { ResultPayload } from './types';

/** Present only for flagged results; legacy artifacts keep their exact bytes. */
export function samplingProvenance(result: ResultPayload) {
  if (!result.frequency_status) return null;
  const solved = result.frequencies.filter((_, i) => result.frequency_status![i] === 'solved');
  const metadata = result.metadata?.adaptive_sampling;
  const adaptive = metadata && typeof metadata === 'object' && !Array.isArray(metadata)
    ? metadata as Record<string, unknown> : {};
  return {
    ...(typeof adaptive.stop_reason === 'string' ? { stop_reason: adaptive.stop_reason } : {}),
    ...(typeof adaptive.estimate_db === 'number' && Number.isFinite(adaptive.estimate_db)
      ? { estimate_db: adaptive.estimate_db } : {}),
    ...(typeof adaptive.native_solved_count === 'number' && Number.isFinite(adaptive.native_solved_count)
      ? { native_solved_count: adaptive.native_solved_count } : {}),
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
    ...(sampling.stop_reason ? [`Adaptive stop reason: ${sampling.stop_reason.replaceAll('_', ' ')}`] : []),
    ...(sampling.estimate_db !== undefined ? [`Adaptive disagreement estimate: ${sampling.estimate_db} dB`] : []),
    ...(sampling.native_solved_count !== undefined ? [`Native frequency solves (including coverage): ${sampling.native_solved_count}`] : []),
  ] : [];
}

export function samplingComments(result: ResultPayload, prefix = '#'): string {
  return samplingNotes(result).map((note) => `${prefix} ${note}\n`).join('');
}
