import { beforeEach, describe, expect, it } from 'vitest';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { hydrateJobDesign, replaceWithJobDesign } from './jobDesign';

describe('versioned job design snapshots', () => {
  beforeEach(() => resetDesignStore());

  it('hydrates canonical schema wire and starts a new undo epoch on Load design', () => {
    useDesignStore.getState().updateField('a', 33);
    const job = {
      script_snapshot: {
        version: 1,
        design: {
          formula: 'OSSE',
          r0: { value: 12.7, raw: '6.35*2' },
          mesh: { quadrants: 14 },
        },
      },
    };
    const hydrated = hydrateJobDesign(job);
    expect(hydrated?.r0).toBe(12.7);
    expect(hydrated?._expressions?.r0).toEqual({ value: 12.7, raw: '6.35*2' });
    expect(hydrated?.quadrants).toEqual([1, 4]);

    expect(replaceWithJobDesign(job)).toBe(true);
    expect(useDesignStore.temporal.getState().pastStates).toEqual([]);
    useDesignStore.getState().undo();
    expect(useDesignStore.getState().design.r0).toBe(12.7);
  });

  it('keeps the working design undoable when a run is selected for viewing', () => {
    useDesignStore.getState().updateField('a', 33);
    const job = {
      script_snapshot: { version: 1, design: { formula: 'OSSE', r0: { value: 12.7, raw: '6.35*2' }, a: 41 } },
    };

    expect(replaceWithJobDesign(job, { keepHistory: true })).toBe(true);
    expect(useDesignStore.getState().design.r0).toBe(12.7);
    expect(useDesignStore.getState().design.a).toBe(41);

    // Clicking through runs is browsing, not opening a document: the design
    // that was on screen beforehand must still come back.
    useDesignStore.getState().undo();
    expect(useDesignStore.getState().design.a).toBe(33);
  });
});
