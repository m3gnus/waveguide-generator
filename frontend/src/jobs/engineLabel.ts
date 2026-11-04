/** Display names for the engine slugs `solve_options.engine` carries.
 * Model details' settings line (`CadModelDetails.tsx`) prefers a capability verdict's own
 * `label` when a solve plan is on hand (CAD Link mode, before a job exists);
 * this is what both that line and this module fall back to otherwise --
 * one name per engine, never a bare `.toUpperCase()` of the slug. */
const ENGINE_LABELS: Record<string, string> = {
  auto: 'Auto',
  metal: 'Metal-BEM',
  beat: 'BEAT Engine',
  'beat-cpu': 'BEAT CPU',
  'beat-metal': 'BEAT Metal',
  'beat-cuda': 'BEAT CUDA',
  'beat-rocm': 'BEAT ROCm',
  official_beat: 'Official BEAT',
  bempp: 'BEMPP',
  circsym: 'Axisymmetric (removed)',
  axisym: 'Axisymmetric (removed)',
  dryrun: 'Dry run',
};

export function resolveEngineLabel(engine: string | null | undefined): string | null {
  const value = typeof engine === 'string' ? engine.trim() : '';
  if (!value) return null;
  const known = ENGINE_LABELS[value.toLowerCase()];
  if (known) return known;
  return value.split(/[_-]+/).filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(' ');
}

