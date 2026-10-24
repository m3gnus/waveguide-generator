import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { compareSelection } from '../../api/results';
import { acknowledgeCadSolve, cadSolveClaim, rememberCadSolve } from '../../jobs/cadSolve';
import { currentModel, currentModelStatus, runVerdict } from '../../model/currentModel';
import { resetCadReturnStore, useCadReturnStore } from '../../stores/cadReturn';
import { resetDesignStore, useDesignStore } from '../../stores/design';
import { resetDocumentStore } from '../../stores/document';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../../stores/solveOptions';
import { importedMeshStore } from '../../viewport/importedMeshStore';
import { useImportedModelStore } from '../../stores/importedModel';
import { workspaceModeStore } from '../../stores/workspaceMode';
import { bindWorkspaceNavigation, noteExplicitNavigation, publishVisiblePanels, resetWorkspaceNavigationForTests } from '../workspaceNavigation';
import { resetSolveAttentionForTests } from '../solveAttention';
import { createFusionSolveDisplay } from './fusionSolveDisplay';
import { bundle, deferred, job, json, record, workspaceFor } from './selection.fixtures';

const NOW = Date.parse('2026-10-15T18:00:00Z');
function external(id = 'fusion', created = NOW - 120_000) {
  return { ...job(), id, client_request_id: `cad-solve:${id}`, created_at: new Date(created).toISOString(),
    status: 'running', cad_state: { state: 'accepted', operation_id: id },
    cad_source: { ...job().cad_source, manifest_sha256: record.manifest_sha256 },
  } as ReturnType<typeof job>;
}
const disposers: Array<() => void> = [];
afterEach(() => { disposers.splice(0).forEach((dispose) => dispose()); });
function fixture(fetcher: typeof fetch = vi.fn(async input => json(String(input).endsWith('/workspace')
  ? workspaceFor() : record))) {
  let sendAt = 0;
  let active = true;
  const display = { isActive: () => active, newestSendAt: () => sendAt, onIntent: vi.fn(),
    enterCadWorkspace: () => workspaceModeStore.setMode('cad'), reportStatus: vi.fn(), reportViewportNotice: vi.fn(), reportDiagnostic: vi.fn(),
    showIngestedMesh: vi.fn(async (r: typeof record, _name: string, _notice: unknown, _fetcher: unknown, generation: number) => {
      importedMeshStore.setCad({ source: 'cad', ingestId: r.ingest_id } as never, generation, true);
    }) };
  const controller = createFusionSolveDisplay(display, fetcher, () => NOW);
  disposers.push(controller.dispose);
  return { ...controller, display, fetcher,
    setActive: (value: boolean) => { active = value; },
    send: (at: number) => { sendAt = at; } };
}
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear(); resetDesignStore(); resetDocumentStore(); resetCadReturnStore();
  resetSolveOptionsStore(); resetWorkspaceNavigationForTests(); resetSolveAttentionForTests(); compareSelection.clear();
  importedMeshStore.clear(); workspaceModeStore.setMode('cad');
});

function preparing(preparedId: string | null = record.ingest_id) {
  const run = external();
  return { ...run, status: 'preparing', run_number: null, cad_source: null,
    cad_intent: { bundle_path: bundle.bundlePath },
    cad_state: { ...run.cad_state!, state: 'processing', stage: 'preparing-mesh',
      snapshot: { document_name: 'Speaker', manifest_sha256: record.manifest_sha256,
        artifact_sha256: record.artifact_sha256, project_lineage_id: null },
      preparation: preparedId ? { preparation_id: preparedId, ingest_id: preparedId,
        blocking_finding_ids: [], report_sha256: record.report_sha256 } : null },
  } as ReturnType<typeof job>;
}

it('selects the arriving request and fronts Results Preparing before a mesh exists', async () => {
  const activate = vi.fn(() => true); disposers.push(bindWorkspaceNavigation(activate));
  const f = fixture(); const request = preparing(null);
  await f.observe([request]);
  expect(currentModelStatus(currentModel(), [request])).toMatchObject({ kind: 'preparing', job: request });
  expect(activate).toHaveBeenCalledWith('results');
  expect(f.fetcher).not.toHaveBeenCalled();
  expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(bundle.bundlePath);
});

it.each(['processing', 'needs_user_input'])('shows the prepared model while the request is %s and has no run', async (state) => {
  const f = fixture(); const request = preparing();
  request.cad_state = { ...request.cad_state!, state };
  if (state === 'needs_user_input') request.status = 'error';
  await f.observe([request]);
  expect(currentModel().ingestId).toBe(record.ingest_id);
  expect(currentModel().viewportShowing).toBe(true);
  expect(f.display.showIngestedMesh).toHaveBeenCalledOnce();
  expect(f.fetcher).toHaveBeenCalledWith(`/api/cadlink/ingest/${record.ingest_id}`, expect.any(Object));
  expect(compareSelection.getSnapshot().awaiting).toBeNull();
  expect(cadSolveClaim(request)?.modelDisplayAcknowledged).not.toBe(true);
});

