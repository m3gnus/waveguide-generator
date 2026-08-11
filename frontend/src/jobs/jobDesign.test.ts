import { beforeEach, describe, expect, it } from 'vitest';
import { resetDesignStore, useDesignStore } from '../stores/design';
import {
  canLoadJobDesign,
  hydrateJobDesign,
  jobDesignAvailability,
  jobRerunState,
  replaceWithJobDesign,
} from './jobDesign';

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

describe('what a job says about its own design', () => {
  const RECOVERED = { version: 1, design: { formula: 'OSSE', r0: { value: 12.7, raw: '12.7' } } };

  it('reports the server verdict for a recovered v1 job', () => {
    const job = {
      script_snapshot: RECOVERED,
      design_availability: {
        reopenable: true, source: 'v1-design-state', reason_code: 'recovered', reason: null, note: null,
      },
    } as const;
    expect(jobDesignAvailability(job).reopenable).toBe(true);
    expect(jobDesignAvailability(job).reason).toBeNull();
    expect(canLoadJobDesign(job)).toBe(true);
    // The design is genuinely usable, not merely declared usable.
    expect(hydrateJobDesign(job)?.r0).toBe(12.7);
  });

  it('never reports an empty reason for a job it is refusing', () => {
    const availability = jobDesignAvailability({
      script_snapshot: null,
      design_availability: {
        reopenable: false, source: 'none', reason_code: 'no_stored_design', reason: '', note: null,
      },
    });
    expect(availability.reopenable).toBe(false);
    expect(availability.reason).toBeTruthy();
  });

  it('refuses to load a design the server called reopenable but the client cannot read', () => {
    // Belt and braces across the wire: a verdict is not a substitute for the
    // design actually hydrating, and Load design must not act on a null.
    const job = {
      script_snapshot: { version: 1, design: { formula: 'NOT-A-FORMULA' } },
      design_availability: {
        reopenable: true, source: 'v1-design-state', reason_code: 'recovered', reason: null, note: null,
      },
    } as const;
    expect(hydrateJobDesign(job)).toBeNull();
    expect(canLoadJobDesign(job)).toBe(false);
    expect(replaceWithJobDesign(job)).toBe(false);
    expect(jobRerunState(job).reason).toContain('this version of the interface');
  });

  it('falls back to the snapshot when the server sent no verdict at all', () => {
    expect(jobDesignAvailability({ script_snapshot: RECOVERED }).reopenable).toBe(true);
    const legacy = jobDesignAvailability({ script_snapshot: { params: { type: 'OSSE' } } });
    expect(legacy.reopenable).toBe(false);
    expect(legacy.reason).toContain('cannot be reopened');
  });

  it('reruns an immutable CAD import without pretending it can be reopened', () => {
    const imported = {
      script_snapshot: null,
      design_availability: {
        reopenable: false, source: 'cad-import', reason_code: 'imported_geometry',
        reason: 'This job came from CAD. The linked design is design-123.', note: null,
      },
    } as const;
    expect(canLoadJobDesign(imported)).toBe(false);
    expect(jobRerunState(imported)).toEqual({ enabled: true, reason: null });
  });
});
