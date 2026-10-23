import { act, useSyncExternalStore } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket } from '../api/jobsSocket';
import { cadJobFixture, migratedCadRequestFixture, publishCadJobs } from '../jobs/cadSolve.fixtures';
import { cadJobSummary } from '../jobs/cadSolve';
import { cadRecoveryNoticesStore } from '../stores/cadRecoveryNotices';
import { useCadReturnStore } from '../stores/cadReturn';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { PanelVisibilityContext } from './panelVisibility';
import { AttentionNotices } from './TopBar';
import { ImportedWorkspacePanel } from './ImportedWorkspacePanel';
import { CadOperationsSection, OnScreenSolveStatus } from './CadOperationsSection';
import { bindWorkspaceNavigation, publishVisiblePanels, resetWorkspaceNavigationForTests, workspaceNavigation } from './workspaceNavigation';

const coordinator = vi.hoisted(() => ({ reportError: vi.fn(), reportStatus: vi.fn(), fusionStatus: null }));
vi.mock('../stores/cadWorkspace', () => ({ cadWorkspace: { subscribe: () => () => undefined, getSnapshot: () => coordinator } }));
vi.mock('./CadProjectPanel', () => ({ openCadProject: vi.fn() }));
// Keep CAD connection controls out of this routing test; use the real request owners.
vi.mock('../design/ParamPanel', () => ({ ParamPanel: () => null }));
vi.mock('./CadLinkPanel', () => ({ CadLinkPanel: () => <div className="cadlink-panel"><CadOperationsSection record={null}/></div> }));

function Surface() {
  const { mode } = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot);
  useSyncExternalStore(workspaceNavigation.subscribe, workspaceNavigation.getSnapshot);
  return <><AttentionNotices/>{(mode === 'cad' || workspaceNavigation.isVisible('geometry')) && <ImportedWorkspacePanel/>}</>;
}

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });

