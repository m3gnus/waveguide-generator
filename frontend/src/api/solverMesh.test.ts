import { afterEach, describe, expect, it } from 'vitest';
import { activityStore } from '../shell/activity';
import { designForFamily } from '../stores/design';
import { readsPlain } from './httpError';
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

afterEach(() => activityStore.reset());

function fetcherReturning(body: unknown, ok = true, status = 200): typeof fetch {
  return async () => ({
    ok,
    status,
    statusText: ok ? 'OK' : 'Unprocessable Entity',
    json: async () => body,
  }) as Response;
}

describe('postSolverMesh', () => {
  it('requests the summary representation without changing the solve body', async () => {
    const { msh_text: _mesh, ...summary } = result;
    let sent: [RequestInfo | URL, RequestInit | undefined] | undefined;
    const fetcher: typeof fetch = async (url, init) => { sent = [url, init]; return Response.json(summary); };
    await expect(postSolverMesh('{"design":{}}', fetcher, undefined, 'summary')).resolves.toEqual(summary);
    expect(sent?.[0]).toBe('/api/solver-mesh?representation=summary');
    expect(sent?.[1]?.body).toBe('{"design":{}}');
    await expect(postSolverMesh('{}', async () => Response.json(summary))).rejects.toThrow('Solver mesh response is invalid');
  });

  it.each([422, 409])('preserves Stop during a deferred %s refusal body and clears activity', async status => {
    let reading!: () => void;
    let lifetime: AbortSignal | null | undefined;
    const bodyStarted = new Promise<void>(resolve => { reading = resolve; });
    const fetcher: typeof fetch = async (_url, init) => {
      lifetime = init?.signal;
      const decode = () => new Promise<never>((_resolve, reject) => {
        reading();
        if (init?.signal?.aborted) reject(init.signal.reason);
        else init?.signal?.addEventListener('abort', () => reject(init.signal!.reason), { once: true });
      });
      return { ok: false, status, json: decode, clone: () => ({ json: decode }) } as unknown as Response;
    };
    const pending = postSolverMesh('{}', fetcher).catch(error => error);
    await bodyStarted;
    expect(activityStore.getSnapshot()).toMatchObject([{ kind: 'solver-mesh', cancel: expect.any(Function) }]);
    activityStore.getSnapshot()[0].cancel!();
    const reason = await pending;
    expect(reason).toMatchObject({ name: 'AbortError' });
    expect(reason).toBe(lifetime?.reason);
    expect(activityStore.getSnapshot()).toHaveLength(0);
  });
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
    expect(sent?.signal).toBeInstanceOf(AbortSignal);
    expect(sent?.signal?.aborted).toBe(false);
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
    await expect(postSolverMesh('{}', async () => response)).rejects.toThrow('refused');
    expect(response.bodyUsed).toBe(true);
  });

  it('says one plain thing when a refusal body is unreadable', async () => {
    const response = new Response('unreadable', { status: 422, statusText: 'Unprocessable Entity' });
    await expect(postSolverMesh('{}', async () => response)).rejects.toThrow('WG could not build the solver mesh. Press Solve to try again.');
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
      message: 'This mesh has 22,001 triangles, above the limit of 22,000 for the part WG solves. Raise Max surface deviation or a mesh resolution in Simulation › Mesh, or press Solve to choose.',
    } satisfies Partial<SolverMeshTooLarge>);
    expect(readsPlain((await postSolverMesh('{}', fetcher).catch((error: Error) => error) as Error).message)).toBe(true);
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
