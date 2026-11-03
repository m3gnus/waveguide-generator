import { previewSocket } from '../api/previewSocket';
import { currentDocumentLoad } from '../stores/design';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { ResultsPanel } from '../shell/ResultsPanel';
import { preferencesStore } from '../prefs/preferences';
import { designFingerprint } from '../results/runCoherence';
import { resultExportSelection } from '../results/exportContext';
import { hydrateJobDesign } from '../jobs/jobDesign';
import { selectJob } from '../jobs/selectJob';
import { showJobModel } from '../jobs/showJobModel';
import { compareSelection, resultsCache } from '../api/results';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { resetDocumentStore } from '../stores/document';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { currentModel, currentModelScene, currentModelStatus, modelForRun, runVerdict, shownResult, spatialResult } from './currentModel';
import parametric from './fixtures/parametric.json';
import fileSha from './fixtures/file-sha.json';
import fileNoSha from './fixtures/file-no-sha.json';
import cad from './fixtures/cad.json';
import cadNoSha from './fixtures/cad-no-sha.json';
import unreadable from './fixtures/unreadable.json';
import refused from '../jobs/cadRefusedJob.fixture.json';
import { adoptDesignBuild, resetImportedModelStore, useDesignModelStore, useImportedModelStore } from '../stores/importedModel';
import { json, mesh, record, workspaceFor } from '../shell/cadlink/selection.fixtures';
import { fieldPlaneJob, femFieldJob } from '../viewport/Viewport';
import { addWoofer, emptyAssembly } from '../stores/assembly';
import { designForFamily, serializeDesign } from '../stores/design';
import type { CurrentModel } from './currentModel';
import { assemblyContentKey, resetAssemblyBuild } from '../stores/assemblyBuild';
import { assemblyMeshScene, solverMeshScene } from '../viewport/solverMeshScene';
import type { CompiledAssembly } from '../api/assembly';

export const oldRuns = [parametric, fileSha, fileNoSha, cad, cadNoSha, unreadable] as unknown as JobItem[];

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

beforeEach(() => {
  resetDesignStore(); resetDocumentStore(); resetCadReturnStore();
  resultsCache.clear(); preferencesStore.resetForTests(); preferencesStore.update({ chartTypes: ['summary'] });
  workspaceModeStore.setImportedInput('cad'); resetImportedModelStore(); workspaceModeStore.setMode('parametric');
  importedMeshStore.clear(); compareSelection.clear();
});

