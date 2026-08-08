import { describe, expect, it } from 'vitest';
import { hydrateDesignDocument } from '../api/designIo';
import { designForFamily, serializeDesign } from '../stores/design';
import type { SolveOptions } from '../stores/solveOptions';
import { fetchSymmetry, formatApiDetail, getCapabilities, postSymmetry, resolveEngine, submitDesign, toSolveDesign } from './actions';

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
  it('keeps AUTO on real backends even when solver mode forces the Metal fast path', () => {
    const capabilities = { engines: [
      { name: 'dryrun', available: true, reason: null, version: 'builtin', fast_paths: [] },
      { name: 'metal', available: true, reason: null, version: '1', fast_paths: ['axisymmetric-meridian'] },
    ] };
    expect(resolveEngine('auto', capabilities)).toBe('metal');
    expect(resolveEngine('auto', capabilities, 'circsym')).toBe('metal');
  });

  it('submits every solve option and the G1 polar_config contract without forcing dryrun', async () => {
    const options: SolveOptions = {
      engine: 'auto', symmetry: 'quarter', mesh_validation_mode: 'strict', verbose: true, frequency_spacing: 'linear',
      polar_config: { angle_range: [0, 90, 10], angle_step: 10, distance: 3, norm_angle: 7, inclination: 30, enabled_axes: ['horizontal', 'diagonal'], observation_origin: 'throat', spherical_sampling: true },
    };
    let body: Record<string, unknown> | undefined;
    const fetcher = async (_input: RequestInfo | URL, init?: RequestInit) => {
      body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return new Response(JSON.stringify({ job_id: 'job-1' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    };
    await expect(submitDesign(designForFamily('R-OSSE'), options, fetcher as typeof fetch, { label: 'atomic', designRevision: 19 })).resolves.toBe('job-1');
    expect(body?.options).toEqual(options);
    expect(body).toMatchObject({ label: 'atomic', design_revision: 19, design_snapshot: { version: 1 } });
    expect((body?.design_snapshot as { design: unknown }).design).toEqual(body?.design);
  });

  it('submits the sweep displayed for an imported design whose sweep controls were absent', async () => {
    const design = hydrateDesignDocument({
      formula: 'R-OSSE',
      simulation: { f1: null, f2: 16_000, num_frequencies: null },
    });
    const savedSimulation = serializeDesign(design).simulation as Record<string, unknown>;
    expect(savedSimulation).toMatchObject({ f1: null, f2: 16_000, num_frequencies: null });

    let body: Record<string, unknown> | undefined;
    const fetcher = async (_input: RequestInfo | URL, init?: RequestInit) => {
      body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return new Response(JSON.stringify({ job_id: 'job-sweep' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    };
    const options: SolveOptions = {
      engine: 'auto', symmetry: 'auto', mesh_validation_mode: 'warn', verbose: false, frequency_spacing: 'log',
      polar_config: { angle_range: [0, 180, 37], angle_step: 5, distance: 2, norm_angle: 5, inclination: 45, enabled_axes: ['horizontal'], observation_origin: 'mouth', spherical_sampling: false },
    };
    await submitDesign(design, options, fetcher as typeof fetch);

    const submittedSimulation = (body?.design as { simulation: Record<string, unknown> }).simulation;
    expect(submittedSimulation).toMatchObject({
      f1: design.simulation.f1,
      f2: design.simulation.f2,
      num_frequencies: design.simulation.num_frequencies,
    });
    expect(((body?.design_snapshot as { design: unknown }).design as { simulation: unknown }).simulation).toEqual(submittedSimulation);
  });

  it('posts the canonical design to the live symmetry resolver', async () => {
    let path = '';
    let body: Record<string, unknown> | undefined;
    const fetcher = async (input: RequestInfo | URL, init?: RequestInit) => {
      path = String(input);
      body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return new Response(JSON.stringify({ quadrants: 1, xz: true, yz: true, reasons: { xz: [], yz: [] }, tolerance_mm: 0.01, relative_tolerance: 0.0002 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    };
    await expect(fetchSymmetry(designForFamily('R-OSSE'), fetcher as typeof fetch)).resolves.toMatchObject({ quadrants: 1, xz: true, yz: true });
    expect(path).toBe('/api/design/symmetry');
    expect(body?.formula).toBe('R-OSSE');
  });
});

describe('symmetry resolution', () => {
  it('sends the caller\'s exact bytes and forwards the abort signal', async () => {
    // Callers key their cache on the serialised payload, so re-serialising the
    // live design here would race an edit landing between keying and sending.
    const body = JSON.stringify({ formula: 'OSSE', marker: 'exact-bytes' });
    const controller = new AbortController();
    let seen: RequestInit | undefined;
    const fetcher = async (_input: RequestInfo | URL, init?: RequestInit) => {
      seen = init;
      return new Response(JSON.stringify({ quadrants: 14, xz: false, yz: true, reasons: { xz: [], yz: [] }, tolerance_mm: 1e-7, relative_tolerance: 2e-4 }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    };
    const resolution = await postSymmetry(body, fetcher as typeof fetch, controller.signal);
    expect(seen?.body).toBe(body);
    expect(seen?.signal).toBe(controller.signal);
    expect(resolution.quadrants).toBe(14);
  });

  it('keeps fetchSymmetry serialising the solve design for its callers', async () => {
    let body: unknown;
    const fetcher = async (_input: RequestInfo | URL, init?: RequestInit) => {
      body = JSON.parse(String(init?.body));
      return new Response(JSON.stringify({ quadrants: 1234, xz: false, yz: false, reasons: { xz: ['a'], yz: ['b'] }, tolerance_mm: 1e-7, relative_tolerance: 2e-4 }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    };
    const design = designForFamily('R-OSSE');
    await fetchSymmetry(design, fetcher as typeof fetch);
    expect(body).toEqual(toSolveDesign(design));
  });
});
