import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadOperationListener, JobItem, JobsSnapshot } from '../api/jobsSocket';
import { createCadConnectionStore, type CadConnectionStatus } from '../stores/cadConnection';
import { connectCadObservation, type CadObservationActivity } from './cadObservation';
import { fusionWorkflowView } from './cadWorkflowView';

function status(overrides: Partial<CadConnectionStatus> = {}): CadConnectionStatus {
  return {
    cadApplication: 'fusion360', cadFolderConfigured: true, cadFolderPath: '/workspace',
    state: 'current', processRunning: true, running: true, updatedAt: new Date().toISOString(),
    documentName: 'Speaker', documentId: 'document-1', currentFormula: 'OSSE', fusionFormula: 'OSSE',
    link: null, wgChangesAvailable: false, fusionChangesAvailable: false, documentChanged: false,
    documentChangeDetectable: true, staleDetectionExplanation: null, observationFreshness: 'current',
    addinInboxTransfer: true, statusTtlSeconds: 20,
    realizedDimensions: { state: 'no_link', instanceId: null, exportId: null, parameters: [] },
    ...overrides,
  };
}
// No coordinator, operation store, React root, or actual server is mounted.
describe('CAD observation scheduler without the coordinator', () => {
  let activity: CadObservationActivity;
  let snapshot: JobsSnapshot;
  let channel: CadOperationListener;
  let activityChanged: () => void;
  let jobsChanged: () => void;
  let stop: ReturnType<typeof connectCadObservation> | undefined;
  let connection: ReturnType<typeof createCadConnectionStore>;
  let readStatus: ReturnType<typeof vi.fn<() => Promise<CadConnectionStatus>>>;
  let reloadReturns: ReturnType<typeof vi.fn<() => Promise<void>>>;
  let reloadReceipts: ReturnType<typeof vi.fn<(ids: readonly string[]) => Promise<ReadonlySet<string>>>>;
  let refreshJobs: ReturnType<typeof vi.fn<() => Promise<void>>>;
  let visible: boolean;
  beforeEach(() => {
    vi.useFakeTimers(); vi.setSystemTime('2026-10-11T12:00:00Z');
    visible = true;
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visible ? 'visible' : 'hidden');
    activity = { cadApplication: 'fusion360', cadFolderConfigured: true, coordination: 'off',
      workspaceMode: 'cad', observationKey: 'design-1', requests: [], awaitingReturn: false };
    snapshot = { connection: 'disconnected', jobs: [], epoch: null, cursor: null, error: null };
    connection = createCadConnectionStore();
    readStatus = vi.fn(async () => status());
    reloadReturns = vi.fn(async () => undefined);
    reloadReceipts = vi.fn(async (ids: readonly string[]) => new Set(ids));
    refreshJobs = vi.fn(async () => undefined);
  });
  afterEach(() => { stop?.(); stop = undefined; connection.invalidate(); vi.restoreAllMocks(); vi.useRealTimers(); });
  async function mount() {
    stop = connectCadObservation({
      getActivity: () => activity,
      subscribeActivity: (listener) => { activityChanged = listener; return vi.fn(); },
      readFusionStatus: () => readStatus(), reloadReturns: () => reloadReturns(),
      reloadReceipts: (ids) => reloadReceipts(ids), connection,
      manager: {
        getSnapshot: () => snapshot,
        subscribe: (listener) => { jobsChanged = listener; return vi.fn(); },
        subscribeCadOperations: (listener) => { channel = listener; return vi.fn(); },
        refresh: () => refreshJobs(),
      },
    });
    await vi.advanceTimersByTimeAsync(0);
  }
  function change(next: Partial<CadObservationActivity>) { activity = { ...activity, ...next }; activityChanged(); }
  const reads = () => [readStatus.mock.calls.length, reloadReturns.mock.calls.length];
  const cadJob = (jobStatus = 'preparing', state = 'processing') => ({ id: 'job-1', status: jobStatus,
    cad_state: { state } } as JobItem);
  async function reconnect() { snapshot = { ...snapshot, connection: 'connected' }; channel.resync(); await vi.advanceTimersByTimeAsync(0); }

  it('off: idle CAD work makes no routine reads over ten minutes', async () => {
    await mount(); const mounted = reads();
    await vi.advanceTimersByTimeAsync(600_000);
    expect(reads()).toEqual(mounted);
    expect(refreshJobs).not.toHaveBeenCalled(); expect(reloadReceipts).not.toHaveBeenCalled();
  });

  it.each(['unknown', 'off'] as const)('%s: defaults to silence until coordination is explicitly on', async (coordination) => {
    activity.coordination = coordination;
    await mount(); const mounted = reads();
    await vi.advanceTimersByTimeAsync(60_000); expect(reads()).toEqual(mounted);
    change({ coordination: 'on' }); await vi.advanceTimersByTimeAsync(5_000);
    expect(readStatus.mock.calls.length).toBeGreaterThan(mounted[0]);
  });

  it.each(['needs_user_input', 'recovery_required'])('off: parked %s requests stay quiet and moving them resumes reads', async (state) => {
    activity.requests = [{ id: 'request-1', state }];
    await mount(); const mounted = reads();
    await vi.advanceTimersByTimeAsync(60_000); expect(reads()).toEqual(mounted);
    change({ requests: [{ id: 'request-1', state: 'processing' }] });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(readStatus.mock.calls.length - mounted[0]).toBe(4);
    expect(reloadReturns.mock.calls.length - mounted[1]).toBe(4);
    change({ requests: [{ id: 'request-1', state: 'accepted' }] });
    const ended = reads(); await vi.advanceTimersByTimeAsync(600_000); expect(reads()).toEqual(ended);
  });

  it('off: moving CAD jobs resume reads, a parked solve and a finished job stop them', async () => {
    await mount(); snapshot = { ...snapshot, jobs: [cadJob()] }; jobsChanged();
    const before = reads(); await vi.advanceTimersByTimeAsync(5_000);
    expect(reads()).toEqual(before.map((count) => count + 2));
    snapshot = { ...snapshot, jobs: [cadJob('preparing', 'needs_user_input')] }; jobsChanged();
    const parked = reads(); await vi.advanceTimersByTimeAsync(60_000); expect(reads()).toEqual(parked);
    snapshot = { ...snapshot, jobs: [cadJob('running')] }; jobsChanged();
    await vi.advanceTimersByTimeAsync(2_500); expect(readStatus.mock.calls.length).toBe(parked[0] + 1);
    snapshot = { ...snapshot, jobs: [cadJob('complete', 'accepted')] }; jobsChanged();
    const ended = reads(); await vi.advanceTimersByTimeAsync(60_000); expect(reads()).toEqual(ended);
  });

  it('does not mistake an ordinary solver job for moving CAD work', async () => {
    snapshot.jobs = [{ id: 'ordinary', status: 'running' } as JobItem];
    await mount(); const before = reads(); await vi.advanceTimersByTimeAsync(60_000); expect(reads()).toEqual(before);
  });

  it('an awaiting return keeps reads moving until it settles', async () => {
    await mount(); change({ awaitingReturn: true }); const before = reads();
    await vi.advanceTimersByTimeAsync(5_000); expect(reads()).toEqual(before.map((count) => count + 2));
    change({ awaitingReturn: false }); const ended = reads();
    await vi.advanceTimersByTimeAsync(60_000); expect(reads()).toEqual(ended);
  });

  it.each(['listing change', 'Send'])('on: %s resets the quiet clock without changing context (row 10)', async () => {
    activity.coordination = 'on'; activity.workspaceMode = 'parametric'; activity.activityRevision = 0;
    readStatus.mockImplementation(async () => status({ state: 'closed', running: false, processRunning: false }));
    await mount(); await vi.advanceTimersByTimeAsync(60_000); const quiet = reads();
    change({ activityRevision: 1 });
    await vi.advanceTimersByTimeAsync(2_500); expect(reads()).toEqual(quiet.map((count) => count + 1));
    await vi.advanceTimersByTimeAsync(27_500); const quietAgain = reads();
    await vi.advanceTimersByTimeAsync(29_999); expect(reads()).toEqual(quietAgain);
  });

  it('on: quiet observations widen to 30 seconds and focus immediately wakes them', async () => {
    activity.coordination = 'on'; activity.workspaceMode = 'parametric';
    readStatus.mockImplementation(async () => status({ state: 'closed', running: false, processRunning: false }));
    await mount(); await vi.advanceTimersByTimeAsync(30_000); const quiet = reads();
    await vi.advanceTimersByTimeAsync(29_999); expect(reads()).toEqual(quiet);
    await vi.advanceTimersByTimeAsync(1); expect(reads()).toEqual(quiet.map((count) => count + 1));
    window.dispatchEvent(new Event('focus')); await vi.advanceTimersByTimeAsync(0);
    expect(reads()).toEqual(quiet.map((count) => count + 2));
    await vi.advanceTimersByTimeAsync(2_500); expect(reads()).toEqual(quiet.map((count) => count + 3));
  });

  it.each(['focus', 'cad-mode', 'folder', 'addin-session'])('off: %s wakes idle reads once without restarting routine reads', async (event) => {
    activity.workspaceMode = 'parametric'; await mount(); const before = reads();
    if (event === 'focus') window.dispatchEvent(new Event('focus'));
    if (event === 'cad-mode') change({ workspaceMode: 'cad' });
    if (event === 'folder') change({ cadFolderConfigured: false });
    if (event === 'addin-session') channel.addinStatusChanged?.();
    await vi.advanceTimersByTimeAsync(0); expect(reads()).toEqual(before.map((count) => count + 1));
    const woke = reads(); await vi.advanceTimersByTimeAsync(60_000); expect(reads()).toEqual(woke);
  });

  it.each(['focus', 'visibility', 'addin-session', 'resync', 'refresh'])('off: a held status survives %s and a failing wake read, then ages into last report', async (event) => {
    const link = { instanceId: 'instance-1', designName: 'WG design', linkName: 'Upper horn' } as NonNullable<CadConnectionStatus['link']>;
    readStatus.mockResolvedValueOnce(status({ link }));
    await mount();
    const held = connection.getState();
    await vi.advanceTimersByTimeAsync(5_000);
    readStatus.mockRejectedValue(new Error('offline'));
    if (event === 'focus') window.dispatchEvent(new Event('focus'));
    if (event === 'visibility') {
      visible = false; document.dispatchEvent(new Event('visibilitychange'));
      visible = true; document.dispatchEvent(new Event('visibilitychange'));
    }
    if (event === 'addin-session') channel.addinStatusChanged?.();
    if (event === 'resync') channel.resync();
    if (event === 'refresh') stop!.refresh();
    await vi.advanceTimersByTimeAsync(0);
    expect(readStatus).toHaveBeenCalledTimes(2);
    expect(connection.getState().fusionStatus).toBe(held.fusionStatus);
    expect(connection.getState().receivedAt).toBe(held.receivedAt);
    expect(connection.getState().expiresAt).toBe(held.expiresAt);
    expect(connection.getState().linkName).toBe('Upper horn');
    expect(connection.getState().fusionStatus?.processRunning).toBe(true);
    expect(fusionWorkflowView(connection.getState().fusionStatus).state).toBe('current');
    await vi.advanceTimersByTimeAsync(14_999);
    expect(connection.getState().fusionStatus?.statusObserved).not.toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(connection.getState().fusionStatus?.statusObserved).toBe(false);
    expect(fusionWorkflowView(connection.getState().fusionStatus).headline).toContain('last reported');
    expect(connection.getState().fusionStatus?.processRunning).toBe(true);
    expect(connection.getState().linkName).toBe('Upper horn');
    expect(readStatus).toHaveBeenCalledTimes(2);
  });

  it('off: a design/instance change fences the old report and reads status once', async () => {
    await mount(); const before = reads();
    change({ observationKey: 'design-2:instance-2' }); await vi.advanceTimersByTimeAsync(0);
    expect(reads()).toEqual([before[0] + 1, before[1]]);
    const changed = reads(); await vi.advanceTimersByTimeAsync(60_000); expect(reads()).toEqual(changed);
  });

  it('off: a stalled design-status read preserves the known inbox declaration and idle silence', async () => {
    await mount();
    readStatus.mockImplementation(() => new Promise(() => undefined));
    change({ observationKey: 'design-2' });
    const changed = reads(); await vi.advanceTimersByTimeAsync(60_000);
    expect(reads()).toEqual(changed);
  });

  it('suppresses hidden-page reads even with moving work, and resumes on visibility', async () => {
    activity.awaitingReturn = true; visible = false;
    await mount(); await vi.advanceTimersByTimeAsync(60_000); expect(reads()).toEqual([0, 0]);
    window.dispatchEvent(new Event('focus')); await vi.advanceTimersByTimeAsync(5_000); expect(reads()).toEqual([0, 0]);
    visible = true; document.dispatchEvent(new Event('visibilitychange')); await vi.advanceTimersByTimeAsync(0);
    expect(reads()).toEqual([1, 1]);
    await vi.advanceTimersByTimeAsync(2_500); expect(reads()).toEqual([2, 2]);
    visible = false; document.dispatchEvent(new Event('visibilitychange'));
    await vi.advanceTimersByTimeAsync(60_000); expect(reads()).toEqual([2, 2]);
    expect(connection.getState().fusionStatus?.statusObserved).toBe(false);
  });

  it('off: legacy add-ins keep return discovery until inbox delivery is declared', async () => {
    readStatus.mockImplementation(async () => status({ addinInboxTransfer: false }));
    await mount(); const before = reads(); await vi.advanceTimersByTimeAsync(5_000);
    expect(reads()).toEqual([before[0], before[1] + 2]);
    readStatus.mockImplementation(async () => status()); channel.addinStatusChanged?.();
    await vi.advanceTimersByTimeAsync(0); const inbox = reads();
    await vi.advanceTimersByTimeAsync(60_000); expect(reads()).toEqual(inbox);
  });

  it('on: with no folder, status clocks stop and discovery uses the idle rate', async () => {
    activity.coordination = 'on'; activity.workspaceMode = 'parametric'; activity.cadFolderConfigured = false;
    readStatus.mockImplementation(async () => status({ state: 'closed', running: false, processRunning: false }));
    await mount(); const before = reads(); await vi.advanceTimersByTimeAsync(30_000);
    expect(reads()).toEqual([before[0], before[1] + 1]);
  });

  it('a job ends while disconnected: reconnect reloads jobs and exact awaited receipt IDs before pending reload drops them', async () => {
    activity.requests = [{ id: 'send-exact', state: 'processing' }]; snapshot.jobs = [cadJob('running')];
    refreshJobs.mockImplementation(async () => { snapshot.jobs = [cadJob('complete', 'accepted')]; jobsChanged(); });
    reloadReceipts.mockImplementation(async (ids: readonly string[]) => {
      expect(ids).toEqual(['send-exact']);
      change({ requests: [] }); // completed receipt is absent from the pending feed
      return new Set(['send-exact']);
    });
    await mount(); await reconnect();
    expect(refreshJobs).toHaveBeenCalledTimes(1); expect(reloadReceipts).toHaveBeenCalledTimes(1);
    expect(snapshot.jobs[0].status).toBe('complete'); expect(connection.getState().recoveryError).toBeNull();
    const recovered = reads(); await vi.advanceTimersByTimeAsync(60_000); expect(reads()).toEqual(recovered);
  });

  it('failed recovery stays retryable even after the pending feed forgets an awaited request', async () => {
    activity.requests = [{ id: 'return-exact', state: 'processing' }];
    reloadReceipts.mockImplementationOnce(async () => { change({ requests: [] }); throw new Error('temporary'); });
    await mount(); await reconnect(); expect(connection.getState().recoveryError).toContain('temporary');
    await vi.advanceTimersByTimeAsync(4_999); expect(reloadReceipts).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1); expect(reloadReceipts.mock.calls[1][0]).toEqual(['return-exact']);
    expect(connection.getState().recoveryError).toBeNull();
  });

  it('an omitted exact outcome remains retryable and retries are bounded until the next reconnect', async () => {
    activity.requests = [{ id: 'missing', state: 'processing' }];
    reloadReceipts.mockImplementation(async () => { change({ requests: [] }); return new Set(); });
    await mount(); await reconnect(); await vi.advanceTimersByTimeAsync(155_000);
    expect(reloadReceipts).toHaveBeenCalledTimes(4);
    expect(reloadReceipts.mock.calls.every(([ids]) => ids.includes('missing'))).toBe(true);
    const calls = reloadReceipts.mock.calls.length; await vi.advanceTimersByTimeAsync(600_000);
    expect(reloadReceipts).toHaveBeenCalledTimes(calls); expect(connection.getState().recoveryError).not.toBeNull();
    reloadReceipts.mockImplementation(async (ids: readonly string[]) => new Set(ids));
    await reconnect(); expect(connection.getState().recoveryError).toBeNull();
  });

  it('failed job reloads retry even when receipt recovery succeeded', async () => {
    refreshJobs.mockRejectedValueOnce(new Error('jobs offline'));
    await mount(); await reconnect(); expect(connection.getState().recoveryError).toContain('jobs offline');
    await vi.advanceTimersByTimeAsync(5_000); expect(refreshJobs).toHaveBeenCalledTimes(2);
    expect(connection.getState().recoveryError).toBeNull();
  });

  it('the real jobs-client resolved error remains retryable, and explicit Refresh retries recovery', async () => {
    refreshJobs.mockImplementationOnce(async () => { snapshot.error = 'jobs reload failed'; });
    await mount(); await reconnect();
    expect(connection.getState().recoveryError).toContain('jobs reload failed');
    refreshJobs.mockImplementation(async () => { snapshot.error = null; });
    stop!.refresh(); await vi.advanceTimersByTimeAsync(0);
    expect(refreshJobs).toHaveBeenCalledTimes(2);
    expect(connection.getState().recoveryError).toBeNull();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(refreshJobs).toHaveBeenCalledTimes(2);
  });

  it('mounting after an already connected socket discovers pending work once', async () => {
    snapshot.connection = 'connected';
    activity.requests = [{ id: 'already-awaited', state: 'processing' }];
    await mount();
    expect(refreshJobs).toHaveBeenCalledTimes(1);
    expect(reloadReceipts).toHaveBeenCalledWith(['already-awaited']);
  });

  it('Onshape never starts Fusion observations, and switching applications fences held Fusion status', async () => {
    activity.cadApplication = 'onshape';
    await mount(); await vi.advanceTimersByTimeAsync(30_000); expect(reads()).toEqual([0, 0]);
    change({ cadApplication: 'fusion360' }); await vi.advanceTimersByTimeAsync(0);
    expect(connection.getState().fusionStatus?.state).toBe('current');
    change({ cadApplication: 'onshape' });
    expect(connection.getState().fusionStatus).toBeNull();
    const switched = reads(); await vi.advanceTimersByTimeAsync(30_000); expect(reads()).toEqual(switched);
  });

  it('a superseded reconnect cannot clear a newer recovery error or schedule its retry', async () => {
    let finish!: (ids: ReadonlySet<string>) => void;
    reloadReceipts.mockImplementationOnce(() => new Promise<ReadonlySet<string>>((resolve) => { finish = resolve; }));
    await mount(); await reconnect();
    reloadReceipts.mockRejectedValueOnce(new Error('new recovery failure')); await reconnect();
    finish(new Set()); await vi.advanceTimersByTimeAsync(0);
    expect(connection.getState().recoveryError).toContain('new recovery failure');
    await vi.advanceTimersByTimeAsync(5_000); expect(reloadReceipts).toHaveBeenCalledTimes(3);
  });

  it('teardown fences delayed status/recovery responses and removes all clocks and wake handlers', async () => {
    let finish!: (ids: ReadonlySet<string>) => void;
    reloadReceipts.mockImplementation(() => new Promise<ReadonlySet<string>>((resolve) => { finish = resolve; }));
    await mount(); await reconnect(); stop!(); stop = undefined;
    finish(new Set()); await vi.advanceTimersByTimeAsync(0);
    const stopped = reads(); window.dispatchEvent(new Event('focus')); await vi.advanceTimersByTimeAsync(600_000);
    expect(reads()).toEqual(stopped); expect(connection.getState().fusionStatus).toBeNull(); expect(vi.getTimerCount()).toBe(0);
  });
  it('on: moving jobs and coarse notifications do not hold the base rate or reset the quiet clock', async () => {
    activity.coordination = 'on'; activity.workspaceMode = 'parametric';
    readStatus.mockResolvedValue(status({ state: 'closed', processRunning: false, running: false }));
    snapshot.jobs = [cadJob()];
    await mount();
    for (let tick = 0; tick < 16; tick += 1) {
      activityChanged(); jobsChanged();
      await vi.advanceTimersByTimeAsync(2_500);
    }
    const quiet = reads();
    await vi.advanceTimersByTimeAsync(15_000);
    expect(reads()).toEqual(quiet);
    change({ awaitingReturn: true });
    await vi.advanceTimersByTimeAsync(2_500);
    expect(reads()[0]).toBe(quiet[0] + 1);
    change({ awaitingReturn: false });
    const settled = reads();
    await vi.advanceTimersByTimeAsync(15_000);
    expect(reads()).toEqual(settled);
  });

});
