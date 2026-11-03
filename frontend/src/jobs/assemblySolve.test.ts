import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FRONT_CONTEXT_LIMIT_MS } from '../api/assembly';
import { addWoofer, emptyAssembly, setDriver, setWooferDrive } from '../stores/assembly';
import { assemblyContentKey, resetAssemblyBuild, useAssemblyBuildStore } from '../stores/assemblyBuild';
import { designForFamily, resetDesignStore, serializeDesign, useDesignStore } from '../stores/design';
import { prepareAssemblySolve } from './assemblySolve';
import { solveModel } from './actions';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';

let counter = 0;
const ids = () => `00000000-0000-4000-8000-${String(counter += 1).padStart(12, '0')}`;

describe('assembly Solve press', () => {
  beforeEach(() => { counter = 0; resetDesignStore(); resetAssemblyBuild(); resetSolveOptionsStore(); });
  afterEach(() => { vi.useRealTimers(); });

  it('binds the check, the build and the press to one revision', async () => {
    const { assembly } = addWoofer(emptyAssembly(ids), { anchor: 'below' }, ids);
    useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly });
    const asked: string[] = [];
    let answer!: (response: Response) => void;
    const fetcher = ((url: string) => {
      asked.push(url);
      return url.endsWith('/context') ? new Promise<Response>(resolve => { answer = resolve; })
        : Promise.resolve(new Response(JSON.stringify({ status: 'superseded' }), { status: 200 }));
    }) as unknown as typeof fetch;
    const pressed = prepareAssemblySolve(fetcher, () => 'p1');
    // The user edits while the check is on its way.
    useDesignStore.getState().updateValue('simulation.f1', 200);
    answer(new Response(JSON.stringify({ minimum_clearance_mm: 0.1, clearances: {} }), { status: 200 }));
    await expect(pressed).rejects.toThrow('The design changed while it was being prepared');
    expect(asked).toEqual(['/api/models/assembly/context']);
  });

  it('refuses an unfinished woofer driver before checking or building anything', async () => {
    const { assembly, id } = addWoofer(emptyAssembly(ids), { anchor: 'below' }, ids);
    useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly: setWooferDrive(assembly, id, 'driver') });
    useSolveOptionsStore.getState().setEngine('beat-cpu');
    const fetcher = vi.fn() as unknown as typeof fetch;
    await expect(prepareAssemblySolve(fetcher, () => 'p1')).rejects.toThrow(
      'Woofer 1 needs its driver: Enter resistance in the stated units. Pick one from the library or enter its values under Drivers in Simulation, or set the woofer to Fixed velocity.');
    expect(fetcher).not.toHaveBeenCalled();
    useSolveOptionsStore.getState().setAdaptiveFrequencySampling(true);
    useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly: setDriver(assembly, id, { exterior_transducer: {
      version: 1, re_ohm: 6, le_h: 0.0005, bl_n_per_a: 7, mmd_kg: 0.02, cms_m_per_n: 0.001, rms_n_s_per_m: 1.5 } }) });
    await expect(prepareAssemblySolve(fetcher, () => 'p1')).rejects.toThrow('Woofer drivers solve without adaptive sampling and the ground plane.');
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('never builds a woofer that does not fit, wherever Solve was pressed', async () => {
    const { assembly, id } = addWoofer(emptyAssembly(ids), { anchor: 'below' }, ids);
    useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly });
    const asked: string[] = [];
    const fetcher = (async (url: string) => {
      asked.push(url);
      return new Response(JSON.stringify({ minimum_clearance_mm: 0.1, clearances: { [id]: { mouth_mm: 10, baffle_edge_mm: -30, woofers: {} } } }), { status: 200 });
    }) as unknown as typeof fetch;
    await expect(prepareAssemblySolve(fetcher, () => 'p1')).rejects.toThrow('Woofer 1 overlaps the edge of the baffle by 30 mm.');
    expect(asked).toEqual(['/api/models/assembly/context']);
  });

  it('leaves the fit to the build when the check never answers, and shows what the build refuses', async () => {
    vi.useFakeTimers();
    const { assembly } = addWoofer(emptyAssembly(ids), { anchor: 'below' }, ids);
    useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly });
    const asked: string[] = [];
    const fetcher = ((url: string) => {
      asked.push(url);
      return url.endsWith('/context') ? new Promise<Response>(() => {})
        : Promise.resolve(new Response(JSON.stringify({ error: { code: 'assembly_refused', message: 'A woofer opening is too close to the horn mouth or baffle edge.' } }), { status: 422 }));
    }) as unknown as typeof fetch;
    const pressed = prepareAssemblySolve(fetcher, () => 'p1');
    const settled = expect(pressed).rejects.toThrow('too close to the horn mouth');
    await vi.advanceTimersByTimeAsync(FRONT_CONTEXT_LIMIT_MS);
    await settled;
    expect(asked).toEqual(['/api/models/assembly/context', '/api/models/assembly/compile']);
  });

  it('does not build a design the check refuses', async () => {
    const { assembly } = addWoofer(emptyAssembly(ids), { anchor: 'below' }, ids);
    useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly });
    const asked: string[] = [];
    const fetcher = (async (url: string) => {
      asked.push(url);
      return new Response(JSON.stringify({ error: { code: 'assembly_refused', message: 'Set an enclosure depth.' } }), { status: 422 });
    }) as unknown as typeof fetch;
    await expect(prepareAssemblySolve(fetcher, () => 'p1')).rejects.toThrow('Set an enclosure depth.');
    expect(asked).toEqual(['/api/models/assembly/context']);
  });

  it.each(['beat-cpu', 'beat-cuda', 'beat-rocm', 'bempp'])('presses the model Solve with the assembly setup and supported %s accuracy', async (engine) => {
    const { assembly, id } = addWoofer(emptyAssembly(ids), { anchor: 'below' }, ids);
    useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly });
    const design = useDesignStore.getState().design;
    useAssemblyBuildStore.setState({ status: 'published', published: { revision: 1, contentKey: assemblyContentKey(design),
      model: { model_id: 'design-assembly:wgu_x', producer: 'design-assembly', display_name: 'x' },
      record: { ingest_id: 'wgi_9', producer: 'design-assembly', model_id: 'design-assembly:wgu_x', manifest_sha256: 'm', artifact_sha256: 'a',
        msh_text: '', design: { design_uid: 'wgu_x', name: 'x', revision: 1, content_sha256: '', assembly_sha256: '', geometry_sha256: '' },
        radiators: [], signal_groups: [], source_tags: {},
        sources: [{ id: 'horn-throat', role: 'HF', radiator_id: 'horn-throat', segment_id: null }, { id: `${id}/piston`, role: 'LF', radiator_id: id, segment_id: 'piston' }],
        excitations: [{ id: 'horn-throat', source_ids: ['horn-throat'], motion: 'normal', patch_weights: {} },
          { id, source_ids: [`${id}/piston`], motion: 'axial', patch_weights: {} }] } } });
    useSolveOptionsStore.getState().setAccuracy('accurate');
    useSolveOptionsStore.getState().setEngine(engine);
    const prepared = await prepareAssemblySolve(fetch, () => 'p1');
    expect(prepared.modelId).toBe('design-assembly:wgu_x');
    expect(prepared.notice).toBeNull();
    const body = prepared.press as unknown as { prepared_id: string; client_request_id: string; setup: { schema_version: number; geometry: Record<string, unknown> } };
    expect(body.prepared_id).toBe('wgi_9');
    expect(body.client_request_id).toBe('assembly-p1');
    expect(body.setup.schema_version).toBe(1);
    expect(body.setup.geometry.required_features).toEqual(['wg-design-assembly-v1']);
    expect((body.setup.geometry.drive_channels as Array<{ id: string }>).map(channel => channel.id)).toEqual(['horn-throat', id]);
    let submitted: { setup: { options: { engine: string; accuracy?: string } } } | undefined;
    await solveModel(prepared.modelId, prepared.press, async (input, init) => {
      expect(String(input)).toBe('/api/models/design-assembly%3Awgu_x/solve');
      submitted = JSON.parse(String(init?.body));
      return Response.json({ job_id: 'assembly-intent' });
    });
    expect(submitted?.setup.options.engine).toBe(engine);
    expect(submitted?.setup.options.accuracy).toBe(engine === 'bempp' ? undefined : 'accurate');
  });

  it('sends the channels of the build this press made, on a first Solve', async () => {
    const { assembly, id } = addWoofer(emptyAssembly(ids), { anchor: 'below' }, ids);
    useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly });
    const record = { ingest_id: 'wgi_9', model_id: 'design-assembly:wgu_x', sources: [{ id: 'horn-throat', role: 'HF' }, { id: `${id}/piston`, role: 'LF' }],
      excitations: [{ id: 'horn-throat', source_ids: ['horn-throat'], motion: 'normal' }, { id, source_ids: [`${id}/piston`], motion: 'axial' }] };
    const fetcher = (async (url: string) => new Response(JSON.stringify(url.endsWith('/context')
      ? { minimum_clearance_mm: 0.1, clearances: {} }
      : { status: 'published', prepared: record, model: { model_id: 'design-assembly:wgu_x', producer: 'design-assembly', display_name: 'x' } }), { status: 200 })) as unknown as typeof fetch;
    const { press } = await prepareAssemblySolve(fetcher, () => 'p1');
    const geometry = (press.setup as unknown as { geometry: { drive_channels: Array<{ id: string }> } }).geometry;
    expect(geometry.drive_channels.map(channel => channel.id)).toEqual(['horn-throat', id]);
    // The run keeps the design it was pressed from, so Results can tie it to this design.
    expect(press.design).toEqual(serializeDesign(useDesignStore.getState().design));
  });

  it('refuses a design without an assembly', async () => {
    useDesignStore.getState().replaceDocument(designForFamily('R-OSSE'));
    await expect(prepareAssemblySolve(fetch)).rejects.toThrow('no Sources and baffle assembly');
  });
});