describe('one model display table', () => {
  it.each(oldRuns)('opens saved charts and the corresponding viewport through history: $id', async saved => {
    const job = { ...saved, field_plane_available: true, has_fem_field_artifact: true };
    const imported = job.config_summary.geometry_type === 'imported';
    const file = job.config_summary.import_origin === 'mesh-file';
    const ingestId = job.cad_source?.ingest_id!;
    const retained = { ...record, ingest_id: ingestId, model_id: job.cad_source?.model_id,
      ...(job.cad_source?.solve_model_sha256 ? { solve_model_sha256: job.cad_source.solve_model_sha256 } : {}) };
    const workspace = workspaceFor(ingestId);
    const response = { ...workspace, job_id: job.id,
      model: { ...workspace.model, model_id: retained.model_id, producer: file ? 'mesh-file' : 'cad' },
      prepared: { ...workspace.prepared, ingest_id: ingestId, model_id: retained.model_id,
        producer: file ? 'mesh-file' : 'cad', ...(file ? {
          msh_text: mesh, source_file: { filename: 'saved.msh', transform: { units: 'm', forward: '+z', up: '+y' }, source_tags: [1] },
          source_tags: { 'source-hf': 1 }, summary: { node_count: 3, triangle_count: 1, dimensions: [1, 1, 0], groups: [] },
        } : {}) } };
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === `/api/results/${job.id}`) return json({ result_kind: 'parametric', result_contract_version: 1, frequencies: [1000], metadata: {} });
      if (path === `/api/runs/${job.id}/workspace`) return json(response);
      if (path.endsWith('/viewport-mesh')) return new Response(mesh);
      if (path === `/api/cadlink/ingest/${ingestId}`) return json(retained);
      if (path.includes('/setup')) return json({ setup: null });
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal('fetch', fetcher);
    const before = currentModel();
    const errors: string[] = [];
    selectJob(job, message => errors.push(message));
    if (imported) await vi.waitFor(() => expect(useImportedModelStore.getState().phase).toBe('ready'));
    expect(errors).toEqual([]);
    const model = currentModel();
    const selected = shownResult(model, compareSelection.getSnapshot(), [job]);
    expect(selected).toMatchObject({ job, ids: [job.id], canRender: true,
      modelNotLoaded: job.id === 'legacy-unreadable' });
    if (job.id === 'legacy-unreadable') {
      expect(model.key).toBe(before.key);
      expect(runVerdict(job, model)).toBe('model-not-shown');
      expect(spatialResult(model, compareSelection.getSnapshot(), [job])).toBeNull();
      expect(fieldPlaneJob([job], job.id)).toBeNull();
      expect(femFieldJob([job], job.id)).toBeNull();
    } else {
      expect(runVerdict(job, model)).toBe('current');
      expect(model.viewportShowing).toBe(true);
      expect(fieldPlaneJob([job], job.id)).toBe(job);
      expect(femFieldJob([job], job.id)).toBe(job);
      if (imported) {
        expect(fetcher).toHaveBeenCalledWith(`/api/runs/${job.id}/workspace`, undefined);
        expect(currentModelScene(model)?.ingestId).toBe(ingestId);
      } else expect(model.key).toBe(designFingerprint(hydrateJobDesign(job)!));
    }
    expect(resultExportSelection(job)).toBe(job);
    // Assert the real chart consumer too, after the real history/model load.
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.spyOn(jobsSocket, 'getSnapshot').mockReturnValue({ connection: 'connected', epoch: 1, cursor: 1, jobs: [job], error: null });
    const host = document.createElement('div'); document.body.append(host);
    const root = createRoot(host);
    try {
      await act(async () => { root.render(createElement(ResultsPanel)); });
      expect(host.querySelector('[data-result-primary]')?.getAttribute('data-result-primary')).toBe(job.id);
      expect(host.querySelector('.result-grid')).not.toBeNull();
      if (job.id === 'legacy-unreadable') expect(host.textContent).toContain('Run model not loaded');
    } finally { act(() => root.unmount()); host.remove(); resultsCache.clear(); }
  });

  it('selects a readable parametric run and its viewport in one call', () => {
    const job = oldRuns[0];
    useDesignStore.getState().updateField('L', 999);
    selectJob(job);
    expect(currentModel().key).toBe(designFingerprint(hydrateJobDesign(job)!));
    expect(shownResult(currentModel(), compareSelection.getSnapshot(), [job]).job).toBe(job);
  });

  it('keeps unreadable saved results selectable under the model-not-loaded line', () => {
    const job = oldRuns.at(-1)!;
    selectJob(job);
    expect(runVerdict(job, currentModel())).toBe('model-not-shown');
    expect(shownResult(currentModel(), compareSelection.getSnapshot(), [job])).toMatchObject({ job, modelNotLoaded: true });
    useDesignStore.getState().updateField('L', 999);
    expect(shownResult(currentModel(), compareSelection.getSnapshot(), [job]).job).toBeNull();
  });

  it('hides an earlier parametric version after an edit, and restores it after Undo', async () => {
    const job = oldRuns[0];
    await showJobModel(job);
    const selection = { primary: job.id };
    useDesignStore.getState().updateField('L', 999);
    expect(runVerdict(job, currentModel())).toBe('earlier-version');
    expect(shownResult(currentModel(), selection, [job]).ids).toEqual([]);
    expect(currentModelStatus(currentModel(), [job]).kind).toBe('never-solved');
    expect(() => resultExportSelection(job, currentModel())).toThrow('not shown');
    useDesignStore.getState().undo();
    expect(shownResult(currentModel(), selection, [job]).job).toBe(job);
  });

  it('hides a new Fusion version of the same registered model', () => {
    const job = oldRuns[3];
    const model = { ...modelForRun(job), key: 'new-return', ingestId: 'new-ingest',
      prepared: { ...modelForRun(job).prepared!, key: 'new-return', ingestId: 'new-ingest' } };
    expect(runVerdict(job, model)).toBe('earlier-version');
    expect(shownResult(model, { primary: job.id }, [job]).ids).toEqual([]);
  });

  it.each(['parametric', 'file', 'cad'] as const)('never leaks another kind into %s', kind => {
    const job = oldRuns.find(job => modelForRun(job).kind === kind)!;
    const others = oldRuns.filter(job => modelForRun(job).kind !== kind);
    const model = modelForRun(job);
    for (const other of others) {
      expect(shownResult(model, { primary: other.id }, [other]).job).toBeNull();
    }
  });

  it.each(['parametric', 'file', 'cad'] as const)('compares any solved run against a %s primary', kind => {
    const job = oldRuns.find(job => modelForRun(job).kind === kind)!;
    const others = oldRuns.filter(job => modelForRun(job).kind !== kind && job.has_results);
    const model = modelForRun(job);
    const shown = shownResult(model, { primary: job.id, overlays: others.map(job => job.id) }, oldRuns);
    expect(shown.job).toBe(job);
    expect(shown.ids).toEqual([job.id, ...others.map(job => job.id)]);
  });

  it('keeps an overlay out until the run has something to draw', () => {
    const [job, other] = oldRuns;
    const model = modelForRun(job);
    const unsolved = { ...other, status: 'complete' as const, has_results: false };
    expect(shownResult(model, { primary: job.id, overlays: [unsolved.id] }, [job, unsolved]).ids).toEqual([job.id]);
  });

  it('does not let an overlay stand in for a primary the model no longer matches', () => {
    const [job, other] = oldRuns;
    const model = { ...modelForRun(job), key: 'new-return',
      authoring: { ...modelForRun(job).authoring!, key: 'new-return' } };
    expect(shownResult(model, { primary: job.id, overlays: [other.id] }, [job, other]).ids).toEqual([]);
  });

  it('switches to the newest matching result after leaving a selected model', () => {
    const [a, b] = [oldRuns[0], oldRuns[3]];
    expect(shownResult(modelForRun(b), { primary: a.id }, [b, a]).job).toBe(b);
  });

  it('never renders charts while an imported model is still loading', () => {
    const job = oldRuns[3];
    const model = { ...modelForRun(job), viewportShowing: false };
    expect(runVerdict(job, model)).toBe('model-not-shown');
    expect(shownResult(model, { primary: job.id }, [job]).ids).toEqual([]);
  });

  it('associates a legacy refused CAD intent with the source model and retains the server message', () => {
    const job = { ...oldRuns[3], ...refused, cad_source: null, status: 'preparing', has_results: false } as JobItem;
    const ingestId = refused.cad_intent.bundle_path.split('/').at(-1)!;
    useCadReturnStore.setState({ ingestRecord: { ingest_id: ingestId } as never });
    workspaceModeStore.setMode('cad');
    importedMeshStore.setCad({ source: 'cad', ingestId } as never);
    expect(currentModelStatus(currentModel(), [job])).toMatchObject({ kind: 'refused', message: refused.cad_state.message });
  });

  it.each([
    ['preparing', 'preparing'], ['queued', 'running'], ['running', 'running'], ['error', 'failed'], ['complete', 'solved'],
  ] as const)('uses existing job state: %s → %s', (status, kind) => {
    const job = { ...oldRuns[0], status, error_message: 'Server failure' };
    expect(currentModelStatus(modelForRun(job), [job])).toMatchObject({ kind, job });
  });
});

