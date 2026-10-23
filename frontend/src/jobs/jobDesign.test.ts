import { beforeEach, describe, expect, it } from 'vitest';
import { resetDesignStore, serializeDesign, serializeSolveDesign, useDesignStore } from '../stores/design';
import parentLegacySubmission from './fixtures/legacySidecar.parent.solve.json?raw';
import {
  canLoadJobDesign,
  canOpenJobModel,
  hydrateJobDesign,
  jobDesignAvailability,
  jobRerunState,
  replaceWithJobDesign,
} from './jobDesign';

describe('versioned job design snapshots', () => {
  beforeEach(() => resetDesignStore());

  it.each([false, true])('reopens legacy sidecars with the parent submission bytes (versioned: %s)', versioned => {
    const wire = { formula: 'OSSE', R: 140, _expressions: { R: { value: null, raw: '70*(1+cos(p))' } } };
    const job = { script_snapshot: versioned ? { version: 1, design: wire } : wire };
    expect(replaceWithJobDesign(job)).toBe(true);
    const submitted = serializeSolveDesign(useDesignStore.getState().design);
    // Captured with the hydrator and submission serializer from 417641762.
    expect(JSON.stringify(submitted)).toBe(parentLegacySubmission.trimEnd());
    expect(submitted.R).toBe(140);
    expect(submitted).not.toHaveProperty('_expressions');
  });

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

  it('reports the server verdict for a stored design', () => {
    const job = {
      script_snapshot: RECOVERED,
      design_availability: {
        reopenable: true, source: 'v2-snapshot', reason_code: 'ok', reason: null, note: null,
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
        reopenable: true, source: 'v2-snapshot', reason_code: 'ok', reason: null, note: null,
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

it('opens retained imported inputs independently of parametric design readability', () => {
  expect(canOpenJobModel({ script_snapshot: null, config_summary: { geometry_type: 'imported' } })).toBe(true);
  expect(canOpenJobModel({ script_snapshot: null, config_summary: {} })).toBe(false);
});

it('restores a run identity and keeps document identity paired through undo and redo', async () => {
  const { resetDocumentStore, useDocumentStore } = await import('../stores/document');
  resetDocumentStore();
  resetDesignStore();
  useDocumentStore.getState().setDesignName('Working B');
  const beforeUid = useDocumentStore.getState().designUid;
  const job = {
    design_uid: 'wgu_01K00000000000000000000000', design_name: 'Run A',
    script_snapshot: { version: 1, design: { formula: 'OSSE', R: 180 } },
  };
  expect(replaceWithJobDesign(job, { keepHistory: true })).toBe(true);
  expect(useDocumentStore.getState()).toMatchObject({ designUid: job.design_uid, designName: 'Run A', identity: null });
  useDesignStore.getState().undo();
  expect(useDocumentStore.getState()).toMatchObject({ designUid: beforeUid, designName: 'Working B' });
  useDesignStore.getState().redo();
  expect(useDocumentStore.getState().designUid).toBe(job.design_uid);
  expect(replaceWithJobDesign({ script_snapshot: job.script_snapshot })).toBe(true);
  expect(useDocumentStore.getState().designUid).not.toBe(job.design_uid);
});

it('keeps the live CAD link when browsing an older run of the same permanent design', async () => {
  const { useDocumentStore } = await import('../stores/document');
  resetDesignStore();
  const uid = 'wgu_01K00000000000000000000000';
  const identity = { designId: 'wgd_current', lineageId: 'wgl_current', baseEditVersion: 7 };
  useDocumentStore.getState().setDesignUid(uid);
  useDocumentStore.getState().setDesignName('Renamed A');
  useDocumentStore.getState().setCadLink(identity, 'current');
  expect(replaceWithJobDesign({ design_uid: uid, design_name: 'Old A',
    script_snapshot: { version: 1, design: { formula: 'OSSE', R: 191 } },
  }, { keepHistory: true })).toBe(true);
  expect(useDocumentStore.getState()).toMatchObject({ designUid: uid, identity, designName: 'Renamed A' });
  useDesignStore.getState().undo();
  expect(useDocumentStore.getState()).toMatchObject({ designUid: uid, identity, classification: 'current' });
});


it('records an identity boundary for identical geometry, and undoes it before earlier geometry', async () => {
  const { resetDocumentStore, useDocumentStore } = await import('../stores/document');
  resetDocumentStore();
  resetDesignStore();
  const original = structuredClone(useDesignStore.getState().design);
  const identity = { designId: 'wgd_a', lineageId: 'wgl_a', baseEditVersion: 4 };
  useDocumentStore.getState().setDesignName('A');
  useDocumentStore.getState().setCadLink(identity, 'current');
  useDesignStore.getState().updateField('a', original.a! + 1);
  const beforeRecall = structuredClone(useDesignStore.getState().design);
  const geometry = serializeDesign(beforeRecall);
  const uidA = useDocumentStore.getState().designUid;
  const uidB = 'wgu_01K00000000000000000000001';
  expect(replaceWithJobDesign({ design_uid: uidB, design_name: 'B',
    script_snapshot: { version: 1, design: geometry },
  }, { keepHistory: true })).toBe(true);
  expect(useDesignStore.getState().design).toEqual(beforeRecall);
  expect(serializeDesign(useDesignStore.getState().design)).toEqual(geometry);
  expect(useDocumentStore.getState()).toMatchObject({ designUid: uidB, designName: 'B', identity: null });
  useDesignStore.getState().undo();
  expect(serializeDesign(useDesignStore.getState().design)).toEqual(geometry);
  expect(useDocumentStore.getState()).toMatchObject({ designUid: uidA, designName: 'A', identity });
  useDesignStore.getState().redo();
  expect(useDocumentStore.getState()).toMatchObject({ designUid: uidB, designName: 'B', identity: null });
  expect(serializeDesign(useDesignStore.getState().design)).toEqual(geometry);
  useDesignStore.getState().undo();
  useDesignStore.getState().undo();
  expect(useDesignStore.getState().design.a).toBe(original.a);
  expect(useDocumentStore.getState().designUid).toBe(uidA);
});
