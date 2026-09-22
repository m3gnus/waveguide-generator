/**
 * "Errors only when the user makes a mistake": a waiting request gets a card
 * only when it is about what is in front of the user -- the model on screen,
 * or the interrupted update the active Fusion document reports. Everything
 * else is one quiet line under "Earlier requests", and no card prints the
 * backend's bookkeeping (stages, finding ids, operation ids).
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
  dismissOperation: vi.fn(async (_operationId: string) => undefined),
  solveOperationWithSettings: vi.fn(async () => undefined),
  reconcileOperation: vi.fn(async () => undefined),
  reportError: vi.fn(),
  fusionStatus: null as null | { documentName: string | null; recoveryRequired: { operationId: string; phase: string } | null },
}));
vi.mock('./CadLinkCoordinator', () => ({
  cadLinkCoordinatorBridge: { getSnapshot: () => coordinator, subscribe: () => () => undefined },
}));
vi.mock('./CadSolveInputs', () => ({ CadSolveInputs: () => null }));
vi.mock('./CadProjectPanel', () => ({ openCadProject: vi.fn() }));

const { CadOperationsSection } = await import('./CadOperationsSection');

const ON_SCREEN = `sha256:${'a'.repeat(64)}`;
const ELSEWHERE = `sha256:${'b'.repeat(64)}`;
const RECOVERY_ID = '82a49358-0f6d-4c1e-9d55-2b7a1c7e0f11';
const FINDING = 'finding-scope-degradation-234d57f09d7664d3';
const record = { manifest_sha256: ON_SCREEN } as CadReturnIngestRecord;

const solve = (overrides: Partial<CadOperationSummary> = {}): CadOperationSummary => ({
  operationId: 'op-screen', kind: 'prepare_and_solve', state: 'needs_user_input', stage: 'validating',
  reason: 'setup_required', message: 'Choose the solve settings … open it from File → CAD-linked designs, then press Solve now.',
  jobId: null, attemptGeneration: 1, setupRevisionId: null, preparationId: 'wgi_prep1',
  snapshot: { manifestSha256: ON_SCREEN, documentName: 'PartyMEH v10', projectLineageId: 'wgl_1' },
  legacy: false, createdAt: '2026-09-22T05:52:34Z', updatedAt: '2026-09-22T05:52:34Z',
  ...overrides,
});

const recovery = (overrides: Partial<CadOperationSummary> = {}): CadOperationSummary => ({
  ...solve(),
  operationId: RECOVERY_ID, kind: 'insert_link', state: 'recovery_required', stage: null, reason: null, message: null,
  preparationId: null, snapshot: { manifestSha256: null, documentName: '250728solana' },
  createdAt: '2026-09-20T12:12:00Z',
  ...overrides,
});

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

function stubReview(approvals: Array<{ preparation_id: string; finding_id: string }> = []) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = decodeURIComponent(String(input));
    if (url.startsWith('/api/cadlink/operations/')) {
      return json({
        ...solve(), approvals,
        preparation: {
          preparationId: 'wgi_prep1', ingestId: 'wgi_prep1', snapshotSha256: 's', setupRevisionId: 'wgs_1',
          reportSha256: 'r', blockingFindingIds: [FINDING], attemptGeneration: 1,
        },
      });
    }
    if (url.startsWith('/api/cadlink/ingest/')) {
      return json({ findings: [{ id: FINDING, kind: 'scope-degradation', detail: 'skipped bodies: Body11', blocking: true }] });
    }
    throw new Error(`unexpected ${url}`);
  }));
}

describe('operation cards for the model on screen', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    coordinator.fusionStatus = null;
    Object.values(coordinator).forEach((value) => { if (typeof value === 'function' && 'mockClear' in value) value.mockClear(); });
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    useCadOperationsStore.setState({ operations: {} });
    vi.unstubAllGlobals();
  });

  async function show(operations: CadOperationSummary[]) {
    useCadOperationsStore.setState({ operations: Object.fromEntries(operations.map((item) => [item.operationId, item])) });
    await act(async () => root.render(<CadOperationsSection record={record}/>));
  }

  const cards = () => [...host.querySelectorAll<HTMLElement>('.cad-operation')];
  const earlier = () => host.querySelector<HTMLDetailsElement>('.cad-earlier-requests');

  it('gives a card only to the solve of the model on screen, and one quiet line to each other request', async () => {
    await show([
      solve(),
      solve({ operationId: 'manual-solve:old', snapshot: { manifestSha256: ELSEWHERE, documentName: 'document', projectLineageId: null }, reason: 'frame_confirmation_required' }),
      recovery(),
    ]);
    // Positive control: the model on screen keeps its card.
    expect(cards().map((card) => card.dataset.operationId)).toEqual(['op-screen']);
    const disclosure = earlier()!;
    expect(disclosure.querySelector('summary')?.textContent).toBe('Earlier requests (2)');
    expect(disclosure.open).toBe(false);
    const lines = [...disclosure.querySelectorAll<HTMLElement>('li')];
    expect(lines.map((line) => line.dataset.operationId)).toEqual([RECOVERY_ID, 'manual-solve:old']);
    expect(lines[0].textContent).toContain('250728solana');
    expect(lines[1].textContent).toContain('document');
    // No axis chooser, no ladder and no ids for a model that is not on screen.
    expect(disclosure.querySelector('[data-action="confirm-frame"]')).toBeNull();
    expect(disclosure.textContent).not.toContain(RECOVERY_ID);
    expect(disclosure.textContent).not.toContain('Journal phase');

    await act(async () => { lines[1].querySelector<HTMLButtonElement>('button')!.click(); });
    expect(coordinator.dismissOperation).toHaveBeenCalledWith('manual-solve:old');
    coordinator.dismissOperation.mockClear();
    const clear = [...disclosure.querySelectorAll('button')].find((button) => button.textContent === 'Clear all')!;
    await act(async () => { clear.click(); });
    await vi.waitFor(() => expect(coordinator.dismissOperation).toHaveBeenCalledTimes(2));
    expect(coordinator.dismissOperation.mock.calls.map(([id]) => id)).toEqual([RECOVERY_ID, 'manual-solve:old']);
  });

  it('shows no Earlier requests at all when every request is about the model on screen', async () => {
    await show([solve()]);
    expect(cards()).toHaveLength(1);
    expect(earlier()).toBeNull();
  });

  it('makes a recovery loud only while the active Fusion document reports it, and then names the document', async () => {
    await show([recovery()]);
    expect(cards()).toHaveLength(0);
    expect(earlier()?.textContent).toContain('250728solana');

    coordinator.fusionStatus = { documentName: '250728solana', recoveryRequired: { operationId: RECOVERY_ID, phase: 'applied' } };
    await act(async () => root.render(<CadOperationsSection record={null}/>));
    const [card] = cards();
    expect(card.dataset.operationId).toBe(RECOVERY_ID);
    expect(card.textContent).toContain('Update interrupted — recovery required · 250728solana');
    for (const jargon of ['Journal phase', 'durable operation notice', RECOVERY_ID]) {
      expect(card.textContent).not.toContain(jargon);
    }
    expect(earlier()).toBeNull();
  });

  it('prints neither the stage nor, for the model on screen, the backend’s settings message', async () => {
    await show([solve()]);
    const [card] = cards();
    expect(card.querySelector('[role="status"]')?.textContent).toBe('Waiting for you · needs its solve settings');
    expect(card.textContent).not.toContain('validating');
    expect(card.textContent).not.toContain('File → CAD-linked designs');
    // Positive control: the card's own guidance remains; its action is the
    // Solve card's one Solve (M1b), never a second solving button here.
    expect(card.textContent).toContain('This model is on screen: check the settings above, then press Solve.');
    expect([...card.querySelectorAll('button')].map((button) => button.textContent)).toEqual(['Dismiss']);
  });

  it('keeps the backend message for a reason the card does not already explain', async () => {
    await show([solve({ reason: 'engine_unavailable', message: 'Engines that can: bempp, beat-cpu.' })]);
    expect(cards()[0].textContent).toContain('Engines that can: bempp, beat-cpu.');
  });

  it('at the frame gate says it once: no repeated guidance, no finding ids, no filler, and approval named as a later step', async () => {
    stubReview();
    await show([solve({ reason: 'frame_confirmation_required', message: 'Confirm this model’s solver frame in WG first: choose the axis it radiates along.' })]);
    const [card] = cards();
    const ladder = () => [...card.querySelectorAll('.cad-operation-ladder li')].map((item) => item.textContent);
    await vi.waitFor(() => expect(ladder()).toEqual([
      'Now: check which way it radiates, above, and press Solve to confirm it.',
      'Then: approve 1 finding.',
    ]));
    expect(card.textContent).not.toContain(FINDING);
    expect(card.textContent).not.toContain('was authored in CAD. Confirm the axis');
    expect(card.textContent).not.toContain('Confirm this model’s solver frame in WG first');
    expect(card.textContent).not.toContain('submits the solve');
    expect(card.textContent).not.toContain('Confirming the frame prepares it again');
  });

  it('at the frame gate names no approval step once every finding is approved', async () => {
    stubReview([{ preparation_id: 'wgi_prep1', finding_id: FINDING }]);
    await show([solve({ reason: 'frame_confirmation_required' })]);
    const [card] = cards();
    // The review has been read (both requests answered) before the ladder is judged.
    await vi.waitFor(() => expect(vi.mocked(fetch).mock.calls).toHaveLength(2));
    await act(async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); });
    expect([...card.querySelectorAll<HTMLElement>('.cad-operation-ladder li')].map((item) => item.dataset.gate)).toEqual(['frame']);
  });

  it('at the findings gate lists each finding in words, beside Approve and solve', async () => {
    stubReview();
    await show([solve({ reason: 'findings_need_review', message: null })]);
    const [card] = cards();
    await vi.waitFor(() => expect(card.querySelector('.cad-operation-findings')?.textContent).toBe('scope degradation — skipped bodies: Body11'));
    expect(card.textContent).not.toContain(FINDING);
    expect([...card.querySelectorAll('button')].some((button) => button.textContent === 'Approve and solve')).toBe(true);
  });
});
