import { describe, expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { currentModelStatus, type CurrentModel } from '../model/currentModel';
import { bundle, record } from '../shell/cadlink/selection.fixtures';
import states from '../../../server/tests/fixtures/cad_request_states.json';
import { selectCadModelState, type CadModelRequest } from './cadModelState';
import { cadModelStatusLine, cadRequestState, type CadModelStatusLine } from './cadModelStatusLine';
import type { MeshCeilingChoice } from './meshCeilingChoice';

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
function expectPlain(line: CadModelStatusLine) {
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
    expectPlain(line);
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
    expectPlain(ready);
  });

  it('offers the one Prepare of an unprepared model until preparation is automatic', () => {
    expect(line(selected)).toMatchObject({ text: 'Speaker is not prepared yet.', action: 'prepare' });
    expect(line(pointer)).toMatchObject({ action: 'none' });
    expect(line({ ...onScreen, needsIngest: true })).toMatchObject({ text: 'Speaker changed since it was prepared.', action: 'prepare', actionLabel: 'Prepare again' });
  });

  it('says Preparing, never "needs preparation", while the server prepares a stale model', () => {
    const job = { id: 'job-1', status: 'preparing', cad_state: { state: 'processing', held: false } } as unknown as JobItem;
    const stale = line({ ...onScreen, needsIngest: true }, idle, { kind: 'preparing', job });
    expect(stale).toMatchObject({ text: 'Preparing Speaker…', short: 'preparing…', action: 'none' });
  });

  it('asks for a choice held without a mesh on screen, and the mesh-size choice of this window', () => {
    const job = { id: 'held-1', status: 'held', cad_state: { state: 'needs_user_input', reason: 'mesh_too_large', held: true, attention: 'question' } } as unknown as JobItem;
    expect(line(selected, idle, { kind: 'waiting', job })).toMatchObject({ action: 'choose', jobId: 'held-1', actionLabel: 'Choose…' });
    const ceiling: MeshCeilingChoice = { name: 'Speaker', nextStep: 'x', bundlePath: bundle.bundlePath, then: 'prepare', deferred: true };
    expect(line(selected, idle, null, ceiling)).toMatchObject({ text: 'Speaker needs your choice: it is larger than WG\'s limit.', action: 'choose', jobId: null });
  });

  it('fails calmly, with Try again only where it can work, and never also says not prepared', () => {
    const failed = line(selected, { ...idle, ingestError: 'stage 1 bundle validation: $.sources[x]' });
    expect(failed).toMatchObject({ text: 'WG could not prepare Speaker.', short: 'could not prepare', action: 'try-again' });
    expectPlain(failed);
    expect(line(pointer, { ...idle, ingestError: 'x' })).toMatchObject({ action: 'none' });
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
});
