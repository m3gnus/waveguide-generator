import { describe, expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { currentModelStatus, type CurrentModel } from '../model/currentModel';
import { bundle, record } from '../shell/cadlink/selection.fixtures';
import states from '../../../server/tests/fixtures/cad_request_states.json';
import { selectCadModelState, type CadModelRequest } from './cadModelState';
import { cadModelStatusLine, cadRequestState, cadRequestsChipLabel, type CadModelStatusLine } from './cadModelStatusLine';
import type { MeshCeilingChoice } from './meshCeilingChoice';
import { cadJobFixture } from '../jobs/cadSolve.fixtures';
import type { CadOperationSummary } from '../api/cadOperations';

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
function expectNoJargon(line: CadModelStatusLine) {
  for (const word of JARGON) expect(`${line.text} ${line.short}`).not.toContain(word);
}

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
    expectNoJargon(line);
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
    expectNoJargon(ready);
  });

  it('prepares selected and changed models automatically without a Prepare action', () => {
    expectNoJargon(line(selected));
    expectNoJargon(line({ ...onScreen, needsIngest: true }));
    expect(line(selected)).toMatchObject({ text: 'Preparing Speaker…', action: 'none' });
    expect(line(pointer)).toMatchObject({ action: 'none' });
    expect(line({ ...onScreen, needsIngest: true })).toMatchObject({ text: 'Preparing Speaker…', action: 'none', actionLabel: null });
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
    expectNoJargon(held);
    const ceiling: MeshCeilingChoice = { name: 'Speaker', nextStep: 'x', bundlePath: bundle.bundlePath, deferred: true,
      meshLimit: { triangles: 30_001, limit: 22_000 } };
    expect(line(selected, idle, null, ceiling).text).toBe('Speaker needs your choice: it meshes to 30,001 triangles (limit 22,000).');
  });

  it('fails calmly, with Try again only where it can work, and never also says not prepared', () => {
    const failed = line(selected, { ...idle, ingestError: 'stage 1 bundle validation: $.sources[x]' });
    expect(failed).toMatchObject({ text: 'Waveguide Generator could not prepare Speaker.', short: 'could not prepare', action: 'try-again' });
    expectNoJargon(failed);
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
    for (const result of [full, newer, recorded]) expectNoJargon(result);
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