describe('Show reveals the requests it counts', () => {
  let host: HTMLDivElement;
  let root: Root;
  let unbind: () => void;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetWorkspaceNavigationForTests();
    cadRecoveryNoticesStore.setState({ notices: {} });
    publishCadJobs([migratedCadRequestFixture()]);
    coordinator.reportError.mockReset();
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    unbind = bindWorkspaceNavigation(destination => {
      publishVisiblePanels([destination]); return true;
    });
  });
  afterEach(() => {
    act(() => root.unmount()); host.remove(); unbind();
    publishCadJobs([]); cadRecoveryNoticesStore.setState({ notices: {} });
    workspaceModeStore.setMode('parametric'); workspaceModeStore.setImportedInput('cad');
    useCadReturnStore.setState({ ingestRecord: null }); importedMeshStore.clear();
    resetWorkspaceNavigationForTests(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  });

  it.each(['parametric', 'cad', 'file'] as const)('opens and focuses the first earlier request from %s without replacing its mesh', async (input) => {
    workspaceModeStore.setMode(input === 'parametric' ? 'parametric' : 'cad');
    workspaceModeStore.setImportedInput(input === 'file' ? 'file' : 'cad');
    importedMeshStore.showParametric();
    const mesh = importedMeshStore.getSnapshot();
    const workingMode = workspaceModeStore.getSnapshot().mode;
    const importedInput = workspaceModeStore.getSnapshot().importedInput;
    const intent = importedMeshStore.captureGeneration();
    await act(async () => root.render(<Surface/>));
    const notice = host.querySelector<HTMLButtonElement>('.attention-waiting')!;
    expect(notice).not.toBeNull();
    notice.focus(); expect(document.activeElement).toBe(notice);
    // Keyboard activation of a native button dispatches a click with detail 0.
    await act(async () => { notice.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 0 })); });
    const disclosure = host.querySelector<HTMLDetailsElement>('.cad-earlier-requests')!;
    const first = disclosure.querySelector<HTMLElement>('li')!;
    expect(disclosure.open).toBe(true);
    expect(document.activeElement).toBe(first);
    expect(first.textContent).toContain('Solve request · kleinuhringur v1');
    expect(first.textContent).toContain('needs its solver frame confirmed');
    expect(first.textContent).toContain(migratedCadRequestFixture().cad_state!.message);
    expect(first.querySelector('time')?.dateTime).toBe(migratedCadRequestFixture().created_at);
    expect(first.querySelector('button')?.textContent).toBe('Open kleinuhringur v1');
    expect(workspaceModeStore.getSnapshot().mode).toBe(workingMode);
    expect(workspaceModeStore.getSnapshot().importedInput).toBe(importedInput);
    expect(importedMeshStore.getSnapshot()).toBe(mesh);
    expect(importedMeshStore.captureGeneration()).toBe(intent);
    expect(workspaceNavigation.getRevealTarget()).toBeNull();
  });

  it('counts exactly the refused and recovery rows while active jobs have Stop and survive Clear all', async () => {
    workspaceModeStore.setMode('cad'); workspaceModeStore.setImportedInput('file');
    const refused = migratedCadRequestFixture();
    const active = cadJobFixture({ ...cadJobSummary(refused), jobId: 'active-job', operationId: 'active-operation', state: 'processing', reason: null });
    publishCadJobs([refused, active]);
    const recovery = { id: 'recovery', kind: 'update_link' as const, document_id: null, document_name: 'Other', created_at: '2026-10-01', sequence: 1, active_document: false };
    cadRecoveryNoticesStore.setState({ notices: { recovery } });
    const dismissNotice = vi.spyOn(cadRecoveryNoticesStore.getState(), 'dismiss').mockResolvedValue();
    const stopped = vi.spyOn(jobsSocket, 'stopJob').mockResolvedValue();
    vi.spyOn(jobsSocket, 'refresh').mockResolvedValue();
    const fetcher = vi.fn(async () => json({})); vi.stubGlobal('fetch', fetcher);
    await act(async () => root.render(<Surface/>));
    expect(host.querySelector('.attention-waiting')?.textContent).toBe('2 CAD requests need you · Show');
    const rows = host.querySelectorAll('.cad-earlier-requests li');
    expect(rows).toHaveLength(2);
    const stop = host.querySelector<HTMLButtonElement>('button[aria-label="Stop: kleinuhringur v1"]')!;
    expect(stop).not.toBeNull();
    expect(stop.closest('.cad-operation')?.querySelector('[aria-label^="Dismiss"]')).toBeNull();
    await act(async () => { [...host.querySelectorAll('button')].find(button => button.textContent === 'Clear all')!.click(); });
    expect(fetcher).toHaveBeenCalledExactlyOnceWith(`/api/jobs/${refused.id}/dismiss`, expect.anything());
    expect(dismissNotice).toHaveBeenCalledExactlyOnceWith('recovery');
    expect(stopped).not.toHaveBeenCalled();
    await act(async () => stop.click());
    expect(stopped).toHaveBeenCalledExactlyOnceWith('active-job');
  });

  it('reports partial failure and lets the remaining row be retried', async () => {
    workspaceModeStore.setMode('cad'); workspaceModeStore.setImportedInput('file');
    const fetcher = vi.fn().mockResolvedValueOnce(json({ detail: 'offline' }, 503)).mockResolvedValue(json({}));
    vi.stubGlobal('fetch', fetcher); vi.spyOn(jobsSocket, 'refresh').mockResolvedValue();
    await act(async () => root.render(<Surface/>));
    const clear = [...host.querySelectorAll('button')].find(button => button.textContent === 'Clear all')!;
    await act(async () => clear.click());
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('offline');
    expect(clear.disabled).toBe(false);
    await act(async () => clear.click());
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });

  it('focuses the existing Solve card when it owns the only current request', async () => {
    const job = migratedCadRequestFixture();
    const record = { manifest_sha256: job.cad_state!.snapshot!.manifest_sha256 } as never;
    vi.stubGlobal('fetch', vi.fn(async () => json({ findings: [] })));
    await act(async () => root.render(<div className="cadlink-panel">
      <OnScreenSolveStatus record={record}/><CadOperationsSection record={record} solves={false}/>
    </div>));
    await act(async () => { workspaceNavigation.reveal('cad-requests'); });
    expect(document.activeElement).toBe(host.querySelector('.cad-operation'));
    expect(host.querySelectorAll('.cad-operation')).toHaveLength(1);
    expect(workspaceNavigation.getRevealTarget()).toBeNull();
  });

  it('waits for dock visibility before taking keyboard focus', async () => {
    let visible = false;
    const listeners = new Set<() => void>();
    const visibility = { get isVisible() { return visible; }, subscribe(listener: () => void) {
      listeners.add(listener); return () => { listeners.delete(listener); };
    } };
    await act(async () => root.render(<PanelVisibilityContext.Provider value={visibility}>
      <CadOperationsSection record={null}/>
    </PanelVisibilityContext.Provider>));
    await act(async () => { workspaceNavigation.reveal('cad-requests'); });
    expect(host.querySelector<HTMLDetailsElement>('details')?.open).toBe(false);
    expect(workspaceNavigation.getRevealTarget()).toBe('cad-requests');
    await act(async () => { visible = true; listeners.forEach(listener => listener()); });
    expect(document.activeElement).toBe(host.querySelector('li'));
    expect(workspaceNavigation.getRevealTarget()).toBeNull();
  });

  it('cancels a pending reveal when the user navigates elsewhere before mounting', async () => {
    workspaceModeStore.setMode('cad'); workspaceModeStore.setImportedInput('file');
    workspaceNavigation.reveal('cad-requests'); workspaceNavigation.navigate('jobs');
    await act(async () => root.render(<Surface/>));
    expect(host.querySelector<HTMLDetailsElement>('details')?.open).toBe(false);
    expect(workspaceNavigation.getRevealTarget()).toBeNull();
  });
});