it('associates a Fusion-file refusal with the source manifest rather than its bundle filename', () => {
  const job = { ...oldRuns[3], ...refused, cad_source: null, cad_return: { ...refused.cad_return, bundle_path: 'wgreturn/speaker.wgreturn' }, status: 'preparing', has_results: false } as JobItem;
  const model = { ...modelForRun(oldRuns[3]), manifestSha256: refused.cad_state.snapshot.manifest_sha256 };
  expect(modelForRun(job).ingestId).toBe(refused.cad_state.preparation.ingest_id);
  expect(currentModelStatus(model, [job])).toMatchObject({ kind: 'refused', message: refused.cad_state.message });
});


it.each(['loading', 'failed', 'old-revision', 'ready'] as const)('holds parametric charts behind the actual preview scene: %s', state => {
  const job = oldRuns[0];
  selectJob(job);
  const revision = useDesignStore.getState().designRevision;
  const snapshot = { ...previewSocket.getSnapshot(), connection: 'connected' as const,
    awaitingDocumentFrame: state === 'loading', displayedRevision: state === 'old-revision' ? revision - 1 : revision,
    frame: { documentLoad: state === 'loading' || state === 'failed' ? currentDocumentLoad() - 1 : currentDocumentLoad(), header: { designRevision: revision } } as never,
    error: state === 'failed' ? 'Preview failed' : null };
  vi.spyOn(previewSocket, 'getSnapshot').mockReturnValue(snapshot);
  expect(currentModel().viewportShowing).toBe(state === 'ready');
  expect(shownResult(currentModel(), { primary: job.id }, [job]).job?.id ?? null).toBe(state === 'ready' ? job.id : null);
});

