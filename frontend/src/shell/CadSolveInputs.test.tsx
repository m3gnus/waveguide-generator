import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadOperationSummary } from '../api/cadOperations';
import { CadSolveInputs } from './CadSolveInputs';

const manifest = `sha256:${'b'.repeat(64)}`;
const operation = (overrides: Partial<CadOperationSummary> = {}): CadOperationSummary => ({
  operationId: 'op-1', kind: 'prepare_and_solve', state: 'needs_user_input', stage: 'ready',
  reason: 'ready_to_solve', message: null, jobId: null, attemptGeneration: 1,
  setupRevisionId: 'wgs_1', preparationId: 'wgp_1',
  snapshot: { manifestSha256: manifest, documentName: 'Tritonia', projectLineageId: 'wgl_1' },
  legacy: false, createdAt: '2026-09-14T10:00:00Z', updatedAt: '2026-09-14T10:00:05Z',
  ...overrides,
});

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});

describe('CAD solve input identities', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('reads a pending operation engine from its setup revision despite a different job engine', async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe('/api/cadlink/setup-revisions/wgs_1');
      return json({
        revisionId: 'wgs_1', contentSha256: 'sha256:setup', createdAt: 'now',
        setup: { schema_version: 1, geometry: {}, options: { engine: 'metal' } },
      });
    });
    vi.stubGlobal('fetch', fetcher);

    await act(async () => root.render(<CadSolveInputs
      operationId="op-1"
      operation={operation()}
      resolvedEngine="beat-cpu"
      engineSource="setup-revision"
    />));
    await vi.waitFor(() => expect(host.textContent).toContain('Enginemetal'));

    expect(host.textContent).toContain(`SnapshotTritonia${manifest}`);
    expect(host.textContent).toContain('Preparationwgp_1');
    expect(host.textContent).toContain('Setup revisionwgs_1');
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it('uses the job engine that actually ran while resolving its operation identities', async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe('/api/cadlink/operations/op-job');
      return json({ ...operation({ operationId: 'op-job', jobId: 'job-1' }), approvals: [], preparation: null });
    });
    vi.stubGlobal('fetch', fetcher);

    await act(async () => root.render(<CadSolveInputs
      operationId="op-job"
      resolvedEngine="beat-cpu"
      engineSource="job"
    />));
    await vi.waitFor(() => expect(host.textContent).toContain('Preparationwgp_1'));

    expect(host.textContent).toContain('Enginebeat-cpu');
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it('labels absent legacy identities instead of manufacturing them', async () => {
    vi.stubGlobal('fetch', vi.fn());
    await act(async () => root.render(<CadSolveInputs
      operationId="op-legacy"
      operation={operation({
        operationId: 'op-legacy', setupRevisionId: null, preparationId: null, snapshot: null, legacy: true,
      })}
      engineSource="job"
    />));

    expect(host.querySelectorAll('code')).toHaveLength(4);
    expect(host.textContent?.match(/not recorded/g)).toHaveLength(4);
  });

  it('does not substitute a setup engine when a historical job did not persist one', async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe('/api/cadlink/operations/op-job');
      return json({ ...operation({ operationId: 'op-job', jobId: 'job-1' }), approvals: [], preparation: null });
    });
    vi.stubGlobal('fetch', fetcher);

    await act(async () => root.render(<CadSolveInputs operationId="op-job" engineSource="job"/>));
    await vi.waitFor(() => expect(host.textContent).toContain('Preparationwgp_1'));

    expect(host.textContent).toContain('Enginenot recorded');
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it('shows complete snapshot digests even when their prefixes match', async () => {
    const first = `sha256:${'a'.repeat(63)}1`;
    const second = `sha256:${'a'.repeat(63)}2`;
    vi.stubGlobal('fetch', vi.fn());

    await act(async () => root.render(<>
      <CadSolveInputs
        operationId="op-first"
        operation={operation({
          operationId: 'op-first', setupRevisionId: null,
          snapshot: { ...operation().snapshot!, manifestSha256: first },
        })}
        resolvedEngine="metal"
        engineSource="job"
      />
      <CadSolveInputs
        operationId="op-second"
        operation={operation({
          operationId: 'op-second', setupRevisionId: null,
          snapshot: { ...operation().snapshot!, manifestSha256: second },
        })}
        resolvedEngine="metal"
        engineSource="job"
      />
    </>));

    expect(host.textContent).toContain(first);
    expect(host.textContent).toContain(second);
  });
});
