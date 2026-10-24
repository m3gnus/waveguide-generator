import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { cadJobFixture, publishCadJobs } from '../jobs/cadSolve.fixtures';
import { cadJobSummary } from '../jobs/cadSolve';
import legacy from '../jobs/cadRefusedJob.fixture.json';
import { HeldCadRequestDialog, openHeldRequest } from './HeldCadRequestDialog';
import { CurrentModelStatus } from './CurrentModelStatus';
import { workspaceNavigation } from './workspaceNavigation';
import { useCadReturnStore } from '../stores/cadReturn';
import { workspaceModeStore } from '../stores/workspaceMode';

const actions = vi.hoisted(() => ({ continueRun: vi.fn(), dismiss: vi.fn() }));
const coordinator = vi.hoisted(() => ({ continueRun: (...args: unknown[]) => actions.continueRun(...args) }));
vi.mock('./JobsCoordinator', () => ({
  jobsCoordinatorBridge: { getSnapshot: () => coordinator, subscribe: () => () => undefined },
  useOptionalSolveControl: () => null,
}));
vi.mock('../jobs/cadSolve', async (load) => ({
  ...await load<typeof import('../jobs/cadSolve')>(), dismissCadJob: actions.dismiss,
}));

function held(id = 'held-request'): JobItem {
  return { ...cadJobFixture(cadJobSummary(legacy as unknown as JobItem)), ...legacy, id, status: 'held', stage: 'held', stage_message: 'Waiting for you', error_message: null,
    cad_state: { ...legacy.cad_state, held: true, reason: 'mesh_too_large',
      message: 'This mesh is above the recommended size.',
      next_step: 'Change resolution in Sources & Mesh… or check the model size.',
      options: [{ id: 'solve_anyway', label: 'Solve anyway' }, { id: 'dismiss', label: 'Dismiss request' }] },
  } as JobItem;
}

describe('held CAD request dialog owner', () => {
  let host: HTMLDivElement;
  let root: Root;
  const button = (text: string) => [...host.querySelectorAll('button')].find(node => node.textContent === text)!;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    actions.continueRun.mockReset().mockResolvedValue('child');
    actions.dismiss.mockReset().mockResolvedValue(undefined);
    vi.spyOn(jobsSocket, 'refresh').mockResolvedValue(undefined);
    publishCadJobs([]);
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });
  afterEach(() => {
    act(() => root.unmount());
    useCadReturnStore.setState({ ingestRecord: null, needsIngest: true });
    workspaceModeStore.setMode('parametric');
    host.remove();
    vi.restoreAllMocks();
  });
  it('shows the server message and options, and continues through the existing solve owner', async () => {
    publishCadJobs([held()]);
    await act(async () => root.render(<HeldCadRequestDialog/>));
    expect(host.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    expect(host.textContent).toContain('This mesh is above the recommended size.');
    expect(host.textContent).toContain('Sources & Mesh…');
    await act(async () => { button('Solve anyway').click(); });
    expect(actions.continueRun).toHaveBeenCalledWith('held-request', { mesh_limit_override: true });
    expect(host.querySelector('[role="dialog"]')).toBeNull();
  });
  it('shows one request at a time and Later keeps it available for the toolbar', async () => {
    publishCadJobs([held('one'), held('two')]);
    await act(async () => root.render(<HeldCadRequestDialog/>));
    act(() => button('Later').click());
    expect(host.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    act(() => button('Later').click());
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    act(() => openHeldRequest('one'));
    expect(host.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    await act(async () => button('Dismiss request').click());
    expect(actions.dismiss).toHaveBeenCalledWith(expect.objectContaining({ id: 'one' }));
  });
  it('retires a superseded popup and ignores genuine errors without a refusal', async () => {
    const job = held();
    publishCadJobs([job]);
    await act(async () => root.render(<HeldCadRequestDialog/>));
    act(() => publishCadJobs([{ ...job, status: 'cancelled', cad_state: { ...job.cad_state!, held: false, state: 'cancelled', reason: 'superseded' } }]));
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    act(() => publishCadJobs([{ ...job, status: 'error', cad_state: { ...job.cad_state!, held: false, reason: null } }]));
    expect(host.querySelector('[role="dialog"]')).toBeNull();
  });
  it('uses a calm Results status and reopens the same choice', async () => {
    const job = held();
    publishCadJobs([job]);
    // The held request belongs to the selected CAD model, whose owner presents it as waiting.
    const record = { ingest_id: 'mesh', manifest_sha256: job.cad_state!.snapshot!.manifest_sha256 } as never;
    workspaceModeStore.setMode('cad');
    useCadReturnStore.setState({ ingestRecord: record, needsIngest: false });
    await act(async () => root.render(<><HeldCadRequestDialog/><CurrentModelStatus record={record}/></>));
    const status = host.querySelector('[data-model-status="waiting"]');
    expect(status?.textContent).toContain('Waiting for you');
    expect(status?.querySelector('.notice-error')).toBeNull();
    act(() => button('Later').click());
    act(() => button('Review request').click());
    expect(host.querySelectorAll('[role="dialog"]')).toHaveLength(1);
  });
  it('routes a review choice through the existing request view', async () => {
    const job = held();
    job.cad_state!.options = [{ id: 'review', label: 'Review settings' }];
    const reveal = vi.spyOn(workspaceNavigation, 'reveal').mockReturnValue(true);
    publishCadJobs([job]);
    await act(async () => root.render(<HeldCadRequestDialog/>));
    await act(async () => button('Review settings').click());
    expect(reveal).toHaveBeenCalledWith('cad-requests');
    expect(actions.continueRun).not.toHaveBeenCalled();
  });
  it('keeps the choice after a failed action and suppresses repeated presses', async () => {
    let fail: (error: Error) => void = () => undefined;
    actions.continueRun.mockImplementation(() => new Promise((_, reject) => { fail = reject; }));
    publishCadJobs([held()]);
    await act(async () => root.render(<HeldCadRequestDialog/>));
    act(() => { button('Solve anyway').click(); button('Solve anyway').click(); });
    expect(actions.continueRun).toHaveBeenCalledTimes(1);
    await act(async () => fail(new Error('internal details')));
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('Please try again');
    expect(host.textContent).not.toContain('internal details');
    expect(button('Solve anyway').disabled).toBe(false);
  });
});
