import { describe, expect, it } from 'vitest';
import { designForFamily } from '../stores/design';
import type { JobResults } from '../api/results';
import { splSubtitle } from './ResultsPanel';
import { solveSummary } from './StatusBar';
import { canLoadDesign } from './JobsPanel';

describe('frontend result and status labels', () => {
  it('labels SPL as absolute at the effective observation distance', () => {
    const result: JobResults = {
      frequencies: [], metadata: { observation: { requested_distance_m: 2, effective_distance_m: 1.75 } },
    };
    expect(splSubtitle(result)).toBe('absolute · 1.75 m');
    expect(splSubtitle({ frequencies: [] })).toBe('absolute · distance unspecified');
  });

  it('derives the solve range and count from the design', () => {
    const design = designForFamily('OSSE');
    design.simulation = { ...design.simulation, f1: 250, f2: 18_000, num_frequencies: 81 };
    expect(solveSummary(design)).toBe('250 Hz – 18 kHz · 81 f · smoothing not configured');
  });

  it('enables Load design only for jobs carrying a design snapshot', () => {
    expect(canLoadDesign({ script_snapshot: null })).toBe(false);
    expect(canLoadDesign({ script_snapshot: { formula: 'OSSE' } })).toBe(true);
  });
});
