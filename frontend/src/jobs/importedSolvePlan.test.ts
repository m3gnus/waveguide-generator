import { describe, expect, it, vi } from 'vitest';
import { postImportedSolvePlan, SolvePlanRefused, type ImportedSolvePlan } from './actions';
import { declaresImportedGeometry } from '../design/backendSupport';
import type { EngineCapability } from './actions';

const PLAN: ImportedSolvePlan = {
  ingest_id: 'wgi_plan',
  requested: 'auto',
  engine: 'beat-cpu',
  code: null,
  reason: 'AUTO selected the first available engine, in AUTO order, that declares imported geometry',
  domain: 'half_yz',
  engines: [
    { name: 'metal', label: 'Metal — Apple GPU', solves: false, stage: 'availability', code: 'engine_unavailable', reason: 'unavailable (no Apple GPU)' },
    { name: 'beat-cpu', label: 'BEAT · CPU — no GPU needed', solves: true },
  ],
};

function respond(body: unknown, status = 200): typeof fetch {
  return vi.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;
}

describe('the imported solve plan', () => {
  it('returns the server’s verdict for every engine, and where the request resolves', async () => {
    const fetcher = respond(PLAN);
    await expect(postImportedSolvePlan('{}', fetcher)).resolves.toEqual(PLAN);
    expect(fetcher).toHaveBeenCalledWith('/api/solve/imported-plan', expect.objectContaining({ method: 'POST' }));
  });

  it('keeps a request no engine takes as an answer, not a fault', async () => {
    const refused = { ...PLAN, engine: null, code: 'imported_engine_unsupported', reason: 'engine bempp cannot solve this CAD return' };
    await expect(postImportedSolvePlan('{}', respond(refused))).resolves.toMatchObject({ engine: null });
  });

  it('turns a record the server refuses into a refusal', async () => {
    await expect(postImportedSolvePlan('{}', respond({ detail: 'ingest_not_found' }, 422)))
      .rejects.toBeInstanceOf(SolvePlanRefused);
  });

  it('rejects a malformed plan rather than drawing it', async () => {
    await expect(postImportedSolvePlan('{}', respond({ ...PLAN, engines: [{ name: 'metal' }] })))
      .rejects.toThrow('Imported solve plan response is invalid');
  });
});

describe('declaresImportedGeometry', () => {
  const engine = (name: string, overrides: Partial<EngineCapability> = {}): EngineCapability => ({
    name, available: true, reason: null, version: null, fast_paths: [], ...overrides,
  });

  it('reads the declaration the server publishes', () => {
    expect(declaresImportedGeometry(engine('beat-cpu', { geometry_sources: ['parametric', 'imported'] }))).toBe(true);
    expect(declaresImportedGeometry(engine('bempp', { geometry_sources: ['parametric'] }))).toBe(false);
  });

  it('reads a payload without the field as the server that sent it behaved', () => {
    expect(declaresImportedGeometry(engine('metal'))).toBe(true);
    expect(declaresImportedGeometry(engine('bempp'))).toBe(false);
  });
});
