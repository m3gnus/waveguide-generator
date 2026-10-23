import { beforeEach, describe, expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { designForFamily, resetDesignStore, serializeDesign, serializeSolveDesign, useDesignStore, type DesignDocument } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { hydrateDesignDocument } from '../api/designIo';
import { designFingerprint, jobDesignFingerprint } from './runCoherence';
import { currentModelScene, currentModel, runContextMarker, runVerdict, runProvenanceMarker, shownResult, spatialResult, type CurrentModel } from '../model/currentModel';

let nextJob = 0;

/** A run carrying the design it was solved from, the way the server stores it. */
function parametric(design: DesignDocument | null): JobItem {
  nextJob += 1;
  return {
    id: `job-${nextJob}`,
    config_summary: {},
    design_revision: 1,
    script_snapshot: design ? { version: 1, design: serializeDesign(design) } : null,
  } as unknown as JobItem;
}

function cad(ingestId: string | null): JobItem {
  nextJob += 1;
  return {
    id: `job-${nextJob}`,
    config_summary: { geometry_type: 'imported' },
    design_revision: 0,
    script_snapshot: null,
    cad_source: { ingest_id: ingestId },
  } as unknown as JobItem;
}

function contextFor(design: DesignDocument, overrides: Partial<CurrentModel> = {}): CurrentModel {
  return { kind: 'parametric', key: designFingerprint(design), lineage: 'wgd_live', label: 'Test', viewportShowing: true, ...overrides };
}

describe('run coherence', () => {
  beforeEach(() => {
    resetDesignStore();
    resetCadReturnStore();
    resetDocumentStore();
    workspaceModeStore.setMode('parametric');
    importedMeshStore.clear();
  });

  it('classifies a parametric run by the design it was solved from', () => {
    const design = designForFamily('OSSE');
    const run = parametric(design);
    expect(runVerdict(run, contextFor(design))).toBe('current');
    // The revision counter moves on every edit, undo and file open; the
    // verdict must not, or only the newest solve could ever read current.
    expect(runVerdict(run, contextFor(design, {}))).toBe('current');
  });

  it('reads current again once an edit is undone back to what was solved', () => {
    const run = parametric(useDesignStore.getState().design);
    expect(runVerdict(run, contextFor(useDesignStore.getState().design))).toBe('current');

    useDesignStore.getState().updateField('R', 180);
    expect(runVerdict(run, contextFor(useDesignStore.getState().design))).toBe('earlier-version');

    useDesignStore.getState().undo();
    expect(runVerdict(run, contextFor(useDesignStore.getState().design))).toBe('current');
  });

  it('ignores the machine-local solver path', () => {
    const design = designForFamily('OSSE');
    const respelled = structuredClone(design);
    respelled.simulation.solver_mode = 'bempp';
    expect(designFingerprint(respelled)).toBe(designFingerprint(design));
  });

  it('separates a real geometry edit', () => {
    const design = designForFamily('OSSE');
    const edited = structuredClone(design);
    edited.L = (edited.L ?? 0) + 1;
    expect(designFingerprint(edited)).not.toBe(designFingerprint(design));
    expect(runVerdict(parametric(design), contextFor(edited))).toBe('earlier-version');
  });

  it('does not mistake an unreadable snapshot for the editor model', () => {
    expect(runVerdict(parametric(null), contextFor(designForFamily('OSSE')))).toBe('other-model');
  });

  it('classifies imported runs by the ingestion in the viewport', () => {
    const design = designForFamily('OSSE');
    const cadContext = contextFor(design, { kind: 'cad', key: 'wgi_live', ingestId: 'wgi_live' });
    expect(runVerdict(cad('wgi_live'), cadContext)).toBe('current');
    expect(runVerdict(cad('wgi_other'), cadContext)).toBe('other-model');
    expect(runVerdict(cad(null), { ...cadContext, key: null, ingestId: null })).toBe('other-model');
    expect(runVerdict(cad('wgi_live'), contextFor(design))).toBe('other-model');
    expect(runVerdict(parametric(design), cadContext)).toBe('other-model');
  });

  it('matches unchanged re-sends and rejects both old geometry-hash collisions', () => {
    const design = designForFamily('OSSE');
    const current = contextFor(design, {
      kind: 'cad', key: 'prepared-a', ingestId: 'wgi_new',
    });
    const run = cad('wgi_old');
    run.cad_source!.transformed_geometry_hash = 'same-centre-area';
    run.cad_source!.solve_model_sha256 = 'prepared-a';
    expect(runVerdict(run, current)).toBe('current');

    // Declared mirrored half versus undeclared open shell: identical STEP and
    // viewport fingerprint, but the effective solve domain differs.
    run.cad_source!.solve_model_sha256 = 'mirrored-half';
    expect(runVerdict(run, current)).toBe('other-model');
    // 2x2 versus 4x1 faces: equal centres and areas, different prepared mesh.
    run.cad_source!.solve_model_sha256 = 'four-by-one';
    expect(runVerdict(run, current)).toBe('other-model');
    // Legacy hashes cannot prove cross-ingestion equality.
    run.cad_source!.solve_model_sha256 = null;
    expect(runVerdict(run, current)).toBe('other-model');
    run.cad_source!.solve_model_sha256 = 'prepared-a';
    expect(runVerdict(run, { ...current, viewportShowing: false })).not.toBe('current');
  });

  it('keeps the marker through a restore until the mesh is visible, including failure', () => {
    const design = designForFamily('OSSE');
    const run = cad('wgi_archive');
    const before = contextFor(design, { kind: 'cad', key: 'wgi_live', ingestId: 'wgi_live' });
    expect(runVerdict(run, before)).toBe('other-model');
    const loading = { ...before, key: 'wgi_archive', ingestId: 'wgi_archive', viewportShowing: false };
    expect(runContextMarker(run, loading)).toBe('Run model not loaded');
    expect(runContextMarker(run, loading)).toBe('Run model not loaded'); // failed load retains the marker
    expect(runContextMarker(run, { ...loading, viewportShowing: true })).toBeNull();
  });

  it('recognizes the selected CAD fallback while a prior solve mesh is cached', () => {
    importedMeshStore.setCadSolver({ source: 'cad', ingestId: 'old' } as never);
    importedMeshStore.setCad({ source: 'cad', ingestId: 'new' } as never);
    importedMeshStore.showCadSolver();
    const model = (ingestId: string): CurrentModel => ({ kind: 'cad', key: ingestId, ingestId, lineage: null, label: 'CAD', viewportShowing: false });
    expect(currentModelScene(model('new'))?.ingestId).toBe('new');
    expect(currentModelScene(model('old'))?.ingestId).toBe('old');
    expect(currentModelScene(model('missing'))).toBeNull();
  });

  it('marks only the provenance that differs from the workspace mode', () => {
    const design = designForFamily('OSSE');
    const parametricContext = contextFor(design);
    const cadContext = contextFor(design, { kind: 'cad', key: 'wgi_live', ingestId: 'wgi_live' });
    expect(runContextMarker(cad('wgi_live'), parametricContext)).toBe('CAD');
    expect(runContextMarker(cad('wgi_live'), cadContext)).toBeNull();
    expect(runContextMarker(cad('wgi_other'), cadContext)).toBe('Different CAD return');
    expect(runContextMarker(parametric(design), cadContext)).toBe('Parametric');
    expect(runProvenanceMarker(cad('wgi_live'), 'cad')).toBeNull();
    expect(runProvenanceMarker(cad('wgi_live'), 'parametric')).toBe('CAD');
  });

  it('marks a parametric run the design has moved on from', () => {
    const design = designForFamily('OSSE');
    const edited = structuredClone(design);
    edited.L = (edited.L ?? 0) + 1;
    expect(runContextMarker(parametric(design), contextFor(design))).toBeNull();
    expect(runContextMarker(parametric(design), contextFor(edited))).toBe('edited since');
  });

  it('derives the live context directly from the workspace stores', () => {
    useDesignStore.setState({ designRevision: 14 });
    useCadReturnStore.setState({ ingestRecord: { ingest_id: 'wgi_store' } as never });
    useDocumentStore.setState({ identity: { designId: 'wgd_store', lineageId: 'wgl_store', baseEditVersion: 2 } });
    workspaceModeStore.setMode('cad');

    expect(currentModel()).toMatchObject({
      kind: 'cad', key: 'wgi_store', lineage: 'wgi_store', ingestId: 'wgi_store', viewportShowing: false,
    });;
  });
});

it.each(['wire', 'sidecar', 'both'] as const)('preserves executable expression identity in legacy %s snapshots', shape => {
  useDesignStore.getState().updateExpression('R', { value: null, raw: '70*(1+cos(p))' });
  const design = useDesignStore.getState().design;
  const submitted = JSON.parse(JSON.stringify(serializeSolveDesign(design)));
  expect(submitted.R).toEqual({ value: null, raw: '70*(1+cos(p))' });
  const wire = shape === 'sidecar' ? structuredClone(design)
    : shape === 'both' ? { ...submitted, _expressions: structuredClone(design._expressions) } : submitted;
  for (const snapshot of [wire, { version: 1, design: wire }]) {
    const run = { ...parametric(null), status: 'complete' as const, has_results: true, script_snapshot: snapshot };
    const model = contextFor(design);
    expect(jobDesignFingerprint(run)).toBe(model.key);
    expect(runVerdict(run, model)).toBe('current');
    expect(shownResult(model, { primary: run.id }, [run]).canRender).toBe(true);

    useDesignStore.getState().updateExpression('R', { value: null, raw: '90*(1+cos(p))' });
    const edited = useDesignStore.getState().design;
    expect(edited.R).toBe(design.R); // Same editor scalar, different executable geometry.
    expect(serializeSolveDesign(edited).R).not.toEqual(submitted.R);
    expect(runVerdict(run, contextFor(edited))).toBe('earlier-version');
    expect(shownResult(contextFor(edited), { primary: run.id }, [run]).canRender).toBe(false);
    expect(spatialResult(contextFor(edited), { primary: run.id }, [run])).toBeNull();
    const plain = structuredClone(design);
    delete plain._expressions;
    expect(designFingerprint(plain)).not.toBe(model.key);
    expect(runVerdict(parametric(plain), model)).toBe('earlier-version');
  }
});

it.each(['140', '1.4e2', ' +140.0 ', null] as const)('matches equivalent scalar and expression snapshots with raw %s', raw => {
  const plain = designForFamily('OSSE');
  plain.R = 140;
  const expression = structuredClone(plain);
  expression._expressions = { R: { value: 140, raw } };
  expect(designFingerprint(expression)).toBe(designFingerprint(plain));
  for (const design of [plain, expression]) {
    for (const wire of [serializeSolveDesign(design), { ...structuredClone(design) }]) {
      for (const snapshot of [wire, { version: 1, design: wire }]) {
        const run = { ...parametric(null), script_snapshot: snapshot };
        expect(runVerdict(run, contextFor(plain))).toBe('current');
        expect(runVerdict(run, contextFor(expression))).toBe('current');
      }
    }
  }
});

it.each(['ath', 'wg-v1'] as const)('round trips the submitted %s dialect without conflating executable dialects', dialect => {
  const design = designForFamily('OSSE');
  design._expressions = { R: { value: null, raw: '70*(1+log(p))', dialect } };
  const run = { ...parametric(null), script_snapshot: { version: 1, design: serializeSolveDesign(design) } };
  expect(runVerdict(run, contextFor(design))).toBe('current');
  const other = structuredClone(design);
  other._expressions!.R.dialect = dialect === 'ath' ? 'wg-v1' : 'ath';
  expect(designFingerprint(other)).not.toBe(designFingerprint(design));
});

it.each(['70*(1+cos(p))', '2*70', '140.0000000001', '140,0'])(
  'does not collapse nonliteral or unequal raw %s to its numeric sample', raw => {
    const plain = designForFamily('OSSE');
    plain.R = 140;
    const expression = structuredClone(plain);
    expression._expressions = { R: { value: 140, raw } };
    expect(designFingerprint(expression)).not.toBe(designFingerprint(plain));
    expect(runVerdict(parametric(expression), contextFor(expression))).toBe('current');
  });

it('matches file runs in Imported mesh even when an unrelated CAD return is retained', () => {
  const context = { ...contextFor(designForFamily('OSSE')), kind: 'file' as const, key: 'wgi_file', ingestId: 'wgi_file' };
  const file = { ...cad('wgi_file'), config_summary: { geometry_type: 'imported', import_origin: 'mesh-file' } } as JobItem;
  expect(runVerdict(file, context)).toBe('current');
  expect(runVerdict(file, context)).toBe('current');
  expect(runVerdict(cad('wgi_cad'), context)).toBe('other-model');
  expect(runVerdict(file, { ...context, key: null, ingestId: null })).toBe('other-model');
});

// Historical snapshots exist as raw designs and versioned envelopes, with
// omitted/null sweep fields or the defaults filled by the solve serializer.
it.each(['omitted', 'null', 'filled'] as const)('normalizes legacy %s sweep snapshots to the effective solve', shape => {
  const design = designForFamily('OSSE');
  design._absent = ['simulation.f1', 'simulation.f2', 'simulation.num_frequencies'];
  const wire = serializeDesign(design);
  if (shape === 'omitted') {
    for (const key of ['f1', 'f2', 'num_frequencies']) delete (wire.simulation as Record<string, unknown>)[key];
  } else if (shape === 'filled') Object.assign(wire, serializeSolveDesign(design));
  for (const snapshot of [wire, { version: 1, design: wire }]) {
    const run = { ...parametric(null), script_snapshot: snapshot };
    expect(jobDesignFingerprint(run)).toBe(designFingerprint(design));
    expect(runVerdict(run, contextFor(design))).toBe('current');
    expect(designFingerprint(hydrateDesignDocument(wire))).toBe(designFingerprint(design));
  }
});

it.each(['f1', 'f2', 'num_frequencies', 'sim_type'] as const)('keeps an effective simulation change distinct: %s', key => {
  const design = designForFamily('OSSE');
  design._absent = ['simulation.f1', 'simulation.f2', 'simulation.num_frequencies'];
  const edited = structuredClone(design);
  const changes = { f1: 123, f2: 12345, num_frequencies: 17, sim_type: 'infinite-baffle' };
  Object.assign(edited.simulation, { [key]: changes[key] });
  expect(designFingerprint(edited)).not.toBe(designFingerprint(design));
  expect(runVerdict(parametric(design), contextFor(edited))).toBe('earlier-version');
});
