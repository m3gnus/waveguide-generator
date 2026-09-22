/**
 * V5 / F6: every `needs_user_input` gate is visible and actionable, not only
 * the first. `reason` names one current gate; the rest of the ladder comes
 * from the preparation's blocking findings still to approve.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnIngestRecord } from '../api/cadlink';
import type { CadOperationSummary } from '../api/cadOperations';
import { useCadOperationsStore } from '../stores/cadOperations';

const coordinator = vi.hoisted(() => ({
  solveOperation: vi.fn(async () => undefined),
  approveOperation: vi.fn(async () => undefined),
  dismissOperation: vi.fn(async () => undefined),
  solveOperationWithSettings: vi.fn(async () => undefined),
}));
vi.mock('./CadLinkCoordinator', () => {
  const snapshot = {
    ...coordinator,
    reconcileOperation: vi.fn(), reportError: vi.fn(),
    fusionStatus: null,
  };
  return { cadLinkCoordinatorBridge: { getSnapshot: () => snapshot, subscribe: () => () => undefined } };
});
vi.mock('./CadSolveInputs', () => ({ CadSolveInputs: () => null }));
vi.mock('./CadProjectPanel', () => ({ openCadProject: vi.fn() }));
// The frame gate's own control, stood in for: its preview has its own tests.
vi.mock('./CadSolverFrameConfirm', () => ({
  CadSolverFrameConfirm: ({ onConfirmed }: { onConfirmed: () => void }) => <button data-action="confirm-frame" onClick={onConfirmed}>Confirm frame and solve</button>,
}));

const { CadOperationsSection, solveGateLadder } = await import('./CadOperationsSection');

const FINDING = 'finding-scope-degradation-234d57f09d7664d3';

const operation = (reason: string, overrides: Partial<CadOperationSummary> = {}): CadOperationSummary => ({
  operationId: 'manual-solve:op-1', kind: 'prepare_and_solve', state: 'needs_user_input', stage: 'ready',
  reason, message: 'The backend’s message for this gate.', jobId: null, attemptGeneration: 1,
  setupRevisionId: 'wgs_1', preparationId: 'wgi_prep1',
  snapshot: { manifestSha256: `sha256:${'a'.repeat(64)}`, documentName: 'PartyMEH', projectLineageId: 'wgl_1' },
  legacy: false, createdAt: 'now', updatedAt: 'now',
  ...overrides,
});

// Cards are shown for the model on screen only.
const onScreen = { manifest_sha256: `sha256:${'a'.repeat(64)}` } as CadReturnIngestRecord;

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

function stubBackend(approvals: Array<{ preparation_id: string; finding_id: string }> = []) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = decodeURIComponent(String(input));
    if (url.startsWith('/api/cadlink/operations/manual-solve:op-1')) {
      return json({
        ...operation('frame_confirmation_required'),
        approvals,
        preparation: {
          preparationId: 'wgi_prep1', ingestId: 'wgi_prep1', snapshotSha256: 'sha256:s', setupRevisionId: 'wgs_1',
          reportSha256: 'sha256:r', blockingFindingIds: [FINDING], attemptGeneration: 1,
        },
      });
    }
    if (url.startsWith('/api/cadlink/ingest/wgi_prep1')) {
      return json({ findings: [{ id: FINDING, kind: 'scope-degradation', detail: 'skipped bodies: Body11', blocking: true }] });
    }
    throw new Error(`unexpected ${url}`);
  }));
}

describe('the needs_user_input ladder', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    Object.values(coordinator).forEach((mock) => mock.mockClear());
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    useCadOperationsStore.setState({ operations: {} });
    vi.unstubAllGlobals();
  });

  async function show(summary: CadOperationSummary): Promise<HTMLOListElement> {
    useCadOperationsStore.setState({ operations: { [summary.operationId]: summary } });
    await act(async () => root.render(<CadOperationsSection record={onScreen}/>));
    await vi.waitFor(() => expect(host.querySelector('.cad-operation-ladder')).not.toBeNull());
    return host.querySelector<HTMLOListElement>('.cad-operation-ladder')!;
  }

  const steps = (ladder: HTMLOListElement) => [...ladder.querySelectorAll('li')].map((item) => ({
    gate: item.dataset.gate, current: item.getAttribute('aria-current') === 'step', text: item.textContent ?? '',
  }));

  it('at the frame gate, shows the frame now and the findings still to approve behind it', async () => {
    stubBackend();
    const ladder = await show(operation('frame_confirmation_required'));
    await vi.waitFor(() => expect(steps(ladder).map((step) => step.gate)).toEqual(['frame', 'findings']));
    const [frame, findings] = steps(ladder);
    expect(frame.current).toBe(true);
    expect(findings.current).toBe(false);
    expect(findings.text).toBe('Then: approve 1 finding.');
    // The current gate's action is on the card.
    const confirm = host.querySelector<HTMLButtonElement>('button[data-action="confirm-frame"]')!;
    expect(confirm).not.toBeNull();
    await act(async () => { confirm.click(); });
    // A continuation: the operation keeps the settings it already holds. New
    // settings from the controls would be a new preparation and lose approvals.
    expect(coordinator.solveOperation).toHaveBeenCalledWith('manual-solve:op-1');
    expect(coordinator.solveOperationWithSettings).not.toHaveBeenCalled();
    // A manual solve is the user's own, never "Fusion asked for a solve".
    expect(host.textContent).toContain('Your solve is waiting');
  });

  it('at the frame gate with no blocking findings, lists only the frame', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).startsWith('/api/cadlink/operations/')) {
        return json({
          ...operation('frame_confirmation_required'), approvals: [],
          preparation: { preparationId: 'wgi_prep1', ingestId: 'wgi_prep1', snapshotSha256: 's', setupRevisionId: 'wgs_1', reportSha256: 'r', blockingFindingIds: [], attemptGeneration: 1 },
        });
      }
      return json({ findings: [] });
    }));
    const ladder = await show(operation('frame_confirmation_required'));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(steps(ladder).map((step) => step.gate)).toEqual(['frame']);
  });

  it('at the findings gate, names the findings and offers Approve and solve', async () => {
    stubBackend();
    // The backend's own message names the findings by id (preparation.py).
    const ladder = await show(operation('findings_need_review', {
      attemptGeneration: 2, message: `Review the preparation's findings before solving: ${FINDING}`,
    }));
    await vi.waitFor(() => expect(host.querySelector('button[aria-label="Approve and solve: PartyMEH"]')).not.toBeNull());
    expect(steps(ladder).map((step) => [step.gate, step.current])).toEqual([['findings', true]]);
    // In words, never by id.
    expect(host.textContent).toContain('scope degradation — skipped bodies: Body11');
    expect(host.textContent).not.toContain(FINDING);
    await act(async () => { host.querySelector<HTMLButtonElement>('button[aria-label="Approve and solve: PartyMEH"]')!.click(); });
    // The approval continues the preparation it was given for, with its settings.
    expect(coordinator.approveOperation).toHaveBeenCalledWith('manual-solve:op-1', {
      preparationId: 'wgi_prep1', findingIds: [FINDING],
    });
    expect(coordinator.solveOperationWithSettings).not.toHaveBeenCalled();
  });

  it('at the last gate, offers Solve now', async () => {
    stubBackend();
    const ladder = await show(operation('ready_to_solve'));
    expect(steps(ladder).map((step) => [step.gate, step.current])).toEqual([['solve', true]]);
    const solve = host.querySelector<HTMLButtonElement>('button[aria-label="Solve now: PartyMEH"]')!;
    await act(async () => { solve.click(); });
    expect(coordinator.solveOperation).toHaveBeenCalledWith('manual-solve:op-1');
    expect(coordinator.solveOperationWithSettings).not.toHaveBeenCalled();
  });

  it.each([
    ['manual-solve:op-1', 'preparation_failed'], ['op-fusion', 'preparation_failed'],
    ['manual-solve:op-1', 'interrupted'], ['op-fusion', 'interrupted'],
  ])('retries %s after %s with the settings it holds', async (operationId, reason) => {
    stubBackend();
    await show(operation(reason, { operationId }));
    await act(async () => { host.querySelector<HTMLButtonElement>('button[aria-label="Solve now: PartyMEH"]')!.click(); });
    expect(coordinator.solveOperation).toHaveBeenCalledWith(operationId);
    expect(coordinator.solveOperationWithSettings).not.toHaveBeenCalled();
  });

  it.each(['manual-solve:op-1', 'op-fusion'])('after submission_refused, solves %s with the settings now on screen', async (operationId) => {
    stubBackend();
    await show(operation('submission_refused', { operationId }));
    // Its card, too, asks for another engine and then Solve now.
    expect(host.textContent).toContain('pick one in the solver selector');
    await act(async () => { host.querySelector<HTMLButtonElement>('button[aria-label="Solve now: PartyMEH"]')!.click(); });
    expect(coordinator.solveOperationWithSettings).toHaveBeenCalledWith(operationId);
    expect(coordinator.solveOperation).not.toHaveBeenCalled();
  });

  it.each(['manual-solve:op-1', 'op-fusion'])('after engine_unavailable, solves %s with the engine now selected on screen', async (operationId) => {
    stubBackend();
    await show(operation('engine_unavailable', { operationId }));
    // The card asks for another engine to be picked, then Solve now: that
    // press has to send the new pick, not retry the engine that was refused.
    expect(host.textContent).toContain('Pick one of the engines');
    await act(async () => { host.querySelector<HTMLButtonElement>('button[aria-label="Solve now: PartyMEH"]')!.click(); });
    expect(coordinator.solveOperationWithSettings).toHaveBeenCalledWith(operationId);
    expect(coordinator.solveOperation).not.toHaveBeenCalled();
  });

  it('counts an approval only on the preparation it was given for, as the backend records them', async () => {
    const SECOND = 'finding-area-drift-0001';
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = decodeURIComponent(String(input));
      if (url.startsWith('/api/cadlink/operations/')) {
        return json({
          ...operation('findings_need_review'),
          // One approved on this preparation, one on an earlier one.
          approvals: [
            { preparation_id: 'wgi_prep1', finding_id: FINDING },
            { preparation_id: 'wgi_prep0', finding_id: SECOND },
          ],
          preparation: {
            preparationId: 'wgi_prep1', ingestId: 'wgi_prep1', snapshotSha256: 's', setupRevisionId: 'wgs_1',
            reportSha256: 'r', blockingFindingIds: [FINDING, SECOND], attemptGeneration: 1,
          },
        });
      }
      return json({ findings: [] });
    }));
    const ladder = await show(operation('findings_need_review'));
    await vi.waitFor(() => expect(steps(ladder)[0].text).toContain('1 finding'));
    expect(steps(ladder)[0].text).not.toContain('2 findings');
  });

  it('counts only findings not already approved on this preparation', () => {
    const ladder = solveGateLadder(operation('findings_need_review'), { findingIds: ['a', 'b'], approvedIds: ['a'] });
    expect(ladder[0].text).toContain('1 finding');
  });

  it('lists nothing for an operation that is not waiting (the positive control is every case above)', () => {
    expect(solveGateLadder(operation('frame_confirmation_required', { state: 'processing' }), null)).toEqual([]);
  });
});
