import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { cadJobFixture, publishCadJobs } from '../jobs/cadSolve.fixtures';
import { cadJobSummary } from '../jobs/cadSolve';
import legacy from '../jobs/cadRefusedJob.fixture.json';
import { HeldCadRequestDialog, openHeldRequest, resetHeldRequestSessionForTests } from './HeldCadRequestDialog';
import { CurrentModelStatus } from './CurrentModelStatus';
import { CadModelStatusLine } from './CadModelStatusLine';
import { workspaceNavigation } from './workspaceNavigation';
import { useCadReturnStore } from '../stores/cadReturn';
import { workspaceModeStore } from '../stores/workspaceMode';

const actions = vi.hoisted(() => ({ continueRun: vi.fn(), dismiss: vi.fn() }));
const coordinator = vi.hoisted(() => ({ continueRun: (...args: unknown[]) => actions.continueRun(...args) }));
type SolveControl = NonNullable<ReturnType<typeof import('./JobsCoordinator').useOptionalSolveControl>>;
/** The one Solve owner, when a test puts one on screen. */
const solveControl = vi.hoisted(() => ({ current: null as null | (Partial<SolveControl> & Pick<SolveControl, 'importedGeometryActive'>) }));
vi.mock('./JobsCoordinator', () => ({
  jobsCoordinatorBridge: { getSnapshot: () => coordinator, subscribe: () => () => undefined },
  useOptionalSolveControl: () => solveControl.current,
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
    solveControl.current = null;
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
  it('states the ceiling\'s facts from the job, with Solve anyway, Change in Fusion and Later, and no axis question', async () => {
    const job = held();
    job.cad_state = { ...job.cad_state!, mesh_limit: { triangles: 54_739, limit: 22_000 } };
    publishCadJobs([job]);
    await act(async () => root.render(<HeldCadRequestDialog/>));
    const dialog = host.querySelector('[role="dialog"]')!;
    const name = job.cad_state!.snapshot!.document_name!;
    expect(dialog.querySelector('h2')?.textContent).toBe(`${name} is larger than Waveguide Generator's limit`);
    expect(dialog.textContent).toContain('It meshes to 54,739 triangles; Waveguide Generator\'s limit is 22,000. Solving it anyway is slower and needs more memory.');
    expect([...dialog.querySelectorAll('button')].map(node => node.textContent)).toEqual(['Later', "Don't solve", 'Change in Fusion', 'Solve anyway']);
    // Don't solve lets the request go.
    await act(async () => button("Don't solve").click());
    expect(actions.dismiss).toHaveBeenCalledWith(expect.objectContaining({ id: job.id }));
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(dialog.textContent).not.toMatch(/axis|radiat/i);
    expect(dialog.textContent).not.toContain('Dismiss');
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
    // Change in Fusion says where: Fusion's own panel, whose Sources & Mesh…
    // button stays while the request waits -- so it is kept, not dismissed.
    act(() => button('Change in Fusion').click());
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain('panel shows this request with a Sources & Mesh… button');
    await act(async () => button('OK').click());
    expect(actions.dismiss).not.toHaveBeenCalled();
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(jobsSocket.getSnapshot().jobs.some(item => item.id === 'one')).toBe(true);
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
    await act(async () => root.render(<><HeldCadRequestDialog/><CadModelStatusLine/><CurrentModelStatus record={record} solveStatus/></>));
    // Results states the model's line; its one action, Choose…, is the line's.
    const status = host.querySelector('[data-model-status="waiting"]');
    expect(status?.textContent).toContain('needs your choice');
    expect(status?.querySelector('.notice-error')).toBeNull();
    expect(status?.querySelector('.current-model-status-line button')).toBeNull();
    act(() => button('Later').click());
    act(() => button('Choose…').click());
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
    expect(host.querySelector('[role="dialog"] h2')?.textContent).toContain(held().cad_state!.snapshot!.document_name!);
    act(() => button('Later').click());
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    // A new window remembers Later for that request.
    act(() => root.unmount());
    root = createRoot(host);
    await act(async () => root.render(<HeldCadRequestDialog/>));
    expect(host.querySelector('[role="dialog"]')).toBeNull();
  });

  it('tells an internal failure calmly in Results, with Try again, never as a hold', async () => {
    const calm = 'Waveguide Generator could not prepare Speaker. This has been recorded for the developers.';
    const failed = { ...held('failed'), status: 'error', stage: 'error', error_message: calm,
      cad_state: { ...held().cad_state!, state: 'needs_user_input', held: false, prepare_failed: true, reason: 'preparation_failed',
        message: calm, next_step: null, options: [{ id: 'retry', label: 'Try again' }, { id: 'dismiss', label: 'Dismiss request' }] } } as JobItem;
    publishCadJobs([failed]);
    const record = { ingest_id: 'mesh', manifest_sha256: failed.cad_state!.snapshot!.manifest_sha256 } as never;
    workspaceModeStore.setMode('cad');
    useCadReturnStore.setState({ ingestRecord: record, needsIngest: false });
    await act(async () => root.render(<><HeldCadRequestDialog/><CadModelStatusLine/><CurrentModelStatus record={record} actions solveStatus/></>));
    const status = host.querySelector('[data-model-status="prepare-failed"]')!;
    expect(status.textContent).toContain(calm);
    expect(status.querySelector('.notice-error')).toBeNull();
    expect(status.textContent).not.toContain('Try again');
    expect(host.textContent).not.toContain('needs your choice');
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    // Try again is the status line's one action.
    await act(async () => button('Try again').click());
    expect(actions.continueRun).toHaveBeenCalledWith('failed');
  });

  it('shows a prepared request as Ready to solve in Results, with Solve, never as a question', async () => {
    const solve = vi.fn().mockResolvedValue('submitted');
    solveControl.current = { solve, disabled: false, title: 'Solve', cadMode: true, importedGeometryActive: true, submitting: false,
      readiness: null, readinessRequested: false, waitingForEngine: false, actionError: null, preparationError: null };
    const ready = { ...held('ready'), stage_message: 'Ready to solve',
      cad_state: { ...held().cad_state!, held: true, reason: 'ready_to_solve', attention: 'ready',
        message: 'Prepared. Press Solve to start it.', next_step: 'Start the prepared solve when you are ready.',
        options: [{ id: 'retry', label: 'Solve' }, { id: 'dismiss', label: 'Dismiss request' }] } } as JobItem;
    publishCadJobs([ready]);
    const record = { ingest_id: 'mesh', manifest_sha256: ready.cad_state!.snapshot!.manifest_sha256 } as never;
    workspaceModeStore.setMode('cad');
    useCadReturnStore.setState({ ingestRecord: record, needsIngest: false });
    await act(async () => root.render(<><HeldCadRequestDialog/><CadModelStatusLine/><CurrentModelStatus record={record} actions solveStatus/></>));
    const status = host.querySelector('[data-model-status="waiting"]')!;
    expect(status.textContent).toContain('Ready to solve');
    expect(status.querySelector('button')).toBeNull();
    expect(status.querySelector('.notice-error')).toBeNull();
    expect(host.textContent).not.toContain('needs your choice');
    expect(host.textContent).not.toContain('refused');
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    // One Solve, the status line's, never a second competing one in Results.
    expect([...host.querySelectorAll('button')].filter(node => node.textContent === 'Solve')).toHaveLength(1);
    expect(button('Solve').disabled).toBe(false);
    await act(async () => button('Solve').click());
    // The one Solve owner solves the settings and frame on screen; the server
    // resumes this request's preparation when they are unchanged.
    expect(solve).toHaveBeenCalledOnce();
    expect(actions.continueRun).not.toHaveBeenCalled();
  });

  it.each([
    ['an engine warming for the press', { waitingForEngine: true, readinessRequested: true }],
    ['a failed press', { actionError: 'Solve refused: choose settings.' }],
  ])('shows %s first, never a competing Ready to solve', async (_name, state) => {
    const solve = vi.fn().mockResolvedValue('submitted');
    solveControl.current = { solve, disabled: false, title: 'Solve', cadMode: true, importedGeometryActive: true, submitting: false,
      readiness: null, readinessRequested: false, waitingForEngine: false, waitingEngine: 'beat-metal', actionError: null, preparationError: null,
      stopWaiting: vi.fn(), ...state };
    const ready = { ...held('ready'), stage_message: 'Ready to solve',
      cad_state: { ...held().cad_state!, held: true, reason: 'ready_to_solve', attention: 'ready',
        message: 'Prepared. Press Solve to start it.', next_step: 'Start the prepared solve when you are ready.',
        options: [{ id: 'retry', label: 'Solve' }] } } as JobItem;
    publishCadJobs([ready]);
    const record = { ingest_id: 'mesh', manifest_sha256: ready.cad_state!.snapshot!.manifest_sha256 } as never;
    workspaceModeStore.setMode('cad');
    useCadReturnStore.setState({ ingestRecord: record, needsIngest: false });
    await act(async () => root.render(<CurrentModelStatus record={record} actions solveStatus/>));
    expect(host.querySelector('[data-model-status="ready"]')).toBeNull();
    expect(host.textContent).not.toContain('Ready to solve');
    if ('waitingForEngine' in state) {
      expect(host.textContent).toContain('is warming up');
      expect([...host.querySelectorAll('button')].filter(node => node.textContent === 'Solve')).toHaveLength(0);
    } else {
      expect(host.querySelector('[role="alert"]')?.textContent).toBe('Solve refused: choose settings.');
      // Solve is the top bar's and the status line's, never Results'.
      expect([...host.querySelectorAll('button')].filter(node => node.textContent === 'Solve')).toHaveLength(0);
    }
  });

  it('keeps an engine that cannot solve the return in its readiness view, never "Needs a change"', async () => {
    solveControl.current = { solve: vi.fn(), disabled: false, title: 'Solve', cadMode: true, importedGeometryActive: true, submitting: false,
      readiness: null, readinessRequested: false, waitingForEngine: false, actionError: null, preparationError: null };
    const engine = { ...held('engine'), stage_message: 'Needs a change',
      cad_state: { ...held().cad_state!, held: true, reason: 'engine_cannot_solve_return', attention: 'action',
        message: 'BEAT · CUDA solves CAD returns only in Accurate. Choose Accurate, or Metal / AUTO.',
        options: [{ id: 'dismiss', label: 'Dismiss request' }] } } as JobItem;
    publishCadJobs([engine]);
    const record = { ingest_id: 'mesh', manifest_sha256: engine.cad_state!.snapshot!.manifest_sha256 } as never;
    workspaceModeStore.setMode('cad');
    useCadReturnStore.setState({ ingestRecord: record, needsIngest: false });
    await act(async () => root.render(<CurrentModelStatus record={record} actions solveStatus/>));
    expect(host.querySelector('[data-model-status="change"]')).toBeNull();
    expect(host.textContent).not.toContain('Needs a change');
    expect(host.textContent).toContain('Choose Accurate, or Metal / AUTO.');
  });

  it('never pops up for an engine gate, a frame changed elsewhere or a failed preparation', async () => {
    const engine = held('engine');
    engine.cad_state = { ...engine.cad_state!, reason: 'engine_unavailable', attention: 'action', options: [{ id: 'review', label: 'Review solve' }] };
    // A manual axis changed elsewhere is held too, but asks nothing.
    const frame = held('frame');
    frame.cad_state = { ...frame.cad_state!, reason: 'frame_confirmation_required', attention: 'action' };
    const failed = { ...held('failed'), status: 'error', cad_state: { ...held().cad_state!, held: false, prepare_failed: true,
      reason: 'preparation_failed', message: 'Waveguide Generator could not prepare Speaker. This has been recorded for the developers.' } } as JobItem;
    publishCadJobs([engine, frame, failed]);
    await act(async () => root.render(<HeldCadRequestDialog/>));
    expect(host.querySelector('[role="dialog"]')).toBeNull();
  });

  const ceilingBundle = (bundlePath: string) => ({ bundlePath, name: bundlePath, documentName: 'NoWaistbanding',
    readable: true, modifiedAt: '2026-10-24T10:00:00Z' }) as unknown as NonNullable<ReturnType<typeof useCadReturnStore.getState>['selectedBundle']>;
  const NEXT = 'Change the mesh resolution in Sources & Mesh… in Fusion, or check the model size.';

  it('asks Solve anyway, however the preparation started, and prepares and solves in one press', async () => {
    const { meshCeilingChoice } = await import('../stores/meshCeilingChoice');
    const { cadWorkspace } = await import('../stores/cadWorkspace');
    const ingest = vi.fn().mockResolvedValue(undefined);
    const prepareAndSolve = vi.fn().mockResolvedValue('solving');
    const previous = cadWorkspace.getState();
    cadWorkspace.setState({ ...previous, ingest, prepareAndSolve });
    useCadReturnStore.setState({ selectedBundle: ceilingBundle('b') });
    await act(async () => root.render(<HeldCadRequestDialog/>));
    act(() => meshCeilingChoice.ask({ name: 'NoWaistbanding', nextStep: NEXT, bundlePath: 'b' }));
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain('You can also solve it anyway.');
    await act(async () => button('Solve anyway').click());
    expect(prepareAndSolve).toHaveBeenCalledWith({ meshLimitOverride: true });
    expect(ingest).not.toHaveBeenCalled();
    expect(meshCeilingChoice.getSnapshot()).toBeNull();
    cadWorkspace.setState(previous, true);
    useCadReturnStore.setState({ selectedBundle: null });
  });

  it('states the browser preparation\'s ceiling facts, and Change in Fusion lets the choice go', async () => {
    const { meshCeilingChoice } = await import('../stores/meshCeilingChoice');
    useCadReturnStore.setState({ selectedBundle: ceilingBundle('b'), ingestRecord: null });
    workspaceModeStore.setMode('cad');
    await act(async () => root.render(<><HeldCadRequestDialog/><CadModelStatusLine/></>));
    act(() => meshCeilingChoice.ask({ name: 'NoWaistbanding', nextStep: NEXT, bundlePath: 'b', meshLimit: { triangles: 54_739, limit: 22_000 } }));
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain('It meshes to 54,739 triangles; Waveguide Generator\'s limit is 22,000.');
    expect(host.querySelector('[role="status"]')?.textContent).toContain('NoWaistbanding needs your choice: it meshes to 54,739 triangles (limit 22,000).');
    act(() => button('Change in Fusion').click());
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain('In Fusion, open Waveguide Generator ▸ Sources & Mesh…');
    act(() => button('Back').click());
    expect(button('Solve anyway')).toBeDefined();
    act(() => button('Change in Fusion').click());
    await act(async () => button('OK').click());
    expect(meshCeilingChoice.getSnapshot()).toBeNull();
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    useCadReturnStore.setState({ selectedBundle: null });
  });

  it('lets this window\'s own ceiling question go with Don\'t solve', async () => {
    const { meshCeilingChoice } = await import('../stores/meshCeilingChoice');
    useCadReturnStore.setState({ selectedBundle: ceilingBundle('b'), ingestRecord: null });
    await act(async () => root.render(<HeldCadRequestDialog/>));
    act(() => meshCeilingChoice.ask({ name: 'NoWaistbanding', nextStep: NEXT, bundlePath: 'b' }));
    await act(async () => button("Don't solve").click());
    expect(meshCeilingChoice.getSnapshot()).toBeNull();
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    useCadReturnStore.setState({ selectedBundle: null });
  });

  it('says the held request\'s facts on the model\'s status line before WG has a mesh of it', async () => {
    const job = { ...held(), cad_return: { bundle_path: 'wgreturn/b.wgreturn', return_id: 'wgr_b' } } as JobItem;
    job.cad_state = { ...job.cad_state!, mesh_limit: { triangles: 54_739, limit: 22_000 } };
    publishCadJobs([job]);
    useCadReturnStore.setState({ selectedBundle: { ...ceilingBundle('wgreturn/b.wgreturn'), returnId: 'wgr_b', requestId: null }, ingestRecord: null });
    workspaceModeStore.setMode('cad');
    await act(async () => root.render(<CadModelStatusLine/>));
    expect(host.querySelector('[role="status"]')?.textContent).toContain('NoWaistbanding needs your choice: it meshes to 54,739 triangles (limit 22,000).');
    expect(button('Choose…')).toBeDefined();
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
    await act(async () => root.render(<><HeldCadRequestDialog/><CadModelStatusLine/><CurrentModelStatus solveStatus/></>));
    act(() => meshCeilingChoice.ask({ name: 'NoWaistbanding', nextStep: NEXT, bundlePath: 'b' }));
    await act(async () => button('Later').click());
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(meshCeilingChoice.getSnapshot()?.deferred).toBe(true);
    const status = host.querySelector('[data-model-status="never-solved"]')!;
    expect(status.textContent).toContain('NoWaistbanding needs your choice: it is larger than WG\'s limit.');
    await act(async () => button('Choose…').click());
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain('NoWaistbanding');
    act(() => meshCeilingChoice.clear());
    useCadReturnStore.setState({ selectedBundle: null });
  });
});
