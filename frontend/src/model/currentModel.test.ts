import { previewSocket } from '../api/previewSocket';
import { currentDocumentLoad } from '../stores/design';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { designFingerprint } from '../results/runCoherence';
import { resultExportSelection } from '../results/exportContext';
import { hydrateJobDesign } from '../jobs/jobDesign';
import { selectJob } from '../jobs/selectJob';
import { showJobModel } from '../jobs/showJobModel';
import { compareSelection } from '../api/results';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { resetDocumentStore } from '../stores/document';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { currentModel, currentModelStatus, modelForRun, runVerdict, shownResult } from './currentModel';
import parametric from './fixtures/parametric.json';
import fileSha from './fixtures/file-sha.json';
import fileNoSha from './fixtures/file-no-sha.json';
import cad from './fixtures/cad.json';
import cadNoSha from './fixtures/cad-no-sha.json';
import unreadable from './fixtures/unreadable.json';
import refused from '../jobs/cadRefusedJob.fixture.json';

export const oldRuns = [parametric, fileSha, fileNoSha, cad, cadNoSha, unreadable] as unknown as JobItem[];

afterEach(() => { vi.restoreAllMocks(); });

beforeEach(() => {
  resetDesignStore(); resetDocumentStore(); resetCadReturnStore();
  workspaceModeStore.setImportedInput('cad'); workspaceModeStore.setMode('parametric');
  importedMeshStore.clear(); compareSelection.clear();
});

describe('one model display table', () => {
  it.each(oldRuns)('opens the saved identity and result together: $id', job => {
    const model = modelForRun(job);
    const selected = shownResult(model, { primary: job.id }, [job]);
    expect(selected.job?.id).toBe(job.id);
    expect(selected.ids).toEqual([job.id]);
    expect(resultExportSelection(job)).toBe(job);
    expect(selected.modelNotLoaded).toBe(job.id === 'legacy-unreadable');
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
    const model = { ...modelForRun(job), key: 'new-return', ingestId: 'new-ingest' };
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
    expect(shownResult(model, { primary: job.id, overlays: others.map(job => job.id) }, oldRuns).ids).toEqual([job.id]);
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
  const job = { ...oldRuns[3], ...refused, cad_source: null, cad_intent: { ...refused.cad_intent, bundle_path: 'wgreturn/speaker.wgreturn' }, status: 'preparing', has_results: false } as JobItem;
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
