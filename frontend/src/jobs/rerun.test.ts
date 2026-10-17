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
      fetcher: f as unknown as typeof fetch, newId: () => 'fresh-id', now: () => new Date('2026-10-17T12:00:00Z'),
    });

    expect(id).toBe('job-new');
    expect(f.mock.calls[0][0]).toBe('/api/jobs/job-old/requests');
    const [, init] = f.mock.calls[1] as unknown as [string, RequestInit];
    const body = JSON.parse(String(init.body));
    expect(body.options.engine).toBe('beat-cpu');
    expect(body.options.accuracy).toBe('high');
    expect(body.options.frequencies).toEqual([200, 400, 800]);
    expect(body.options.polar_config).toEqual({ ...effective.options.polar_config, spherical_sampling: true });
    expect(body.geometry).toEqual(effective.geometry);
    expect(body.client_request_id).toBe('fresh-id');
    expect(body.label).not.toBe('old');
    expect(JSON.stringify(useSolveOptionsStore.getState().options())).toBe(editorBefore);
    expect(useSolveOptionsStore.getState().polar.distance).toBe(9);
    unsubscribe();
    expect(writes).not.toHaveBeenCalled();
  });

  it('refuses a run whose request cannot be read', async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ detail: 'gone' }), { status: 409 }));
    await expect(rerunWithPatch('x', {}, { fetcher: f as unknown as typeof fetch })).rejects.toThrow('gone');
  });
});
