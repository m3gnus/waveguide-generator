import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { compareSelection } from '../../api/results';
import { cadSolveClaim, rememberCadSolve } from '../../jobs/cadSolve';
import { currentModel, runVerdict } from '../../model/currentModel';
import { resetCadReturnStore, useCadReturnStore } from '../../stores/cadReturn';
import { resetDesignStore, useDesignStore } from '../../stores/design';
import { resetDocumentStore } from '../../stores/document';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../../stores/solveOptions';
import { importedMeshStore } from '../../viewport/importedMeshStore';
import { workspaceModeStore } from '../../stores/workspaceMode';
import { noteExplicitNavigation, resetWorkspaceNavigationForTests } from '../workspaceNavigation';
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
  const display = { isActive: () => true, newestSendAt: () => sendAt, onIntent: vi.fn(),
    enterCadWorkspace: () => workspaceModeStore.setMode('cad'), reportStatus: vi.fn(), reportViewportNotice: vi.fn(),
    showIngestedMesh: vi.fn(async (r: typeof record, _name: string, _notice: unknown, _fetcher: unknown, generation: number) => {
      importedMeshStore.setCad({ source: 'cad', ingestId: r.ingest_id } as never, generation, true);
    }) };
  const controller = createFusionSolveDisplay(display, fetcher, () => NOW);
  disposers.push(controller.dispose);
  return { ...controller, display, fetcher,
    send: (at: number) => { sendAt = at; } };
}
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear(); resetDesignStore(); resetDocumentStore(); resetCadReturnStore();
  resetSolveOptionsStore(); resetWorkspaceNavigationForTests(); resetSolveAttentionForTests(); compareSelection.clear();
  importedMeshStore.clear(); workspaceModeStore.setMode('cad');
});

it('loads the exact solved model when WG first sees a Fusion job already accepted', async () => {
  useCadReturnStore.getState().selectBundle({ ...bundle, documentName: 'Older fixture' });
  const old = { ...record, ingest_id: 'old', manifest_sha256: 'sha256:old' };
  useCadReturnStore.getState().applyIngest(old, useCadReturnStore.getState().beginIngestIntent());
  importedMeshStore.setCad({ source: 'cad', ingestId: old.ingest_id } as never);
  const f = fixture(); const run = external();
  expect(cadSolveClaim(run)).toBeNull();
  await f.observe([run]);
  expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(record.ingest_id);
  expect(runVerdict(run, currentModel())).toBe('current');
  expect(compareSelection.getSnapshot().awaiting).toBe(run.id);
  expect(cadSolveClaim(run)?.completionAcknowledged).toBe(true);
  await f.observe([run]);
  expect(f.display.showIngestedMesh).toHaveBeenCalledOnce();
});

it('recovers a recent completed Fusion solve but does not replay old finished history', async () => {
  const f = fixture();
  await f.observe([{ ...external('old', NOW - 600_001), status: 'complete', has_results: true }]);
  expect(f.fetcher).not.toHaveBeenCalled();
  await f.observe([{ ...external('recent'), status: 'complete', has_results: true }]);
  expect(f.display.showIngestedMesh).toHaveBeenCalledOnce();
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
  expect(f.display.reportStatus).toHaveBeenLastCalledWith(expect.stringContaining('missing'));
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