it('keeps the preparing geometry visible through acceptance and completion without another mesh load', async () => {
  const accepted = deferred<Response>();
  const f = fixture(vi.fn(async input => String(input).endsWith('/workspace') ? accepted.promise : json(record)));
  await f.observe([preparing()]);
  const run = external(); const opening = f.observe([run]);
  expect(currentModel().viewportShowing).toBe(true);
  expect(currentModel().ingestId).toBe(record.ingest_id);
  accepted.resolve(json(workspaceFor())); await opening;
  await f.observe([{ ...run, status: 'complete', has_results: true }]);
  expect(f.display.showIngestedMesh).toHaveBeenCalledOnce();
  expect(compareSelection.getSnapshot().awaiting).toBe(run.id);
  expect(cadSolveClaim(run)?.modelDisplayAcknowledged).toBe(true);
});

it('joins repeated progress notifications while one preparing model load is pending', async () => {
  const response = deferred<Response>(); const f = fixture(vi.fn(() => response.promise));
  const request = preparing();
  const first = f.observe([request]);
  const second = f.observe([{ ...request, progress: 0.1 }]);
  await vi.waitFor(() => expect(f.fetcher).toHaveBeenCalledOnce());
  response.resolve(json(record)); await Promise.all([first, second]);
  expect(f.display.showIngestedMesh).toHaveBeenCalledOnce();
});

it('does not lose acceptance that arrives during the preparing model load', async () => {
  const response = deferred<Response>();
  const f = fixture(vi.fn(async input => String(input).endsWith('/workspace') ? json(workspaceFor()) : response.promise.then(reply => reply.clone())));
  const first = f.observe([preparing()]);
  const second = f.observe([external()]);
  response.resolve(json(record)); await Promise.all([first, second]);
  expect(f.display.showIngestedMesh).toHaveBeenCalledOnce();
  expect(f.display.reportDiagnostic.mock.calls).toEqual([]);
  expect(cadSolveClaim(external())?.modelDisplayAcknowledged).toBe(true);
});

it('retries an inactive display when state permits and reports the skip only once', async () => {
  const f = fixture(); f.setActive(false);
  await f.observe([external()]); await f.observe([external()]);
  expect(f.fetcher).not.toHaveBeenCalled();
  expect(f.display.reportDiagnostic).toHaveBeenCalledExactlyOnceWith('fusion', 'inactive');
  f.setActive(true); await f.observe([external()]);
  expect(f.display.showIngestedMesh).toHaveBeenCalledOnce();
});

it('retries a failed record read without a new request or duplicate mesh load', async () => {
  let failed = true;
  const f = fixture(vi.fn(async input => failed ? json({ detail: 'internal failure' }, 503)
    : String(input).endsWith('/workspace') ? json(workspaceFor()) : json(record)));
  await f.observe([external()]);
  expect(useImportedModelStore.getState().error).not.toContain('internal failure');
  expect(useCadReturnStore.getState().ingestStaleReason).not.toContain('internal failure');
  failed = false;
  await f.observe([external()]);
  expect(f.display.showIngestedMesh).toHaveBeenCalledOnce();
  expect(f.display.reportDiagnostic).toHaveBeenCalledOnce();
  expect(f.display.reportStatus.mock.calls.flat().join(' ')).not.toContain('internal failure');
});

it('retries after an explicit return to CAD while navigation away still wins', async () => {
  const f = fixture(); const request = preparing(null);
  await f.observe([request]);
  noteExplicitNavigation(); workspaceModeStore.setMode('parametric');
  await f.observe([preparing()]); await f.observe([preparing()]);
  expect(f.fetcher).not.toHaveBeenCalled();
  expect(f.display.reportDiagnostic).toHaveBeenCalledOnce();
  noteExplicitNavigation(); workspaceModeStore.setMode('cad');
  await vi.waitFor(() => expect(f.display.showIngestedMesh).toHaveBeenCalledOnce());
});

it('does not rearm a model selection or settings edit when the user returns', async () => {
  const f = fixture(); await f.observe([preparing(null)]);
  useCadReturnStore.getState().setDriveVoltage(7);
  workspaceModeStore.setMode('parametric'); workspaceModeStore.setMode('cad');
  await f.observe([preparing()]);
  expect(f.fetcher).not.toHaveBeenCalled();
  expect(useCadReturnStore.getState().driveVoltageV).toBe(7);
});

