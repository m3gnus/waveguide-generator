import { describe, expect, it } from 'vitest';
import { designForFamily } from '../stores/design';
import type { JobResults } from '../api/results';
import { splSubtitle } from './ResultsPanel';
import { documentLabel, engineStatusLabel, solveSummary } from './StatusBar';
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
    expect(solveSummary(design)).toBe('250 Hz – 18 kHz · 81 f · smoothing none');
    expect(solveSummary(design, { frequencyMode: 'list', frequencyListText: '500, 1000 4000', smoothing: '1/6' }))
      .toBe('500 Hz – 4 kHz · 3 f · smoothing 1/6');
    expect(solveSummary(design, { frequencyMode: 'list', frequencyListText: 'invalid' }))
      .toBe('invalid frequency list · smoothing none');
  });

  it('labels the selected engine instead of the first available capability', () => {
    const engines = [
      { name: 'metal', available: true, reason: null, version: '1.0', fast_paths: [] },
      { name: 'bempp', available: true, reason: null, version: '2.0', fast_paths: [] },
    ];
    expect(engineStatusLabel(engines, 'bempp', 'auto')).toBe('BEMPP · 2.0');
    expect(engineStatusLabel(engines, 'auto', 'auto')).toBe('METAL · 1.0');
  });

  it('shows the tracked design filename instead of a path placeholder', () => {
    expect(documentLabel('loaded-design.cfg')).toBe('loaded-design.cfg');
    expect(documentLabel('   ')).toBe('untitled design');
  });

  it('enables Load design only for jobs carrying a design snapshot', () => {
    expect(canLoadDesign({ script_snapshot: null })).toBe(false);
    expect(canLoadDesign({ script_snapshot: { formula: 'OSSE' } })).toBe(true);
  });
});
