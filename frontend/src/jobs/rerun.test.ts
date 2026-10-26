import { describe, expect, it, vi } from 'vitest';
import { useSolveOptionsStore } from '../stores/solveOptions';
import { rerunWithPatch } from './rerun';

const effective = {
  geometry: { type: 'parametric', design: { formula: 'OSSE' }, design_revision: 7, design_snapshot: { version: 1, design: {} } },
  options: {
    engine: 'beat-cpu',
    accuracy: 'high',
    frequencies: [200, 400, 800],
    polar_config: { distance: 3.5, angle_step: 10, spherical_sampling: false, observation_origin: 'mouth' },
  },
  label: 'old',
  client_request_id: 'old-id',
};

function fetcher() {
  return vi.fn(async (url: RequestInfo | URL) => {
    if (String(url).endsWith('/requests')) return new Response(JSON.stringify({ effective_request: effective, execution_request: { options: { engine: 'other' } } }));
    if (String(url) === '/api/solver-mesh') return new Response(JSON.stringify({ msh_text: 'mesh', stats: { mesh_cache_key: 'key', triangle_count: 4 }, cut_planes: [] }));
    return new Response(JSON.stringify({ job_id: 'job-new' }));
  });
}

describe('rerunWithPatch', () => {
  it('keeps the old run settings, patches only spherical sampling and leaves stored preferences alone', async () => {
    useSolveOptionsStore.getState().updatePolar({ distance: 9 });
    const editorBefore = JSON.stringify(useSolveOptionsStore.getState().options());
    const writes = vi.fn();
    const unsubscribe = useSolveOptionsStore.subscribe(writes);
    const f = fetcher();

    const id = await rerunWithPatch('job-old', { options: { polar_config: { spherical_sampling: true } } }, {
      fetcher: f as unknown as typeof fetch, newId: () => 'fresh-id',
    });

    expect(id).toBe('job-new');
    expect(f.mock.calls[0][0]).toBe('/api/jobs/job-old/requests');
    expect(f.mock.calls[1][0]).toBe('/api/solver-mesh');
    const [, init] = f.mock.calls[2] as unknown as [string, RequestInit];
    const body = JSON.parse(String(init.body));
    expect(body.options.engine).toBe('beat-cpu');
    expect(body.options.accuracy).toBe('high');
    expect(body.options.frequencies).toEqual([200, 400, 800]);
    expect(body.options.polar_config).toEqual({ ...effective.options.polar_config, spherical_sampling: true });
    expect(body.geometry).toEqual(effective.geometry);
    expect(body.client_request_id).toBe('fresh-id');
    expect(body.label).toBe('old (rerun)');
    expect(body.parent_job_id).toBe('job-old');
    expect(JSON.stringify(useSolveOptionsStore.getState().options())).toBe(editorBefore);
    expect(useSolveOptionsStore.getState().polar.distance).toBe(9);
    unsubscribe();
    expect(writes).not.toHaveBeenCalled();
  });

  it('does not submit a changed mesh before the triangle choice', async () => {
    const f = vi.fn(async (url: RequestInfo | URL) => String(url).endsWith('/requests')
      ? new Response(JSON.stringify({ effective_request: effective }))
      : new Response(JSON.stringify({ detail: { code: 'mesh_too_large', triangles: 22001, limit: 22000,
        mesh_cache_key: 'a'.repeat(64) } }), { status: 409 }));
    await expect(rerunWithPatch('job-old', { options: { symmetry: 'full' } }, { fetcher: f as unknown as typeof fetch }))
      .rejects.toMatchObject({ name: 'SolverMeshTooLarge', meshCacheKey: 'a'.repeat(64) });
    expect(f.mock.calls.map(([url]) => String(url))).toEqual(['/api/jobs/job-old/requests', '/api/solver-mesh']);
  });

  it('does not submit if the selection changes during mesh preparation', async () => {
    const f = fetcher();
    await expect(rerunWithPatch('job-old', {}, {
      fetcher: f as unknown as typeof fetch, beforeSubmit: () => false,
    })).resolves.toBeNull();
    expect(f.mock.calls.map(([url]) => String(url))).toEqual(['/api/jobs/job-old/requests', '/api/solver-mesh']);
  });

  it('refuses a run whose request cannot be read', async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ detail: 'gone' }), { status: 409 }));
    await expect(rerunWithPatch('x', {}, { fetcher: f as unknown as typeof fetch })).rejects.toThrow('gone');
  });

  it('refuses imported geometry, which belongs to the retry and model-solve guards', async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({
      effective_request: { ...effective, geometry: { type: 'imported', ingest_id: 'i1' } },
    })));
    await expect(rerunWithPatch('job-cad', {}, { fetcher: f as unknown as typeof fetch })).rejects.toThrow(/parametric/);
    expect(f).toHaveBeenCalledTimes(1);
  });
});
