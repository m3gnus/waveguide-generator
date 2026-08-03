import { describe, expect, it } from 'vitest';
import { designForFamily } from '../stores/design';
import type { SolveOptions } from '../stores/solveOptions';
import { formatApiDetail, getCapabilities, resolveEngine, submitDesign } from './actions';

describe('API validation errors', () => {
  it('formats structured FastAPI detail arrays with locations', async () => {
    const detail = [{ loc: ['body', 'design', 'simulation', 'f1'], msg: 'must be finite', type: 'value_error' }];
    expect(formatApiDetail(detail)).toBe('body.design.simulation.f1: must be finite');
    const fetcher = async () => new Response(JSON.stringify({ detail }), {
      status: 422, headers: { 'Content-Type': 'application/json' },
    });
    await expect(getCapabilities(fetcher as typeof fetch)).rejects.toThrow('body.design.simulation.f1: must be finite');
  });
});

describe('solve submission', () => {
  it('mirrors G1 AUTO engine preference and CircSym compatibility', () => {
    const capabilities = { engines: [
      { name: 'dryrun', available: true, reason: null, version: 'builtin' },
      { name: 'metal', available: true, reason: null, version: '1' },
      { name: 'circsym', available: true, reason: null, version: '1' },
    ] };
    expect(resolveEngine('auto', capabilities)).toBe('metal');
    expect(resolveEngine('auto', capabilities, 'circsym')).toBe('circsym');
  });

  it('submits every solve option and the G1 polar_config contract without forcing dryrun', async () => {
    const options: SolveOptions = {
      engine: 'auto', mesh_validation_mode: 'strict', verbose: true, frequency_spacing: 'linear',
      polar_config: { angle_range: [0, 90, 10], distance: 3, norm_angle: 7, inclination: 30, enabled_axes: ['horizontal', 'diagonal'], observation_origin: 'throat', spherical_sampling: true },
    };
    let body: Record<string, unknown> | undefined;
    const fetcher = async (_input: RequestInfo | URL, init?: RequestInit) => {
      body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return new Response(JSON.stringify({ job_id: 'job-1' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    };
    await expect(submitDesign(designForFamily('R-OSSE'), options, fetcher as typeof fetch)).resolves.toBe('job-1');
    expect(body?.options).toEqual(options);
  });
});
