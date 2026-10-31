import { describe, expect, it } from 'vitest';
import { designForFamily } from '../stores/design';
import { postSolverMesh, SolverMeshTooLarge, solverMeshDesignKey, type SolverMeshResult } from './solverMesh';

const result: SolverMeshResult = {
  plan: { engine: 'metal', formulation: 'full-3d', reason: 'test', eligibility_reasons: [] },
  msh_text: 'transport artifact',
  stats: {
    triangle_count: 12,
    vertex_count: 9,
    warnings: [],
    mesh_cache_key: 'abc123',
    mesh_cache_hit: false,
  },
  cut_planes: ['x0', 'y0'],
  quadrants: 1,
};

function fetcherReturning(body: unknown, ok = true, status = 200): typeof fetch {
  return async () => ({
    ok,
    status,
    statusText: ok ? 'OK' : 'Unprocessable Entity',
    json: async () => body,
  }) as Response;
}

describe('postSolverMesh', () => {
  it('returns a validated result and posts the given body verbatim', async () => {
    let sent: RequestInit | undefined;
    const fetcher: typeof fetch = async (_input, init) => {
      sent = init;
      return {
        ok: true, status: 200, statusText: 'OK', json: async () => result,
      } as Response;
    };
    const controller = new AbortController();
    const received = await postSolverMesh('{"design":{}}', fetcher, controller.signal);
    expect(received.stats.mesh_cache_key).toBe('abc123');
    expect(sent?.method).toBe('POST');
    expect(sent?.body).toBe('{"design":{}}');
    expect(sent?.signal).toBe(controller.signal);
  });

  it('rejects a malformed payload rather than rendering from it', async () => {
    await expect(postSolverMesh('{}', fetcherReturning({ msh_text: 'x' })))
      .rejects.toThrow('Solver mesh response is invalid');
  });

  it('surfaces the server refusal detail', async () => {
    await expect(postSolverMesh(
      '{}',
      fetcherReturning({ detail: 'Solver mesh exceeds the ceiling' }, false, 422),
    )).rejects.toThrow('Solver mesh exceeds the ceiling');
  });

  it('retains a structured non-ceiling 409 detail after checking a cloned body', async () => {
    const response = Response.json({ detail: [{ loc: ['body', 'design'], msg: 'Value error, refused' }] }, { status: 409 });
    await expect(postSolverMesh('{}', async () => response)).rejects.toThrow('body.design: Value error, refused');
    expect(response.bodyUsed).toBe(true);
  });

  it('keeps the status fallback when a refusal body is unreadable', async () => {
    const response = new Response('unreadable', { status: 422, statusText: 'Unprocessable Entity' });
    await expect(postSolverMesh('{}', async () => response)).rejects.toThrow('422 Unprocessable Entity');
    expect(response.bodyUsed).toBe(true);
  });

  it('propagates a fetch abort without replacing its identity', async () => {
    const error = new DOMException('Cancelled', 'AbortError');
    await expect(postSolverMesh('{}', async () => { throw error; })).rejects.toBe(error);
  });

  it('keeps the measured triangle limit for the shared solve choice', async () => {
    const fetcher: typeof fetch = async () => new Response(JSON.stringify({ detail: {
      code: 'mesh_too_large', triangles: 22001, limit: 22000, mesh_cache_key: 'a'.repeat(64),
    } }), { status: 409, headers: { 'Content-Type': 'application/json' } });
    await expect(postSolverMesh('{}', fetcher)).rejects.toMatchObject({
      name: 'SolverMeshTooLarge', triangles: 22001, limit: 22000, meshCacheKey: 'a'.repeat(64),
    } satisfies Partial<SolverMeshTooLarge>);
  });
});

describe('solverMeshDesignKey', () => {
  it('ignores sampling edits while retaining geometry and non-polar extra blocks', () => {
    const design = designForFamily('OSSE');
    const key = solverMeshDesignKey(design);
    Object.assign(design.simulation, { f1: 50, f2: 20_000, num_frequencies: 80 });
    design.extra_blocks['ABEC.Polars:test'] = { items: {}, lines: ['sampling'] };
    expect(solverMeshDesignKey(design)).toBe(key);
    design.extra_blocks['Report'] = { items: { Title: 'mesh' }, lines: [] };
    expect(solverMeshDesignKey(design)).not.toBe(key);
    delete design.extra_blocks['Report'];
    design.mesh.throat_resolution *= 2;
    expect(solverMeshDesignKey(design)).not.toBe(key);
  });
});