it('loads the exact solved model when WG first sees a Fusion job already accepted', async () => {
  useCadReturnStore.getState().selectBundle({ ...bundle, documentName: 'Older fixture' });
  const old = { ...record, ingest_id: 'old', manifest_sha256: 'sha256:old' };
  useCadReturnStore.getState().applyIngest(old, useCadReturnStore.getState().beginIngestIntent());
  importedMeshStore.setCad({ source: 'cad', ingestId: old.ingest_id } as never);
  const f = fixture(); const run = external();
  expect(cadSolveClaim(run)).toBeNull();
  await f.observe([run]);
  expect(f.display.reportDiagnostic.mock.calls).toEqual([]);
  expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(record.ingest_id);
  expect(runVerdict(run, currentModel())).toBe('current');
  expect(compareSelection.getSnapshot().awaiting).toBe(run.id);
  expect(cadSolveClaim(run)?.completionAcknowledged).toBe(true);
  expect(cadSolveClaim(run)?.modelDisplayAcknowledged).toBe(true);
  await f.observe([run]);
  expect(f.display.showIngestedMesh).toHaveBeenCalledOnce();
});

it('does not reopen a displayed Fusion run when the display owner remounts', async () => {
  const first = fixture(); const run = external();
  await first.observe([run]); first.dispose();
  const remounted = fixture();
  await remounted.observe([run]);
  expect(remounted.fetcher).not.toHaveBeenCalled();
});

it('recovers a recent completed Fusion solve but does not replay old finished history', async () => {
  const f = fixture();
  await f.observe([{ ...external('old', NOW - 600_001), status: 'complete', has_results: true }]);
  expect(f.fetcher).not.toHaveBeenCalled();
  await f.observe([{ ...external('recent'), status: 'complete', has_results: true }]);
  expect(f.display.showIngestedMesh).toHaveBeenCalledOnce();
});

it('shows the Fusion model even when the jobs coordinator acknowledged its result first', async () => {
  const f = fixture(); const run = external();
  await f.observe([{ ...run, status: 'preparing', cad_state: { ...run.cad_state!, state: 'processing' } }]);
  // JobsCoordinator claims the processing row and acknowledges acceptance in
  // its React effect. That selects a result, but has not displayed its model.
  rememberCadSolve(run.id, '');
  acknowledgeCadSolve(run, 'completion');
  await f.observe([run]);
  expect(f.display.showIngestedMesh).toHaveBeenCalledOnce();
  expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe(record.ingest_id);
});

it('leaves a WG-owned solve on its existing completion path', async () => {
  const f = fixture(); const run = external(); rememberCadSolve(run.id, 'User design', 'source-ingest');
  await f.observe([run]); expect(f.fetcher).not.toHaveBeenCalled();
});

it('preserves navigation made while Fusion preparation was pending', async () => {
  const f = fixture(); const run = external();
  await f.observe([{ ...run, status: 'preparing', cad_state: { ...run.cad_state!, state: 'processing' } }]);
  noteExplicitNavigation();
  await f.observe([run]); expect(f.fetcher).not.toHaveBeenCalled();
});

it.each(['navigation', 'selection', 'edit', 'voltage', 'sweep', 'options', 'design', 'newer-send', 'dispose', 'removed'] as const)(
  'does not paint an obsolete model after %s while its ingestion is loading', async (change) => {
    const response = deferred<Response>(); const f = fixture(vi.fn(() => response.promise));
    const run = external(); const pending = f.observe([run]);
    if (change === 'navigation') noteExplicitNavigation();
    if (change === 'selection') useCadReturnStore.getState().selectBundle(bundle);
    if (change === 'edit') useCadReturnStore.getState().setAreaDriftOverride('source-hf', true);
    if (change === 'voltage') useCadReturnStore.getState().setDriveVoltage(7);
    if (change === 'sweep') useCadReturnStore.getState().setSweep({ frequencyCount: 19 });
    if (change === 'options') useSolveOptionsStore.getState().setVerbose(true);
    if (change === 'design') useDesignStore.getState().updateValue('length', 234);
    if (change === 'newer-send') f.send(NOW);
    if (change === 'dispose') f.dispose();
    if (change === 'removed') await f.observe([]);
    response.resolve(json(record)); await pending;
    expect(f.display.showIngestedMesh).not.toHaveBeenCalled();
    expect(compareSelection.getSnapshot().awaiting).toBeNull();
    if (change === 'voltage') expect(useCadReturnStore.getState().driveVoltageV).toBe(7);
    if (change === 'sweep') expect(useCadReturnStore.getState().frequencyCount).toBe(19);
    if (change === 'options') expect(useSolveOptionsStore.getState().verbose).toBe(true);
    if (change === 'design') expect(useDesignStore.getState().design.length).toBe(234);
  });