it('does not use a retained parametric solver mesh belonging to another model', () => {
  const job = oldRuns[0]; selectJob(job);
  importedMeshStore.setSolver({ source: 'solver', modelKey: 'another-model' } as never);
  expect(currentModel().viewportShowing).toBe(false);
  expect(shownResult(currentModel(), { primary: job.id }, [job]).ids).toEqual([]);
  importedMeshStore.setSolver({ source: 'solver', modelKey: modelForRun(job).key! } as never);
  expect(shownResult(currentModel(), { primary: job.id }, [job]).job).toBe(job);
});


it('reads held and historical error-state CAD requests as waiting', () => {
  const job = { ...oldRuns[3], ...refused, cad_source: null, status: 'held', has_results: false } as JobItem;
  const model = { ...modelForRun(job), kind: 'cad' as const, manifestSha256: job.cad_state!.snapshot!.manifest_sha256 };
  expect(currentModelStatus(model, [job])).toMatchObject({ kind: 'waiting', message: refused.cad_state.message });
  expect(currentModelStatus(model, [{ ...job, status: 'error' }])).toMatchObject({ kind: 'waiting' });
  const error = { ...job, status: 'error', cad_state: { ...job.cad_state!, held: false, reason: null }, error_message: 'Preparation failed' } as JobItem;
  expect(currentModelStatus(model, [error]).kind).not.toBe('waiting');
});


it('uses the server return identity for an unprepared ingest request without a client intent', () => {
  const job = { ...oldRuns[3], cad_source: null, cad_intent: null, cad_state: null,
    cad_return: { bundle_path: 'ingest/wgi_retained', return_id: 'wgr_retained' } } as JobItem;
  expect(modelForRun(job).ingestId).toBe('wgi_retained');
});

