import { rememberLastModel } from '../../stores/lastModel';
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
import { createCadReturnArrivalReader } from './arrivals';
import { bundle, deferred, job, json, record, workspaceFor } from './selection.fixtures';

const NOW = Date.parse('2026-10-15T18:00:00Z');
function external(id = 'fusion', created = NOW - 120_000) {
  return { ...job(), id, client_request_id: `cad-solve:${id}`, created_at: new Date(created).toISOString(),
    cad_return: { bundle_path: bundle.bundlePath, return_id: bundle.returnId },
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

it('shows a second Fusion run of the same model after the first run was displayed', async () => {
  const secondRecord = { ...record, ingest_id: 'wgi_second' };
  const f = fixture(vi.fn(async input => {
    const path = String(input);
    if (path.endsWith('/workspace')) return json(workspaceFor(path.includes('/second/') ? secondRecord.ingest_id : record.ingest_id));
    return json(path.endsWith(secondRecord.ingest_id) ? secondRecord : record);
  }));
  const first = { ...external(), status: 'complete' as const, has_results: true, run_number: 1 };
  await f.observe([first]);
  expect(currentModel().ingestId).toBe(record.ingest_id);
  const pending = { ...preparing(null), id: 'second', client_request_id: 'cad-solve:second',
    created_at: new Date(NOW).toISOString() };
  await f.observe([pending, first]);
  const second = { ...external('second', NOW), run_number: 2,
    cad_intent: null, cad_source: { ...external().cad_source!, ingest_id: secondRecord.ingest_id } };
  await f.observe([second, first]);
  // Binding replaces the server's CAD intent with a SolveRequest. A folder
  // listing can discover that same export after its run was already opened.
  const prepare = vi.fn();
  const reader = createCadReturnArrivalReader({ initialChoiceOwed: { current: false },
    seenReturnRevisions: { current: new Map() },
    ownsReturn: item => f.ownsReturn(item, [second, first]),
    ownsSolveReturn: item => f.ownsReturn(item, [second, first]),
    autoIngestSelected: prepare,
  }, (async () => json({ cadFolderConfigured: true, items: [bundle] })) as typeof fetch);
  await reader.refresh({ background: true, autoOpenNew: true });
  await f.observe([{ ...second, status: 'complete', has_results: true }, first]);
  expect(currentModel().ingestId).toBe(secondRecord.ingest_id);
  expect(runVerdict(second, currentModel())).toBe('current');
  expect(compareSelection.getSnapshot().awaiting).toBe(second.id);
  expect(cadSolveClaim(second)?.modelDisplayAcknowledged).toBe(true);
  expect(prepare).not.toHaveBeenCalled();
  expect(f.display.reportDiagnostic.mock.calls).toEqual([]);
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
  expect(f.display.reportStatus).toHaveBeenLastCalledWith(expect.stringContaining('when you return to CAD or Results'));
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

it('leaves an earlier held request to the request list at start, and shows one held in this session', async () => {
  const request = preparing();
  request.status = 'held'; request.created_at = new Date(NOW - 3_600_000).toISOString();
  request.completed_at = new Date(NOW - 3_000_000).toISOString();
  request.cad_state = { ...request.cad_state!, state: 'needs_user_input', held: true };
  const mode = workspaceModeStore.getSnapshot().mode;
  const f = fixture(); await f.observe([request]);
  expect(f.display.showIngestedMesh).not.toHaveBeenCalled();
  expect(useCadReturnStore.getState().selectedBundle?.requestId).not.toBe(request.id);
  expect(workspaceModeStore.getSnapshot().mode).toBe(mode);
  // The same request held again just now (a continuation answered elsewhere, or a newer hold) is shown.
  await f.observe([{ ...request, completed_at: new Date(NOW).toISOString() }]);
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


it.each(['running', 'complete'] as const)('owns the first already-bound %s snapshot after reload or reconnect', async status => {
  const response = deferred<Response>();
  const f = fixture(vi.fn(async input => String(input).endsWith('/workspace') ? response.promise : json(record)));
  const run = { ...external('second', NOW), run_number: 2, status, cad_intent: null, has_results: status === 'complete' };
  expect(f.ownsReturn(bundle, [run])).toBe(true);
  const opening = f.observe([run]);
  const prepare = vi.fn();
  const reader = createCadReturnArrivalReader({ initialChoiceOwed: { current: true },
    ownsReturn: item => f.ownsReturn(item, [run]), ownsSolveReturn: item => f.ownsReturn(item, [run]),
    autoIngestSelected: prepare,
  }, (async () => json({ cadFolderConfigured: true, items: [bundle] })) as typeof fetch);
  await reader.refresh({ background: true, autoOpenNew: true });
  expect(prepare).not.toHaveBeenCalled();
  response.resolve(json(workspaceFor())); await opening;
  expect(currentModel().ingestId).toBe(record.ingest_id);
  expect(currentModel().viewportShowing).toBe(true);
  expect(cadSolveClaim(run)?.modelDisplayAcknowledged).toBe(true);
  expect(f.ownsReturn(bundle, [run])).toBe(false);
});

it('keeps a user model pick made after binding when run #2 completes', async () => {
  const response = deferred<Response>();
  const f = fixture(vi.fn(() => response.promise));
  const first = { ...external('first'), status: 'complete' as const, run_number: 1 };
  const second = { ...external('second', NOW), cad_intent: null, run_number: 2 };
  const opening = f.observe([second, first]);
  const picked = { ...bundle, bundlePath: 'wgreturn/picked.wgreturn', returnId: 'wgr_picked' };
  useCadReturnStore.getState().selectBundle(picked);
  response.resolve(json(workspaceFor())); await opening;
  await f.observe([{ ...second, status: 'complete', has_results: true }, first]);
  expect(useCadReturnStore.getState().selectedBundle).toBe(picked);
  expect(f.display.showIngestedMesh).not.toHaveBeenCalled();
  expect(compareSelection.getSnapshot().awaiting).toBeNull();
});

it.each([false, true])('opens a newer same-path return with unchanged folder time (prior displayed: %s)', async displayed => {
  const f = fixture(); const run = external();
  if (displayed) await f.observe([run]);
  const newer = { ...bundle, returnId: 'wgr_new' };
  const prepare = vi.fn();
  let listed = bundle;
  const reader = createCadReturnArrivalReader({ initialChoiceOwed: { current: false },
    ownsReturn: item => f.ownsReturn(item, [run]), ownsSolveReturn: item => f.ownsReturn(item, [run]),
    autoIngestSelected: prepare,
  }, (async () => json({ cadFolderConfigured: true, items: [listed] })) as typeof fetch);
  await reader.refresh({ background: true, autoOpenNew: true });
  expect(prepare).not.toHaveBeenCalled();
  listed = newer;
  expect(f.ownsReturn(newer, [run])).toBe(false);
  await reader.refresh({ background: true, autoOpenNew: true });
  expect(useCadReturnStore.getState().selectedBundle?.returnId).toBe(newer.returnId);
  expect(prepare).toHaveBeenCalledOnce();
});

it('does not retain ownership for displayed, old, WG-owned, manual, or superseded runs', () => {
  const f = fixture(); const run = external();
  expect(f.ownsReturn(bundle, [run])).toBe(true);
  rememberCadSolve(run.id, ''); acknowledgeCadSolve(run, 'display');
  expect(f.ownsReturn(bundle, [run])).toBe(false);
  expect(f.ownsReturn(bundle, [{ ...external('old', NOW - 600_001), status: 'complete' }])).toBe(false);
  const owned = external('owned'); rememberCadSolve(owned.id, '', 'wg-ingest');
  expect(f.ownsReturn(bundle, [owned])).toBe(false);
  expect(f.ownsReturn(bundle, [{ ...external('manual'), cad_provenance: { manual_waiting: true } }])).toBe(false);
  const parent = external('parent');
  const child = { ...external('child', NOW), parent_job_id: parent.id,
    cad_return: { bundle_path: 'wgreturn/child.wgreturn', return_id: 'wgr_child' } };
  expect(f.ownsReturn(bundle, [parent, child])).toBe(false);
});


it('owns an older delivery without a return id while rejecting a later same-path export', async () => {
  const response = deferred<Response>();
  const f = fixture(vi.fn(async input => String(input).endsWith('/workspace') ? response.promise : json(record)));
  const run = { ...external(), cad_return: { bundle_path: bundle.bundlePath, return_id: null } };
  expect(f.ownsReturn(bundle, [run])).toBe(true);
  expect(f.ownsReturn({ ...bundle, returnId: 'wgr_later', modifiedAt: new Date(NOW).toISOString() }, [run])).toBe(false);
  const opening = f.observe([run]);
  const prepare = vi.fn();
  const reader = createCadReturnArrivalReader({ initialChoiceOwed: { current: false },
    seenReturnRevisions: { current: new Map() },
    ownsReturn: item => f.ownsReturn(item, [run]), ownsSolveReturn: item => f.ownsReturn(item, [run]),
    autoIngestSelected: prepare,
  }, (async () => json({ cadFolderConfigured: true, items: [bundle] })) as typeof fetch);
  await reader.refresh({ background: true, autoOpenNew: true });
  response.resolve(json(workspaceFor())); await opening;
  expect(prepare).not.toHaveBeenCalled();
  expect(currentModel().viewportShowing).toBe(true);
});

it.each(['preparing', 'held', 'complete'] as const)('shows an older explicit %s request through the existing owner', async (status) => {
  const activate = vi.fn(() => true); disposers.push(bindWorkspaceNavigation(activate));
  const f = fixture();
  const old = status === 'complete'
    ? { ...external('older', NOW - 40 * 86400_000), status, has_results: true }
    : { ...preparing(), id: 'older', status, created_at: new Date(NOW - 40 * 86400_000).toISOString() };
  acknowledgeCadSolve(old, 'display');
  const newer = external('newer');
  await f.observe([newer, old]);
  vi.mocked(f.fetcher).mockClear();
  await f.showRequest({ operationId: 'older', jobId: 'older' });
  expect(activate).toHaveBeenLastCalledWith('results');
  expect(f.fetcher).toHaveBeenCalled();
  if (status === 'complete') expect(compareSelection.getSnapshot().primary).toBe('older');
  else expect(currentModel().ingestId).toBe(record.ingest_id);
});

it.each(['navigation', 'edit', 'choice', 'dispose'] as const)('keeps a later %s while an explicit result is loading', async (change) => {
  const reply = deferred<Response>();
  const f = fixture(vi.fn(() => reply.promise));
  const run = { ...external('old', NOW - 86400_000), status: 'complete' as const, has_results: true };
  await f.observe([run]);
  const pending = f.showRequest({ operationId: 'old', jobId: 'old' });
  if (change === 'navigation') noteExplicitNavigation();
  if (change === 'edit') useSolveOptionsStore.getState().setVerbose(true);
  if (change === 'choice') useCadReturnStore.getState().selectBundle({ ...bundle, bundlePath: 'wgreturn/other.wgreturn' });
  if (change === 'dispose') f.dispose();
  compareSelection.setPrimary('person-choice');
  reply.resolve(json(workspaceFor()));
  await pending;
  expect(f.display.showIngestedMesh).not.toHaveBeenCalled();
  expect(compareSelection.getSnapshot().primary).toBe('person-choice');
});

it('captures user intent before fetching an explicit job outside the current page', async () => {
  const response = deferred<Response>();
  const f = fixture(vi.fn(() => response.promise));
  const pending = f.showRequest({ operationId: 'older', jobId: 'older' });
  noteExplicitNavigation();
  response.resolve(json(external('older')));
  await pending;
  expect(f.display.onIntent).not.toHaveBeenCalled();
  expect(f.display.showIngestedMesh).not.toHaveBeenCalled();
});

it('releases explicit history for a newly pressed Fusion Solve', async () => {
  const f = fixture(); const old = preparing();
  await f.observe([old]);
  await f.showRequest({ operationId: old.id, jobId: old.id });
  const newer = { ...preparing(null), id: 'new-press', client_request_id: 'cad-solve:new-press',
    cad_state: { ...preparing(null).cad_state!, operation_id: 'new-press' }, created_at: new Date(NOW + 1000).toISOString() };
  await f.observe([newer, old]);
  expect(useCadReturnStore.getState().selectedBundle?.requestId).toBe('new-press');
});

it.each([false, true])('opens a Send through existing CAD selection and respects intervening navigation (%s)', async (navigate) => {
  const listing = deferred<Response>();
  const fetcher = vi.fn(async input => String(input).endsWith('/returns') ? listing.promise
    : json({ operationId: 'send-request', kind: 'receive_snapshot', snapshot: { bundlePath: bundle.bundlePath } }));
  const f = fixture(fetcher);
  const pending = f.showRequest({ operationId: 'send-request' });
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  if (navigate) noteExplicitNavigation();
  listing.resolve(json({ items: [bundle] }));
  await pending;
  if (navigate) expect(f.display.onIntent).not.toHaveBeenCalled();
  else expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(bundle.bundlePath);
  expect(fetcher.mock.calls.every((call) => !String(call[0]).includes('/ingest'))).toBe(true);
});

it('uses the server-named retry job even when its parent remains in the current page', async () => {
  const parent = { ...external('parent', NOW - 86400_000), cad_state: { ...external().cad_state!, operation_id: 'request' } };
  const child = { ...external('child', NOW - 86400_000), cad_state: { ...external().cad_state!, operation_id: 'request' } };
  const fetcher = vi.fn(async input => String(input).startsWith('/api/status/') ? json(child)
    : json(String(input).endsWith('/workspace') ? workspaceFor() : record));
  const f = fixture(fetcher);
  acknowledgeCadSolve(parent, 'display');
  await f.observe([parent]);
  await f.showRequest({ operationId: 'request', jobId: 'child' });
  expect(fetcher).toHaveBeenCalledWith('/api/status/child');
  expect(fetcher).toHaveBeenCalledWith('/api/runs/child/workspace', undefined);
});

it('keeps a newer Send while an explicit history model is loading', async () => {
  const reply = deferred<Response>();
  const f = fixture(vi.fn(() => reply.promise));
  const run = { ...external('old', NOW - 86400_000), status: 'complete' as const, has_results: true };
  await f.observe([run]);
  const pending = f.showRequest({ operationId: 'old', jobId: 'old' });
  f.send(NOW + 1);
  reply.resolve(json(workspaceFor()));
  await pending;
  expect(f.display.showIngestedMesh).not.toHaveBeenCalled();
});


it('keeps an explicit fetched job through an empty socket page while its model loads', async () => {
  const reply = deferred<Response>();
  const old = { ...external('outside-page', NOW - 86400_000), status: 'complete' as const, has_results: true };
  const f = fixture(vi.fn(async input => String(input).startsWith('/api/status/') ? json(old)
    : String(input).endsWith('/workspace') ? reply.promise : json(record)));
  const opening = f.showRequest({ operationId: old.id, jobId: old.id });
  await vi.waitFor(() => expect(f.fetcher).toHaveBeenCalledWith('/api/runs/outside-page/workspace', undefined));
  const snapshot = f.observe([]);
  reply.resolve(json(workspaceFor()));
  await opening; await snapshot;
  expect(f.display.showIngestedMesh).toHaveBeenCalledOnce();
  expect(compareSelection.getSnapshot().primary).toBe(old.id);
  await f.observe([external('automatic', NOW - 1000)]);
  expect(compareSelection.getSnapshot().primary).toBe(old.id);
});

it('allows automatic following again after the person moves on from explicit history', async () => {
  const f = fixture(); const old = preparing();
  await f.observe([old]);
  await f.showRequest({ operationId: old.id, jobId: old.id });
  noteExplicitNavigation();
  const newer = { ...preparing(null), id: 'new-press', client_request_id: 'cad-solve:new-press',
    cad_state: { ...preparing(null).cad_state!, operation_id: 'new-press' }, created_at: new Date(NOW + 1000).toISOString() };
  await f.observe([newer, old]);
  expect(useCadReturnStore.getState().selectedBundle?.requestId).toBe('new-press');
});

it.each(['missing-job', 'missing-operation', 'refused-before-job', 'inactive'] as const)('explains an unavailable explicit request calmly: %s', async (scenario) => {
  const f = fixture(vi.fn(async () => scenario === 'refused-before-job' ? json({ kind: 'prepare_and_solve' }) : new Response('', { status: 404 })));
  if (scenario === 'inactive') f.setActive(false);
  await f.showRequest({ operationId: 'gone', ...(scenario === 'missing-job' ? { jobId: 'gone' } : {}) });
  expect(f.display.reportStatus).toHaveBeenCalledExactlyOnceWith(scenario === 'inactive'
    ? 'Switch to Fusion mode to show this request.' : 'This request is no longer in Waveguide Generator.');
});


it('keeps older automatic returns from overriding an explicit history selection', async () => {
  const f = fixture(); const old = preparing();
  await f.observe([old]);
  await f.showRequest({ operationId: old.id, jobId: old.id });
  const prepare = vi.fn(async () => ({ ok: true as const }));
  const later = { ...bundle, bundlePath: 'wgreturn/later.wgreturn', returnId: 'later', modifiedAt: new Date(NOW - 1000).toISOString() };
  const reader = createCadReturnArrivalReader({ initialChoiceOwed: { current: false },
    seenReturnRevisions: { current: new Map() },
    ownsReturn: item => f.ownsReturn(item, []), ownsSolveReturn: item => f.ownsReturn(item, []),
    autoIngestSelected: prepare,
  }, (async () => json({ cadFolderConfigured: true, items: [later] })) as typeof fetch);
  await reader.refresh({ background: true, autoOpenNew: true });
  expect(prepare).not.toHaveBeenCalled();
  expect(currentModel().ingestId).toBe(record.ingest_id);
  noteExplicitNavigation();
  expect(f.ownsReturn(later, [])).toBe(false);
});

it('reports one calm line when an explicit run has lost its retained model', async () => {
  const old = { ...external('lost', NOW - 86400_000), status: 'complete' as const, has_results: true };
  const f = fixture(vi.fn(async input => String(input).startsWith('/api/status/') ? json(old) : new Response('', { status: 404 })));
  await f.showRequest({ operationId: old.id, jobId: old.id });
  const messages = f.display.reportStatus.mock.calls.map(([message]) => message);
  expect(messages.filter(message => message === 'This request is no longer in Waveguide Generator.')).toHaveLength(1);
  expect(messages.some(message => message.includes('No run with id') || message.includes('could not show the Fusion model'))).toBe(false);
});


it('keeps explicit history through snapshots, older jobs and its own newer retry', async () => {
  const f = fixture(); const old = preparing();
  await f.observe([old]);
  await f.showRequest({ operationId: old.id, jobId: old.id });
  await f.observe([]);
  const retry = { ...preparing(null), id: 'retry', parent_job_id: old.id,
    created_at: new Date(NOW + 1000).toISOString() };
  await f.observe([retry]);
  await f.observe([external('older', NOW - 1000)]);
  expect(f.latestSentAt()).toBe(Date.parse(old.created_at));
  expect(currentModel().ingestId).toBe(record.ingest_id);
});

it('discovers a new Send after its receipt arrives while history is explicitly selected', async () => {
  const f = fixture(); const old = preparing();
  await f.observe([old]);
  await f.showRequest({ operationId: old.id, jobId: old.id });
  const later = { ...bundle, bundlePath: 'wgreturn/new-send.wgreturn', returnId: 'new-send',
    modifiedAt: new Date(NOW + 1000).toISOString() };
  // Receipt ownership is known before the return listing changes; the explicit
  // Solve owner must not eat the return's revision during that listing.
  const prepare = vi.fn(async () => ({ ok: true as const }));
  const seen = { current: new Map<string, string>() };
  const reader = createCadReturnArrivalReader({ initialChoiceOwed: { current: false },
    seenReturnRevisions: seen, sendOwnersKnown: () => true,
    ownsReturn: item => f.ownsReturn(item, []), autoIngestSelected: prepare,
  }, (async () => json({ cadFolderConfigured: true, items: [later] })) as typeof fetch);
  await reader.refresh({ background: true, autoOpenNew: true });
  expect(useCadReturnStore.getState().selectedBundle?.returnId).toBe('new-send');
  expect(prepare).toHaveBeenCalledOnce();
});

it('does not claim an unseen newer Send while an explicit selection still holds', async () => {
  const f = fixture(); const old = preparing();
  await f.observe([old]); await f.showRequest({ operationId: old.id, jobId: old.id });
  expect(f.ownsReturn({ ...bundle, returnId: 'new-send', modifiedAt: new Date(NOW + 1000).toISOString() }, [])).toBe(false);
});

it('keeps the shown preparation ready if tab navigation cancels the same run setup load', async () => {
  const accepted = deferred<Response>();
  const f = fixture(vi.fn(async input => String(input).endsWith('/workspace') ? accepted.promise : json(record)));
  await f.observe([preparing()]);
  expect(useCadReturnStore.getState().needsIngest).toBe(false);
  const run = external(); const loading = f.observe([run]);
  noteExplicitNavigation();
  accepted.resolve(json(workspaceFor())); await loading;
  await f.observe([{ ...run, status: 'complete', has_results: true }]);
  expect(useCadReturnStore.getState().needsIngest).toBe(false);
  expect(useImportedModelStore.getState().phase).toBe('ready');
  expect(currentModel().viewportShowing).toBe(true);
  expect(f.display.reportStatus.mock.calls.at(-1)?.[0]).not.toContain('Loading');
});


it('leaves a saved model selected at restart but still allows Show of a recent finished request', async () => {
  rememberLastModel({ model_id: 'wgl_chosen', input: 'cad' });
  const f = fixture();
  const old = { ...external(), status: 'complete' as const, has_results: true,
    completed_at: new Date(NOW - 60_000).toISOString() };
  await f.observe([old]);
  expect(f.fetcher).not.toHaveBeenCalled();
  expect(f.ownsReturn(bundle, [old])).toBe(false);
  await f.showRequest({ operationId: old.id, jobId: old.id });
  expect(currentModel().ingestId).toBe(record.ingest_id);
  expect(f.display.showIngestedMesh).toHaveBeenCalledOnce();
});

it.each(['active', 'newly completed'] as const)('lets a %s Fusion solve own display despite a remembered model', async state => {
  rememberLastModel({ model_id: 'wgl_chosen', input: 'cad' });
  const f = fixture();
  const run = state === 'active' ? external()
    : { ...external(), status: 'complete' as const, has_results: true, completed_at: new Date(NOW + 1000).toISOString() };
  await f.observe([run]);
  expect(currentModel().ingestId).toBe(record.ingest_id);
  expect(f.display.showIngestedMesh).toHaveBeenCalledOnce();
});