it('keeps parametric work and undo history while following an external solve', async () => {
  useDesignStore.getState().updateValue('length', 180);
  const design = useDesignStore.getState().design;
  const history = useDesignStore.temporal.getState().pastStates;
  await fixture().observe([external()]);
  expect(useDesignStore.getState().design).toBe(design);
  expect(useDesignStore.temporal.getState().pastStates).toBe(history);
});

it.each(['parametric', 'WG-owned CAD'] as const)('a newer %s solve cancels an in-flight Fusion model load', async (kind) => {
  const response = deferred<Response>(); const f = fixture(vi.fn(() => response.promise));
  const old = external(); const pending = f.observe([old]);
  const newer = external('newer', NOW);
  if (kind === 'parametric') { newer.cad_state = null; newer.config_summary.geometry_type = 'parametric'; }
  else rememberCadSolve(newer.id, 'WG design', 'newer-ingest');
  await f.observe([newer, old]);
  response.resolve(json(record)); await pending;
  expect(f.display.showIngestedMesh).not.toHaveBeenCalled();
  expect(compareSelection.getSnapshot().awaiting).toBeNull();
});

it('reports an unavailable prepared record without solving again or inventing a model', async () => {
  const f = fixture(vi.fn(async () => json({ detail: 'missing' }, 404)));
  await f.observe([external()]);
  expect(f.display.showIngestedMesh).not.toHaveBeenCalled();
  expect(f.display.reportStatus).toHaveBeenLastCalledWith(expect.stringContaining('will retry'));
  expect(f.display.reportDiagnostic).toHaveBeenCalledWith('fusion', 'open-failed', expect.any(Error));
  expect(cadSolveClaim(external())?.modelDisplayAcknowledged).not.toBe(true);
});

it.each([true, false])('leaves a WG continuation alone when its parent row is present: %s', async (present) => {
  const f = fixture(); const parent = external('parent', NOW - 300_000);
  rememberCadSolve(parent.id, 'WG design', 'source-ingest');
  const child = { ...external('child'), parent_job_id: parent.id };
  await f.observe(present ? [child, parent] : [child]);
  expect(f.fetcher).not.toHaveBeenCalled();
  expect(cadSolveClaim(child)).toBeNull();
});

it('follows an approved external continuation whose parent has only an empty Fusion claim', async () => {
  const f = fixture(); const parent = external('parent', NOW - 300_000);
  rememberCadSolve(parent.id, '');
  useCadReturnStore.getState().selectBundle({ ...bundle, documentName: 'Older fixture' });
  const child = { ...external('child'), parent_job_id: parent.id };
  await f.observe([child, parent]);
  expect(f.display.showIngestedMesh).toHaveBeenCalledOnce();
  expect(runVerdict(child, currentModel())).toBe('current');
  expect(compareSelection.getSnapshot().awaiting).toBe(child.id);
});

it('shows an old held request after restart while completed history remains gated', async () => {
  const request = preparing();
  request.status = 'error'; request.created_at = new Date(NOW - 3_600_000).toISOString();
  request.cad_state = { ...request.cad_state!, state: 'needs_user_input' };
  const f = fixture(); await f.observe([request]);
  expect(f.display.showIngestedMesh).toHaveBeenCalledOnce();
});

it('cancels a pending model read on navigation and retries only after the user returns to Results', async () => {
  publishVisiblePanels(['results']);
  const response = deferred<Response>();
  const f = fixture(vi.fn(async input => String(input).endsWith('/workspace') ? json(workspaceFor()) : response.promise.then(reply => reply.clone())));
  const pending = f.observe([external()]);
  await vi.waitFor(() => expect(f.fetcher).toHaveBeenCalledTimes(2));
  noteExplicitNavigation(); publishVisiblePanels(['geometry']);
  response.resolve(json(record)); await pending;
  expect(f.display.showIngestedMesh).not.toHaveBeenCalled();
  await f.observe([external()]);
  expect(f.display.showIngestedMesh).not.toHaveBeenCalled();
  noteExplicitNavigation(); publishVisiblePanels(['results']);
  await vi.waitFor(() => expect(f.display.showIngestedMesh).toHaveBeenCalledOnce());
});

it('fences navigation during artifact fetching itself', async () => {
  const artifact = deferred<void>(); const f = fixture();
  f.display.showIngestedMesh.mockImplementation(async (r, _name, _notice, _fetcher, generation) => {
    await artifact.promise;
    importedMeshStore.setCad({ source: 'cad', ingestId: r.ingest_id } as never, generation, true);
  });
  const pending = f.observe([external()]);
  await vi.waitFor(() => expect(f.display.showIngestedMesh).toHaveBeenCalledOnce());
  noteExplicitNavigation(); artifact.resolve(); await pending;
  expect(currentModel().viewportShowing).toBe(false);
  expect(cadSolveClaim(external())?.modelDisplayAcknowledged).not.toBe(true);
});
