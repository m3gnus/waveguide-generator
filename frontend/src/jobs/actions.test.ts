import { describe, expect, it } from 'vitest';
import { hydrateDesignDocument } from '../api/designIo';
import { designForFamily, serializeDesign } from '../stores/design';
import type { SolveOptions } from '../stores/solveOptions';
import { fetchSymmetry, formatApiDetail, getCapabilities, plannedEngineNames, postSymmetry, resolveEngine, submitDesign, submitImported, toSolveDesign } from './actions';

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
  it('requires the advertised Axisym runner when solver mode forces the meridian path', () => {
    const capabilities = { engines: [
      { name: 'dryrun', available: true, reason: null, version: 'builtin', fast_paths: [] },
      { name: 'metal', available: true, reason: null, version: '1', fast_paths: ['axisymmetric-meridian'] },
    ], engineSelection: {
      default: 'auto', resolvedDefault: 'metal', full3dOrder: ['metal', 'dryrun'], axisymmetricRunner: 'axisym',
    } };
    expect(resolveEngine('auto', capabilities)).toBe('metal');
    expect(() => resolveEngine('auto', capabilities, 'circsym')).toThrow('requires the advertised axisym runner');
    expect(() => resolveEngine('metal', capabilities, 'circsym')).toThrow('requires the advertised axisym runner');

    capabilities.engines.push({
      name: 'axisym', available: true, reason: null, version: '1', fast_paths: ['axisymmetric-meridian'],
    });
    expect(resolveEngine('auto', capabilities, 'circsym')).toBe('axisym');
    expect(resolveEngine('metal', capabilities, 'circsym')).toBe('axisym');
    expect(() => resolveEngine('dryrun', capabilities, 'circsym')).toThrow('Dry-run cannot run forced Axisymmetric');
    expect(() => resolveEngine('stale-manual-engine', capabilities, 'circsym')).toThrow('Unknown solve engine');
    expect(() => resolveEngine('axisym', capabilities, 'full_3d')).toThrow("cannot run solver mode Full 3D");
    expect(resolveEngine('dryrun', capabilities, 'auto')).toBe('dryrun');
    expect(resolveEngine('metal', capabilities, 'full_3d')).toBe('metal');

    expect(plannedEngineNames('metal', capabilities, 'auto')).toEqual(['axisym', 'metal']);
    expect(plannedEngineNames('auto', capabilities, 'full_3d')).toEqual(['metal', 'dryrun']);
    expect(plannedEngineNames('metal', capabilities, 'circsym')).toEqual(['axisym']);
    expect(plannedEngineNames('dryrun', capabilities, 'auto')).toEqual(['dryrun']);

    const staleBeat = {
      engines: [
        { name: 'beat', available: false, reason: 'GPU offline', version: null, fast_paths: [], formulations: ['full-3d'] },
        { name: 'axisym', available: true, reason: null, version: '1', fast_paths: [], formulations: ['axisymmetric'] },
      ],
      engineSelection: {
        default: 'auto', resolvedDefault: null, full3dOrder: ['beat'], axisymmetricRunner: 'axisym',
      },
    };
    expect(resolveEngine('beat', staleBeat, 'auto')).toBe('beat');
    expect(plannedEngineNames('beat', staleBeat, 'auto')).toEqual(['axisym', 'beat']);
    expect(resolveEngine('beat', staleBeat, 'full_3d')).toBe('beat');
  });

  it('resolves AUTO to Axisym when it is the only engine the host has', () => {
    // The server planner routes an eligible circular design to the meridian
    // runner before any full-3D fallback, so throwing here blocked Run on a
    // design the server would have solved; the only escape was knowing to
    // force the meridian mode by hand.
    const axisymOnly = { engines: [
      { name: 'axisym', available: true, reason: null, version: '1', fast_paths: ['axisymmetric-meridian'] },
      { name: 'bempp', available: false, reason: 'not installed', version: null, fast_paths: [] },
    ], engineSelection: {
      default: 'auto', resolvedDefault: null, full3dOrder: ['bempp'], axisymmetricRunner: 'axisym',
    } };
    expect(resolveEngine('auto', axisymOnly)).toBe('axisym');
    expect(() => resolveEngine('auto', axisymOnly, 'full_3d')).toThrow('No full-3D solver backend');

    // A full-3D backend still wins when the host has one, and a host with
    // nothing available still refuses rather than inventing an engine.
    const withBempp = { engines: [
      ...axisymOnly.engines.slice(0, 1),
      { name: 'bempp', available: true, reason: null, version: '1', fast_paths: [] },
    ], engineSelection: {
      ...axisymOnly.engineSelection,
      resolvedDefault: 'bempp',
    } };
    expect(resolveEngine('auto', withBempp)).toBe('bempp');
    expect(() => resolveEngine('auto', { engines: [] })).toThrow('No solver backend is currently available');
    expect(() => resolveEngine('removed-engine', withBempp)).toThrow('Unknown solve engine');
  });

  it('submits every solve option and the G1 polar_config contract without forcing dryrun', async () => {
    const options: SolveOptions = {
      engine: 'auto', symmetry: 'quarter', mesh_validation_mode: 'strict', verbose: true, frequency_spacing: 'linear',
      polar_config: { angle_range: [0, 90, 10], angle_step: 10, distance: 3, norm_angle: 7, inclination: 30, enabled_axes: ['horizontal', 'diagonal'], observation_origin: 'throat', spherical_sampling: true, field_plane: false },
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

  it('submits the imported geometry union without a parametric design sibling', async () => {
    let body: Record<string, unknown> | undefined;
    const fetcher = async (_input: RequestInfo | URL, init?: RequestInit) => {
      body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return new Response(JSON.stringify({ job_id: 'job-imported' }), { status: 200 });
    };
    await submitImported({
      geometry: {
        type: 'imported', ingest_id: 'wgi_example', manifest_sha256: 'sha256:m', artifact_sha256: 'sha256:a',
        drive_channels: [{ id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' }],
        mesh: { rigid_size_mm: 8, transition_mm: 8, source_size_mm: { 'source-hf': 4 } },
        acknowledged_findings: ['sha256:r:finding-a'], skipped_source_ids: [],
      },
      options: {
        engine: 'metal', symmetry: 'auto', mesh_validation_mode: 'warn', verbose: false, frequency_spacing: 'log',
        frequency_range: [200, 20_000], num_frequencies: 24,
        polar_config: { angle_range: [0, 180, 37], angle_step: 5, distance: 2, norm_angle: 5, inclination: 45, enabled_axes: ['horizontal'], observation_origin: 'mouth', spherical_sampling: false, field_plane: true },
      },
    }, fetcher as typeof fetch, 'cad-run');
    expect(body).toMatchObject({ geometry: { type: 'imported', ingest_id: 'wgi_example' }, label: 'cad-run' });
    expect(body).not.toHaveProperty('design');
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
      polar_config: { angle_range: [0, 180, 37], angle_step: 5, distance: 2, norm_angle: 5, inclination: 45, enabled_axes: ['horizontal'], observation_origin: 'mouth', spherical_sampling: false, field_plane: true },
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
