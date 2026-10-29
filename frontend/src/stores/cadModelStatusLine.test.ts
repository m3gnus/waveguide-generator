import { expectNoJargon } from '../testing/expectNoJargon';
import { describe, expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { currentModelStatus, type CurrentModel } from '../model/currentModel';
import { bundle, record } from '../shell/cadlink/selection.fixtures';
import states from '../../../server/tests/fixtures/cad_request_states.json';
import { selectCadModelState, type CadModelRequest } from './cadModelState';
import { cadModelStatusLine, cadRequestState, cadRequestsChipLabel, isQuietStatusLine } from './cadModelStatusLine';
import type { MeshCeilingChoice } from './meshCeilingChoice';
import { cadJobFixture } from '../jobs/cadSolve.fixtures';
import type { CadOperationSummary } from '../api/cadOperations';
import { cadPreparationKey, LISTING_GONE_REASON } from './cadReturn';

const MANIFEST = `sha256:${'c'.repeat(64)}`;
function operation(jobId: string, state: string, overrides: Partial<CadOperationSummary> = {}): CadOperationSummary {
  return { operationId: `op-${jobId}`, kind: 'prepare_and_solve', state, stage: 'submitted', reason: null, message: null,
    jobId, attemptGeneration: 1, setupRevisionId: null, preparationId: null,
    snapshot: { manifestSha256: MANIFEST, documentName: 'Speaker' }, legacy: false,
    createdAt: '2026-10-25T10:00:00Z', updatedAt: '2026-10-25T10:00:00Z', ...overrides } as CadOperationSummary;
}

const idle = { ingesting: false, ingestError: null };
const prepared = { ...record, mesh: { stats: { triangle_count: 3035 } } } as typeof record;
const selected = { selectedBundle: bundle, ingestRecord: null, needsIngest: true, projectLineageId: null };
const onScreen = { ...selected, ingestRecord: prepared, needsIngest: false };
/** A Fusion request selected before WG has a mesh of it. */
const pointer = { ...selected, selectedBundle: { ...bundle, readable: false, requestId: 'job-1' } };

/** The request for the model on screen, as `currentModelStatus` matches it. */
function requestFor(job: JobItem): CadModelRequest {
  const model: CurrentModel = { kind: 'cad', key: null, lineage: null, label: 'Speaker', viewportShowing: false, requestId: job.id };
  return currentModelStatus(model, [job]);
}

/** Words a user never reads (server/cadlink/wglink_protocol.py checks the same sentences). */
const JARGON = ['$.', 'bundle', 'validation', 'wgi_', 'sha256', 'ingest', 'manifest', 'snapshot', 'BEM', 'cut plane',
  'physical group', 'Prepare simulation', 'CAD Link setup', 'needs preparation', 'Not prepared yet', 'No CAD geometry'];

interface Row {
  name: string;
  model: 'prepared' | 'pointer';
  job: Record<string, unknown>;
  request_status: string;
  wg: { action: string; text: string };
}

describe('the CAD request states shared with Fusion', () => {
  it.each((states.rows as Row[]).map(row => [row.name, row] as const))('%s', (_name, row) => {
    const job = { created_at: '2026-10-25T10:00:00Z', queued_at: '2026-10-25T10:00:00Z', ...row.job } as unknown as JobItem;
    // The same class Fusion's request-status file says for this job.
    expect(cadRequestState(job)).toBe(row.request_status);
    const request = requestFor(job);
    const model = selectCadModelState(row.model === 'prepared' ? onScreen : pointer, idle, request);
    const line = cadModelStatusLine(model, request);
    expect(line.action).toBe(row.wg.action);
    expect(line.text).toBe(row.wg.text);
    expectNoJargon(`${line.text} ${line.short}`, { forbidden: JARGON });
  });
});

describe('the CAD model status line', () => {
  const line = (state: Parameters<typeof selectCadModelState>[0], preparation: Parameters<typeof selectCadModelState>[1] = idle,
    request: CadModelRequest | null = null, ceiling: MeshCeilingChoice | null = null, context = {}) =>
    cadModelStatusLine(selectCadModelState(state, preparation, request, null, ceiling), request, context);

  it('says how to get a model when none is selected', () => {
    const none = line({ ...selected, selectedBundle: null });
    expect(none).toMatchObject({ text: 'No CAD model yet. In Fusion, use Waveguide Generator ▸ Send or Solve.', action: 'none', short: 'none selected' });
    expect(line({ ...selected, selectedBundle: null }, idle, null, null, { application: 'Onshape' }).text).toContain('In Onshape');
  });

  it('prepares, then is ready to solve, with one Solve', () => {
    expect(line(selected, { ...idle, ingesting: true })).toMatchObject({ text: 'Preparing Speaker…', action: 'none', tone: 'busy' });
    const ready = line(onScreen);
    expect(ready).toMatchObject({ text: 'Ready to solve · 3,035 triangles', short: 'ready · 3,035 triangles', action: 'solve', actionLabel: 'Solve' });
    expectNoJargon(`${ready.text} ${ready.short}`, { forbidden: JARGON });
  });

  it('prepares selected and changed models automatically without a Prepare action', () => {
    for (const current of [line(selected), line({ ...onScreen, needsIngest: true })]) {
      expectNoJargon(`${current.text} ${current.short}`, { forbidden: JARGON });
    }
    expect(line(selected)).toMatchObject({ text: 'Preparing Speaker…', action: 'none' });
    expect(line(pointer)).toMatchObject({ action: 'none' });
    expect(line({ ...onScreen, needsIngest: true })).toMatchObject({ text: 'Preparing Speaker…', action: 'none', actionLabel: null });
  });

  it.each([false, true])('keeps honest wording when automatic preparation will not run (stale: %s)', stale => {
    const state = stale ? { ...onScreen, needsIngest: true } : selected;
    const expected = { text: stale ? 'Speaker changed since it was prepared.' : 'Speaker is not prepared yet.',
      tone: 'attention', action: 'none', actionLabel: null };
    for (const stopped of [
      { ...state, selectedBundle: { ...bundle, bundleOrigin: 'onshape' as const } },
      { ...state, ingestStaleReason: LISTING_GONE_REASON },
      { ...state, attemptedPreparationKey: cadPreparationKey(state, null) },
    ]) {
      const result = line(stopped);
      expect(result).toMatchObject(expected);
      expectNoJargon(`${result.text} ${result.short}`, { forbidden: JARGON });
      // Real in-flight work still says Preparing, regardless of eligibility.
      expect(line(stopped, { ...idle, ingesting: true })).toMatchObject({ text: 'Preparing Speaker…', tone: 'busy' });
    }
  });

  it('does not promise automatic preparation while Fusion is inactive', () => {
    const model = selectCadModelState(selected, idle, null, null, null, 'onshape');
    expect(cadModelStatusLine(model, null)).toMatchObject({ text: 'Speaker is not prepared yet.', tone: 'attention' });
  });

  it('does not revive Preparing when a design edit supersedes a stopped failure intent', () => {
    const state = { ...selected, captureIngestIntent: () => 2 };
    const attempted = { ...state, attemptedPreparationKey: cadPreparationKey(state, null) };
    expect(line(attempted, { ...idle, ingestError: 'Older failure', preparationIntent: 1 }))
      .toMatchObject({ text: 'Speaker is not prepared yet.', tone: 'attention', action: 'none' });
  });

  it('says Preparing, never "needs preparation", while the server prepares a stale model', () => {
    const job = { id: 'job-1', status: 'preparing', cad_state: { state: 'processing', held: false } } as unknown as JobItem;
    const stale = line({ ...onScreen, needsIngest: true }, idle, { kind: 'preparing', job });
    expect(stale).toMatchObject({ text: 'Preparing Speaker…', short: 'preparing…', action: 'none' });
  });

  it('asks for a choice held without a mesh on screen, and the mesh-size choice of this window', () => {
    const job = { id: 'held-1', status: 'held', cad_state: { state: 'needs_user_input', reason: 'mesh_too_large', held: true, attention: 'question' } } as unknown as JobItem;
    expect(line(selected, idle, { kind: 'waiting', job })).toMatchObject({ action: 'choose', jobId: 'held-1', actionLabel: 'Choose…' });
    const ceiling: MeshCeilingChoice = { name: 'Speaker', nextStep: 'x', bundlePath: bundle.bundlePath, deferred: true };
    expect(line(selected, idle, null, ceiling)).toMatchObject({ text: 'Speaker needs your choice: it is larger than WG\'s limit.', action: 'choose', jobId: null });
  });

  it('states the ceiling\'s numbers from structured fields, never from the message', () => {
    const job = { id: 'held-1', status: 'held', cad_state: { state: 'needs_user_input', reason: 'mesh_too_large', held: true, attention: 'question',
      message: 'This model meshes to 1 triangles', mesh_limit: { triangles: 54_739, limit: 22_000 } } } as unknown as JobItem;
    const held = line(selected, idle, { kind: 'waiting', job });
    expect(held).toMatchObject({ text: 'Speaker needs your choice: it meshes to 54,739 triangles (limit 22,000).', action: 'choose' });
    expectNoJargon(`${held.text} ${held.short}`, { forbidden: JARGON });
    const ceiling: MeshCeilingChoice = { name: 'Speaker', nextStep: 'x', bundlePath: bundle.bundlePath, deferred: true,
      meshLimit: { triangles: 30_001, limit: 22_000 } };
    expect(line(selected, idle, null, ceiling).text).toBe('Speaker needs your choice: it meshes to 30,001 triangles (limit 22,000).');
  });

  it('fails calmly, with Try again only where it can work, and never also says not prepared', () => {
    const failed = line(selected, { ...idle, ingestError: 'stage 1 bundle validation: $.sources[x]' });
    expect(failed).toMatchObject({ text: 'Waveguide Generator could not prepare Speaker.', short: 'could not prepare', action: 'try-again' });
    expectNoJargon(`${failed.text} ${failed.short}`, { forbidden: JARGON });
    expect(line(pointer, { ...idle, ingestError: 'x' })).toMatchObject({ action: 'none' });
  });

  it('says the server\'s words for a failure the user can act on, with Try again only where it can work', () => {
    const full = line(selected, { ...idle, ingestError: 'The disk is full, so Waveguide Generator cannot prepare Speaker. Free some disk space, then try again.',
      ingestFailure: { recorded: false, retry: true, diagnostic: null } });
    expect(full).toMatchObject({ text: 'The disk is full, so Waveguide Generator cannot prepare Speaker. Free some disk space, then try again.', action: 'try-again' });
    const newer = line(selected, { ...idle, ingestError: 'Speaker was sent by a newer Waveguide Generator add-in than this Waveguide Generator can read. Update Waveguide Generator, then send it again.',
      ingestFailure: { recorded: false, retry: false, diagnostic: null } });
    expect(newer.action).toBe('none');
    // WG's own failure without raw text is still its recorded sentence.
    const recorded = line(selected, { ...idle, ingestError: 'Waveguide Generator could not prepare Speaker. This has been recorded for the developers.',
      ingestFailure: { recorded: true, retry: true, diagnostic: null } });
    expect(recorded.text).toContain('This has been recorded for the developers.');
    for (const result of [full, newer, recorded]) expectNoJargon(`${result.text} ${result.short}`, { forbidden: JARGON });
  });

  it.each(['solver_memory_exceeded', 'snapshot_changed'])('routes a prepared %s refusal to a remedy', reason => {
    const job = { id: 'refused', status: 'error', cad_state: { state: 'needs_user_input', reason,
      held: false, attention: 'action', message: 'Change the model.', next_step: 'Send it again.' } } as unknown as JobItem;
    expect(line(onScreen, idle, { kind: 'refused', job })).toMatchObject({ action: 'cad-link', jobId: 'refused' });
    expect(line(onScreen, idle, { kind: 'prepare-failed', job }).action).toBe('cad-link');
    if (reason === 'solver_memory_exceeded') expect(line({ ...onScreen, selectedBundle: { ...bundle, bundleOrigin: 'onshape' } }, idle, { kind: 'refused', job }).action).toBe('mesh-settings');
  });

  it('never offers Solve for an unheld action refusal on a prepared model', () => {
    const job = { id: 'refused', status: 'error', cad_state: { state: 'needs_user_input', reason: 'excitation_refused',
      held: false, attention: 'action', message: 'Change the drive channels.' } } as unknown as JobItem;
    expect(line(onScreen, idle, requestFor(job))).toMatchObject({ action: 'none', tone: 'attention' });
  });

  it('words the top-bar chip for requests about other models', () => {
    const question = { kind: 'prepare_and_solve', attention: 'question' } as const;
    expect(cadRequestsChipLabel([question], { otherModels: true })).toBe('Another model needs your choice');
    expect(cadRequestsChipLabel([question])).toBe('A solve is waiting for you');
    expect(cadRequestsChipLabel([question, { kind: 'prepare_and_solve', attention: 'action' }])).toBe('2 CAD requests need you');
  });

  it('shows results only when Results are not on screen, and waits for the engine without a button', () => {
    const job = { id: 'job-1', status: 'complete', has_results: true, run_number: 2, cad_state: { state: 'accepted', held: false } } as unknown as JobItem;
    const request = requestFor(job);
    expect(line(onScreen, idle, request)).toMatchObject({ text: 'Solved · run #2', action: 'show-results' });
    expect(line(onScreen, idle, request, null, { resultsShown: true })).toMatchObject({ action: 'none' });
    expect(line(onScreen, idle, null, null, { waitingForEngine: true })).toMatchObject({ text: 'Solve engine is getting ready…', action: 'none' });
  });

  it('reads the solve progress the server counts, without parsing the stage message', () => {
    const job = { id: 'job-1', status: 'running', progress: 0.25, stage: 'solve', stage_message: 'Solving frequency 9/36',
      cad_state: { state: 'accepted', held: false } } as unknown as JobItem;
    expect(line(onScreen, idle, requestFor(job)).text).toBe('Solving · 25%');
    expect(line(onScreen, idle, requestFor({ ...job, frequency_progress: { done: 9, total: 36 } } as JobItem)))
      .toMatchObject({ text: 'Solving · 9 of 36 frequencies', short: 'solving 9/36' });
  });

  it('says what happened to a return WG cannot read, and where a copy comes from, in the user\'s CAD app', () => {
    const unreadable = { ...selected, selectedBundle: { ...bundle, readable: false } };
    expect(line(unreadable)).toMatchObject({ text: 'Waveguide Generator cannot read Speaker. Send it again from Fusion.', action: 'none' });
    expect(line({ ...unreadable, selectedBundle: { ...unreadable.selectedBundle, bundleOrigin: 'onshape' } }).text)
      .toBe('Waveguide Generator cannot read Speaker. Send it again from Onshape.');
  });

  it('does not offer Choose… for the question already on screen', () => {
    const job = { id: 'held-1', status: 'held', cad_state: { state: 'needs_user_input', reason: 'setup_required', held: true, attention: 'question' } } as unknown as JobItem;
    expect(line(selected, idle, { kind: 'waiting', job }, null, { choiceOpen: true })).toMatchObject({ action: 'none', short: 'needs your choice' });
    const asked: MeshCeilingChoice = { name: 'Speaker', nextStep: 'x', bundlePath: bundle.bundlePath, deferred: false };
    expect(line(selected, idle, null, asked).action).toBe('none');
  });

  it('follows a refused continuation child, never its solved parent', () => {
    const parent = { ...cadJobFixture(operation('job-parent', 'accepted'), { status: 'complete' }), has_results: true, run_number: 1 } as JobItem;
    const child = cadJobFixture(operation('job-child', 'rejected', { reason: 'model_cut_unsolvable', message: 'Speaker arrived cut open.' }),
      { parent_job_id: 'job-parent', status: 'error', created_at: '2026-10-25T11:00:00Z' });
    const model: CurrentModel = { kind: 'cad', key: null, lineage: null, label: 'Speaker', viewportShowing: false,
      manifestSha256: MANIFEST };
    const request = currentModelStatus(model, [parent, child]);
    const said = cadModelStatusLine(selectCadModelState(onScreen, idle, request), request);
    expect(said).toMatchObject({ text: 'Speaker arrived cut open.', action: 'none' });
    expect(said.text).not.toContain('Solved');
  });
});

describe('file input uses the same status line', () => {
  const file = { input: 'file' as const, label: 'speaker.msh', reading: false, preparing: false,
    choice: null, failureMessage: null, canPrepare: true, prepared: true, triangles: 9730, symmetryLabel: 'Quarter model' };
  it.each([
    [{ reading: true }, 'Reading speaker.msh…', 'none'],
    [{ preparing: true }, 'Preparing speaker.msh…', 'none'],
    [{ choice: "Choose the file's units.", prepared: false }, "Choose the file's units.", 'none'],
    [{ choice: 'Choose which surface is a driver.', prepared: false }, 'Choose which surface is a driver.', 'none'],
    [{ failureMessage: 'Close the opening in the meshing tool.' }, 'Close the opening in the meshing tool.', 'try-again'],
    [{}, 'Ready to solve · 9,730 triangles · quarter model', 'solve'],
  ])('states %j without jargon', (patch, text, action) => {
    const status = cadModelStatusLine({ ...file, ...patch }, null);
    expect(status).toMatchObject({ text, action });
    expectNoJargon(status.text);
    if (status.actionLabel) expectNoJargon(status.actionLabel);
  });
  it('reuses solving and solved behavior for file requests', () => {
    const job = cadJobFixture(operation('file-run', 'accepted'), { status: 'running', progress: .5 });
    const running = cadModelStatusLine(file, { kind: 'running', job, message: '' });
    expect(running).toMatchObject({ text: 'Solving · 50%', action: 'none' });
    const solved = cadModelStatusLine(file, { kind: 'solved', job: { ...job, status: 'complete' }, message: '' });
    expect(solved.action).toBe('show-results');
    expectNoJargon(running.text);
    expectNoJargon(solved.text);
  });
});


it('keeps file readiness and import notes as separate facts', () => {
  const status = cadModelStatusLine({ input: 'file', label: 'speaker.msh', reading: false, preparing: false,
    choice: null, failureMessage: null, canPrepare: true, prepared: true, triangles: 9730,
    symmetryLabel: 'Quarter model', notice: 'Using the settings from an earlier import' }, null);
  expect(status.text).toBe('Ready to solve · 9,730 triangles · quarter model · Using the settings from an earlier import');
  expect(status).toMatchObject({ tone: 'ready', action: 'solve', short: 'ready · 9,730 triangles · quarter model' });
});


it('keeps a restored file note visible beside a solved run', () => {
  const job = cadJobFixture(operation('file-run', 'accepted'), { status: 'complete' });
  const status = cadModelStatusLine({ input: 'file', label: 'speaker.msh', reading: false, preparing: false,
    choice: null, failureMessage: null, canPrepare: true, prepared: true, triangles: 9730,
    symmetryLabel: 'Quarter model', notice: 'This run uses an older saved setup.' },
  { kind: 'solved', job, message: '' }, { resultsShown: true });
  expect(status.text).toContain('Solved');
  expect(status.text).toContain('This run uses an older saved setup.');
  expect(status.action).toBe('none');
  expect(isQuietStatusLine(status)).toBe(false);
});