it('says a selected run is arriving while the viewport has yet to draw its model', () => {
  const job = { id: 'next', status: 'complete', has_results: true, config_summary: {}, script_snapshot: { version: 1, design: { formula: 'OSSE' } } } as unknown as JobItem;
  const model = { ...currentModel(), kind: 'parametric' as const, key: modelForRun(job).key, authoring: modelForRun(job).authoring, unloadedRunId: undefined };
  const shown = (viewportShowing: boolean, other: Partial<JobItem> = {}) =>
    shownResult({ ...model, viewportShowing }, { primary: 'next' }, [{ ...job, ...other }]);
  expect(shown(false)).toMatchObject({ arriving: true, job: null, canRender: false });
  expect(shown(true)).toMatchObject({ arriving: false, job: { id: 'next' }, canRender: true });
  expect(shown(false, { has_results: false, status: 'error' }).arriving).toBe(false);
  expect(shownResult({ ...model, key: 'another model', authoring: { ...model.authoring!, key: 'another model' }, viewportShowing: true }, { primary: 'next' }, [job]).arriving).toBe(false);
});

it.each(['file', 'cad'] as const)('keeps legacy ingest fallback and prepared lineage for %s runs', kind => {
  const modern = oldRuns.find(job => modelForRun(job).kind === kind && !!job.cad_source?.solve_model_sha256)!;
  const legacy = { ...modern, cad_source: { ...modern.cad_source!, solve_model_sha256: null } };
  const retained = modelForRun(modern);
  expect(runVerdict(legacy, retained)).toBe('current');
  expect(runVerdict(modern, modelForRun(legacy))).toBe('current');
  const changed = { ...retained, prepared: { ...retained.prepared!, key: 'new-sha', ingestId: 'new-ingest' } };
  expect(runVerdict(modern, changed)).toBe('earlier-version');
  expect(runVerdict(modern, { ...changed, prepared: { ...changed.prepared, lineage: 'other-model' } })).toBe('other-model');
});

it.each(oldRuns.filter(job => ['cad', 'file'].includes(modelForRun(job).kind)))(
  'never treats the anchor snapshot of $id as solved parametric geometry', saved => {
    const model = { ...currentModel(), viewportShowing: true };
    const job = { ...saved, design_uid: model.designUid,
      script_snapshot: { version: 1, design: serializeDesign(model.design!) } } as JobItem;
    // The imported solve's geometry can differ even though its document UID
    // and captured anchor still match the untouched authoring document.
    expect(modelForRun(job).authoring).toMatchObject({ key: model.authoring!.key, designUid: model.designUid });
    expect(runVerdict(job, model)).toBe('other-model');
    expect(currentModelStatus(model, [job]).kind).toBe('never-solved');
    for (const primary of [job.id, null]) {
      expect(shownResult(model, { primary }, [job])).toMatchObject({ job: null, latest: null, ids: [], canRender: false });
      expect(spatialResult(model, { primary }, [job])).toBeNull();
    }
    expect(runVerdict(job, modelForRun(job))).toBe('current');
  });

