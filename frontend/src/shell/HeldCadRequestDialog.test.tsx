import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { cadJobFixture, publishCadJobs } from '../jobs/cadSolve.fixtures';
import { cadJobSummary } from '../jobs/cadSolve';
import legacy from '../jobs/cadRefusedJob.fixture.json';
import { HeldCadRequestDialog, openHeldRequest, resetHeldRequestSessionForTests } from './HeldCadRequestDialog';
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

function held(id = 'held-request', heldAt = new Date().toISOString()): JobItem {
  return { ...cadJobFixture(cadJobSummary(legacy as unknown as JobItem)), ...legacy, id, status: 'held', stage: 'held', stage_message: 'Waiting for you', error_message: null,
    completed_at: heldAt,
    cad_state: { ...legacy.cad_state, held: true, reason: 'mesh_too_large', attention: 'question',
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
    resetHeldRequestSessionForTests(Date.now() - 1000);
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
  it('shows only the next step and the options, and continues through the existing solve owner', async () => {
    publishCadJobs([held()]);
    await act(async () => root.render(<HeldCadRequestDialog/>));
    expect(host.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    // The server's raw message and reason stay out of the pop-up.
    expect(host.textContent).not.toContain('This mesh is above the recommended size.');
    expect(host.textContent).not.toContain('mesh_too_large');
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
    await act(async () => root.render(<><HeldCadRequestDialog/><CurrentModelStatus record={record} solveStatus/></>));
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

  it('keeps the Waiting notice in Results only, never twice on the CAD Solve card', async () => {
    const job = held();
    publishCadJobs([job]);
    const record = { ingest_id: 'mesh', manifest_sha256: job.cad_state!.snapshot!.manifest_sha256 } as never;
    workspaceModeStore.setMode('cad');
    useCadReturnStore.setState({ ingestRecord: record, needsIngest: false });
    await act(async () => root.render(<CurrentModelStatus record={record}/>));
    expect(host.querySelector('.current-model-status')).not.toBeNull();
    expect(host.querySelector('.notice')).toBeNull();
    expect([...host.querySelectorAll('button')].some(node => node.textContent === 'Review request')).toBe(false);
  });

  it('opens by itself only for a request held in this session; Later holds across windows', async () => {
    const yesterday = new Date(Date.now() - 86_400_000).toISOString();
    publishCadJobs([held('old', yesterday)]);
    await act(async () => root.render(<HeldCadRequestDialog/>));
    // An earlier day's request stays behind the chip and Results.
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    act(() => openHeldRequest('old'));
    expect(host.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    act(() => button('Later').click());
    act(() => publishCadJobs([held('old', yesterday), held('new')]));
    expect(host.querySelector('[role="dialog"] p')?.textContent).toContain(held().cad_state!.snapshot!.document_name!);
    act(() => button('Later').click());
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    // A new window remembers Later for that request.
    act(() => root.unmount());
    root = createRoot(host);
    await act(async () => root.render(<HeldCadRequestDialog/>));
    expect(host.querySelector('[role="dialog"]')).toBeNull();
  });

  it('tells an internal failure calmly in Results, with Try again, never as a hold', async () => {
    const calm = 'WG could not prepare Speaker. This has been recorded for the developers.';
    const failed = { ...held('failed'), status: 'error', stage: 'error', error_message: calm,
      cad_state: { ...held().cad_state!, state: 'needs_user_input', held: false, prepare_failed: true, reason: 'preparation_failed',
        message: calm, next_step: null, options: [{ id: 'retry', label: 'Try again' }, { id: 'dismiss', label: 'Dismiss request' }] } } as JobItem;
    publishCadJobs([failed]);
    const record = { ingest_id: 'mesh', manifest_sha256: failed.cad_state!.snapshot!.manifest_sha256 } as never;
    workspaceModeStore.setMode('cad');
    useCadReturnStore.setState({ ingestRecord: record, needsIngest: false });
    await act(async () => root.render(<><HeldCadRequestDialog/><CurrentModelStatus record={record} actions solveStatus/></>));
    const status = host.querySelector('[data-model-status="prepare-failed"]')!;
    expect(status.textContent).toContain(calm);
    expect(status.querySelector('.notice-error')).toBeNull();
    expect(host.textContent).not.toContain('Waiting for you');
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    await act(async () => button('Try again').click());
    expect(actions.continueRun).toHaveBeenCalledWith('failed');
  });

  it('never pops up for an engine gate, a frame changed elsewhere or a failed preparation', async () => {
    const engine = held('engine');
    engine.cad_state = { ...engine.cad_state!, reason: 'engine_unavailable', attention: 'action', options: [{ id: 'review', label: 'Review solve' }] };
    // A manual axis changed elsewhere is held too, but asks nothing.
    const frame = held('frame');
    frame.cad_state = { ...frame.cad_state!, reason: 'frame_confirmation_required', attention: 'action' };
    const failed = { ...held('failed'), status: 'error', cad_state: { ...held().cad_state!, held: false, prepare_failed: true,
      reason: 'preparation_failed', message: 'WG could not prepare Speaker. This has been recorded for the developers.' } } as JobItem;
    publishCadJobs([engine, frame, failed]);
    await act(async () => root.render(<HeldCadRequestDialog/>));
    expect(host.querySelector('[role="dialog"]')).toBeNull();
  });

  const ceilingBundle = (bundlePath: string) => ({ bundlePath, name: bundlePath, documentName: 'NoWaistbanding',
    readable: true, modifiedAt: '2026-10-24T10:00:00Z' }) as unknown as NonNullable<ReturnType<typeof useCadReturnStore.getState>['selectedBundle']>;
  const NEXT = 'Change the mesh resolution in Sources & Mesh… in Fusion, or check the model size.';

  it('asks Prepare anyway after Prepare simulation, and the prepared mesh carries the choice', async () => {
    const { meshCeilingChoice } = await import('../stores/meshCeilingChoice');
    const { cadWorkspace } = await import('../stores/cadWorkspace');
    const ingest = vi.fn().mockResolvedValue(undefined);
    const prepareAndSolve = vi.fn().mockResolvedValue('solving');
    const previous = cadWorkspace.getState();
    cadWorkspace.setState({ ...previous, ingest, prepareAndSolve });
    useCadReturnStore.setState({ selectedBundle: ceilingBundle('b') });
    await act(async () => root.render(<HeldCadRequestDialog/>));
    act(() => meshCeilingChoice.ask({ name: 'NoWaistbanding', nextStep: NEXT, bundlePath: 'b' }));
    const dialog = host.querySelector('[role="dialog"]');
    expect(dialog?.textContent).toContain('NoWaistbanding');
    expect(dialog?.textContent).toContain('You can also prepare it anyway.');
    expect(button('Solve anyway')).toBeUndefined();
    await act(async () => button('Prepare anyway').click());
    expect(ingest).toHaveBeenCalledWith({ meshLimitOverride: true });
    expect(prepareAndSolve).not.toHaveBeenCalled();
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    cadWorkspace.setState(previous, true);
    useCadReturnStore.setState({ selectedBundle: null });
  });

  it('asks Solve anyway after a Solve, and prepares and solves in one press', async () => {
    const { meshCeilingChoice } = await import('../stores/meshCeilingChoice');
    const { cadWorkspace } = await import('../stores/cadWorkspace');
    const ingest = vi.fn().mockResolvedValue(undefined);
    const prepareAndSolve = vi.fn().mockResolvedValue('solving');
    const previous = cadWorkspace.getState();
    cadWorkspace.setState({ ...previous, ingest, prepareAndSolve });
    useCadReturnStore.setState({ selectedBundle: ceilingBundle('b') });
    await act(async () => root.render(<HeldCadRequestDialog/>));
    act(() => { meshCeilingChoice.ask({ name: 'NoWaistbanding', nextStep: NEXT, bundlePath: 'b' }); meshCeilingChoice.solveAfter('b'); });
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain('You can also solve it anyway.');
    await act(async () => button('Solve anyway').click());
    expect(prepareAndSolve).toHaveBeenCalledWith({ meshLimitOverride: true });
    expect(ingest).not.toHaveBeenCalled();
    expect(meshCeilingChoice.getSnapshot()).toBeNull();
    cadWorkspace.setState(previous, true);
    useCadReturnStore.setState({ selectedBundle: null });
  });

  it('never applies the ceiling choice to another selected model', async () => {
    const { meshCeilingChoice } = await import('../stores/meshCeilingChoice');
    const { cadWorkspace } = await import('../stores/cadWorkspace');
    const ingest = vi.fn().mockResolvedValue(undefined);
    const previous = cadWorkspace.getState();
    cadWorkspace.setState({ ...previous, ingest });
    useCadReturnStore.setState({ selectedBundle: ceilingBundle('b') });
    await act(async () => root.render(<HeldCadRequestDialog/>));
    act(() => meshCeilingChoice.ask({ name: 'NoWaistbanding', nextStep: NEXT, bundlePath: 'b' }));
    expect(host.querySelector('[role="dialog"]')).not.toBeNull();
    // A background owner (a Fusion takeover, a folder arrival) selects another model.
    act(() => useCadReturnStore.setState({ selectedBundle: ceilingBundle('c') }));
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(ingest).not.toHaveBeenCalled();
    act(() => meshCeilingChoice.clear());
    cadWorkspace.setState(previous, true);
    useCadReturnStore.setState({ selectedBundle: null });
  });

  it('keeps the model waiting for its mesh-size choice after Later, and Choose asks again', async () => {
    const { meshCeilingChoice } = await import('../stores/meshCeilingChoice');
    useCadReturnStore.setState({ selectedBundle: ceilingBundle('b'), ingestRecord: null });
    workspaceModeStore.setMode('cad');
    await act(async () => root.render(<><HeldCadRequestDialog/><CurrentModelStatus solveStatus/></>));
    act(() => meshCeilingChoice.ask({ name: 'NoWaistbanding', nextStep: NEXT, bundlePath: 'b' }));
    await act(async () => button('Later').click());
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(meshCeilingChoice.getSnapshot()?.deferred).toBe(true);
    const status = host.querySelector('[data-model-status="waiting"]')!;
    expect(status.textContent).toContain('NoWaistbanding is waiting for your mesh-size choice.');
    await act(async () => button('Choose').click());
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain('NoWaistbanding');
    act(() => meshCeilingChoice.clear());
    useCadReturnStore.setState({ selectedBundle: null });
  });
});