describe('a design assembly run on its parametric design', () => {
  let counter = 0;
  const ids = () => `00000000-0000-4000-8000-${String(counter += 1).padStart(12, '0')}`;
  const onScreen = () => {
    const { assembly } = addWoofer(emptyAssembly(ids), { anchor: 'below' }, ids);
    useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly });
    return { ...currentModel(), viewportShowing: true };
  };
  const assemblyRun = (model: CurrentModel, overrides: Partial<JobItem> = {}) => ({
    ...oldRuns[0], id: 'assembly-run', status: 'complete', has_results: true, field_plane_available: true,
    script_snapshot: { version: 1, design: serializeDesign(model.design!) },
    config_summary: { geometry_type: 'imported', formula_type: 'design-assembly', import_origin: 'design-assembly',
      ingest_id: 'wgi_1', design_assembly: { design: { design_uid: model.designUid } } },
    cad_source: { ingest_id: 'wgi_1', model_id: 'design-assembly:x', solve_model_sha256: 'sha256:assembly' },
    ...overrides,
  }) as unknown as JobItem;

  it('is this design solved, in the status and the charts', () => {
    const model = onScreen();
    const job = assemblyRun(model);
    expect(runVerdict(job, model)).toBe('current');
    expect(currentModelStatus(model, [job])).toMatchObject({ kind: 'solved', job });
    expect(shownResult(model, { primary: job.id }, [job])).toMatchObject({ job, canRender: true });
  });

  const build = (sha = 'sha256:assembly') => {
    const model = currentModel();
    const record = { ingest_id: 'wgi_1', solve_model_sha256: sha, producer: 'design-assembly', sources: [], source_tags: {}, msh_text: mesh } as unknown as CompiledAssembly;
    adoptDesignBuild({ record, model: { model_id: 'design-assembly:x', display_name: 'x', producer: 'design-assembly' } },
      { designUid: model.designUid ?? null, revision: useDesignStore.getState().designRevision, load: currentDocumentLoad(), contentKey: assemblyContentKey(model.design!) });
    importedMeshStore.setSolver(assemblyMeshScene('x', record, 'solver'), importedMeshStore.captureGeneration(), false);
    return currentModel();
  };

  it('keeps fields off the authoring preview before a build', () => {
    const model = onScreen();
    const job = assemblyRun(model);
    expect(spatialResult(model, { primary: job.id }, [job])).toBeNull();
    expect(fieldPlaneJob([job], job.id, model)).toBeNull();
  });

  it('matches the prepared build on both the live and retained model, regardless of setup fingerprint', () => {
    const job = assemblyRun(onScreen());
    const built = build();
    expect(modelForRun(job)).toMatchObject({ prepared: { key: 'sha256:assembly', lineage: 'design-assembly:x' },
      authoring: { key: built.key, designUid: built.designUid } });
    for (const model of [built, modelForRun(job)]) {
      expect(runVerdict(job, model)).toBe('current');
      expect(spatialResult(model, { primary: job.id }, [job])).toBe(job);
      expect(fieldPlaneJob([job], job.id, model)).toBe(job);
    }
    useDesignStore.getState().updateValue('simulation.f1', 200);
    const edited = { ...currentModel(), viewportShowing: true };
    expect(runVerdict(job, edited)).toBe('earlier-version');
    expect(shownResult(edited, { primary: job.id }, [job]).job).toBeNull();
    expect(spatialResult(edited, { primary: job.id }, [job])).toBeNull();
    // Reuse has the same SHA, but a fresh deep-copied record and changed authoring content.
    const rebuilt = build();
    expect(rebuilt.key).not.toBe(built.key);
    expect(runVerdict(job, rebuilt)).toBe('current');
    expect(fieldPlaneJob([job], job.id, rebuilt)).toBe(job);
  });

  it('refuses fields for a different SHA, a missing compiled scene or a stale session', () => {
    const job = assemblyRun(onScreen());
    const different = build('sha256:different');
    expect(runVerdict(job, different)).toBe('earlier-version');
    expect(fieldPlaneJob([job], job.id, different)).toBeNull();
    build();
    importedMeshStore.clear();
    expect(fieldPlaneJob([job], job.id, { ...currentModel(), viewportShowing: true })).toBeNull();
    build();
    useDesignStore.getState().updateValue('enclosure.depth', 321);
    expect(fieldPlaneJob([job], job.id, { ...currentModel(), viewportShowing: true })).toBeNull();
  });

  it.each(['different-uid', 'missing-uid', 'missing-snapshot', 'unreadable-snapshot'] as const)(
    'rejects %s on the live model even when the prepared SHA matches', reason => {
      const before = onScreen();
      const original = assemblyRun(before);
      const job = reason === 'missing-snapshot' ? { ...original, script_snapshot: null }
        : reason === 'unreadable-snapshot' ? { ...original, script_snapshot: { version: 9 } } as unknown as JobItem
        : { ...original, config_summary: { ...original.config_summary,
          design_assembly: { design: { design_uid: reason === 'different-uid' ? 'wgu_other' : null } } } };
      for (const model of [before, build()]) {
        expect(runVerdict(job, model)).toBe('other-model');
        expect(fieldPlaneJob([job], job.id, model)).toBeNull();
      }
      expect(runVerdict(job, modelForRun(job))).toBe('current');
    });

  it('is an earlier version once the design is edited, and another model for another design', () => {
    const before = onScreen();
    const job = assemblyRun(before);
    useDesignStore.getState().updateValue('simulation.f1', 200);
    const edited = { ...currentModel(), viewportShowing: true };
    expect(runVerdict(job, edited)).toBe('earlier-version');
    expect(currentModelStatus(edited, [job]).kind).toBe('never-solved');
    expect(runVerdict(assemblyRun(before, { config_summary: { ...job.config_summary,
      design_assembly: { design: { design_uid: 'wgu_someone_else' } } } }), edited)).toBe('other-model');
  });

  it('draws the compiled assembly in Geometry only while it is current, and in Mesh until the next build', () => {
    resetAssemblyBuild();
    const model = onScreen();
    const horn = solverMeshScene('Horn', { msh_text: mesh, cut_planes: [], stats: { triangle_count: 1, mesh_cache_key: 'horn' } } as never);
    importedMeshStore.setSolver({ ...horn, modelKey: model.key! });
    importedMeshStore.showSolver();
    // Never the horn-only solver mesh: Solve would solve the compile.
    expect(currentModelScene(currentModel())).toBeNull();
    importedMeshStore.showParametric();
    expect(currentModelScene(currentModel())).toBeNull();
    const record = { ingest_id: 'wgi_1', solve_model_sha256: 'sha256:assembly', producer: 'design-assembly', sources: [], source_tags: {}, msh_text: mesh } as unknown as CompiledAssembly;
    adoptDesignBuild({ record, model: { model_id: 'design-assembly:x', display_name: 'x', producer: 'design-assembly' } },
      { designUid: model.designUid ?? null, revision: 1, load: currentDocumentLoad(), contentKey: assemblyContentKey(model.design!) });
    importedMeshStore.setSolver({ ...assemblyMeshScene('x', record, 'solver'), modelKey: 'prepared:wgi_1' }, importedMeshStore.captureGeneration(), false);
    expect(currentModel()).toMatchObject({ kind: 'parametric', key: model.key, modelId: 'design-assembly:x', ingestId: 'wgi_1',
      prepared: { modelId: 'design-assembly:x', ingestId: 'wgi_1', key: 'sha256:assembly', current: true } });
    const built = currentModelScene(currentModel());
    expect(built).toMatchObject({ source: 'solver', ingestId: 'wgi_1', triangleCount: 1 });
    expect(currentModel().viewportShowing).toBe(true);
    importedMeshStore.showSolver();
    expect(currentModelScene(currentModel())).toBe(built);
    useDesignStore.getState().updateValue('enclosure.depth', 321);
    // Mesh keeps the last build up, as the earlier revision it is; Geometry follows the design.
    expect(currentModelScene(currentModel())).toBe(built);
    expect(currentModel().viewportShowing).toBe(false);
    expect(currentModel().prepared).toBeUndefined();
    importedMeshStore.showParametric();
    expect(currentModelScene(currentModel())).toBeNull();
    useDesignStore.getState().undo();
    expect(currentModelScene(currentModel())).toBe(built);
    expect(currentModel().viewportShowing).toBe(true);
    expect(currentModel().prepared?.current).toBe(true);
    useDesignStore.getState().replaceDocument(designForFamily('R-OSSE'));
    expect(useDesignModelStore.getState().prepared).toBeNull();
    expect(currentModelScene(currentModel())).toBeNull();
    resetDesignStore();
    expect(useDesignModelStore.getState().prepared).toBeNull();
    expect(currentModelScene(currentModel())).toBeNull();
  });

  it('stays another model when the run stored no design', () => {
    const model = onScreen();
    expect(runVerdict(assemblyRun(model, { script_snapshot: null } as Partial<JobItem>), model)).toBe('other-model');
  });
});
