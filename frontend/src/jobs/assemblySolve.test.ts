import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mesh } from '../shell/cadlink/selection.fixtures';
import { FRONT_CONTEXT_LIMIT_MS } from '../api/assembly';
import { addWoofer, emptyAssembly, setDriver, setWooferDrive } from '../stores/assembly';
import { assemblyContentKey, resetAssemblyBuild } from '../stores/assemblyBuild';
import { currentDocumentLoad, designForFamily, resetDesignStore, serializeDesign, useDesignStore } from '../stores/design';
import { adoptDesignBuild } from '../stores/importedModel';
import { designAssemblySnapshot, NEEDS_BUILD, prepareDesignAssembly } from './assemblySolve';
import { modelSolvePress, planSubmission } from './importedSubmission';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { solveModel } from './actions';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';

let counter = 0;
const ids = () => `00000000-0000-4000-8000-${String(counter += 1).padStart(12, '0')}`;

describe('assembly Solve press', () => {
  beforeEach(() => { counter = 0; resetDocumentStore(); resetDesignStore(); resetAssemblyBuild(); resetSolveOptionsStore(); });
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
    const pressed = prepareDesignAssembly(fetcher);
    // The user edits while the check is on its way.
    useDesignStore.getState().updateValue('simulation.f1', 200);
    answer(new Response(JSON.stringify({ minimum_clearance_mm: 0.1, clearances: {} }), { status: 200 }));
    await expect(pressed).rejects.toThrow('The design changed while it was being prepared');
    expect(asked).toEqual(['/api/models/assembly/context']);
  });

  it('requires a current build for a synchronous snapshot', () => {
    useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly: emptyAssembly(ids) });
    expect(designAssemblySnapshot).toThrow(NEEDS_BUILD);
  });

  it.each(['context', 'compile'].flatMap(stage => ['uid', 'load', 'revision'].map(identity => [stage, identity])))
  ('fences %s completion against a changed document %s', async (stage, identity) => {
    useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly: emptyAssembly(ids) });
    let answer!: (response: Response) => void;
    const fetcher = vi.fn<typeof fetch>(async url => {
      if (String(url).endsWith(`/${stage}`)) return new Promise<Response>(resolve => { answer = resolve; });
      return Response.json({ minimum_clearance_mm: 0.1, clearances: {} });
    });
    const pressed = prepareDesignAssembly(fetcher);
    await vi.waitFor(() => expect(answer).toBeTypeOf('function'));
    if (identity === 'uid') useDocumentStore.setState({ designUid: 'wgu_another' });
    else if (identity === 'revision') useDesignStore.getState().updateValue('simulation.f1', 200);
    else {
      const { design, designRevision } = useDesignStore.getState();
      useDesignStore.getState().replaceDocument(structuredClone(design));
      useDesignStore.setState({ designRevision });
    }
    answer(Response.json(stage === 'context' ? { minimum_clearance_mm: 0.1, clearances: {} } : { status: 'superseded' }));
    await expect(pressed).rejects.toThrow('The design changed while it was being prepared. Press Solve again.');
    expect(fetcher).toHaveBeenCalledTimes(stage === 'context' ? 1 : 2);
  });

  it('refuses an unfinished woofer driver before checking or building anything', async () => {
    const { assembly, id } = addWoofer(emptyAssembly(ids), { anchor: 'below' }, ids);
    useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly: setWooferDrive(assembly, id, 'driver') });
    useSolveOptionsStore.getState().setEngine('beat-cpu');
    const fetcher = vi.fn() as unknown as typeof fetch;
    await expect(prepareDesignAssembly(fetcher)).rejects.toThrow(
      'Woofer 1 needs its driver: Enter resistance in the stated units. Pick one from the library or enter its values under Drivers in Simulation, or set the woofer to Fixed velocity.');
    expect(fetcher).not.toHaveBeenCalled();
    useSolveOptionsStore.getState().setAdaptiveFrequencySampling(true);
    useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly: setDriver(assembly, id, { exterior_transducer: {
      version: 1, re_ohm: 6, le_h: 0.0005, bl_n_per_a: 7, mmd_kg: 0.02, cms_m_per_n: 0.001, rms_n_s_per_m: 1.5 } }) });
    await expect(prepareDesignAssembly(fetcher)).rejects.toThrow('Woofer drivers solve without adaptive sampling and the ground plane.');
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
    await expect(prepareDesignAssembly(fetcher)).rejects.toThrow('Woofer 1 overlaps the edge of the baffle by 30 mm.');
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
    const pressed = prepareDesignAssembly(fetcher);
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
    await expect(prepareDesignAssembly(fetcher)).rejects.toThrow('Set an enclosure depth.');
    expect(asked).toEqual(['/api/models/assembly/context']);
  });

  it.each(['beat-cpu', 'beat-cuda', 'beat-rocm', 'bempp'])('presses the model Solve with the assembly setup and supported %s accuracy', async (engine) => {
    // Retain the saved-piston solve contract alongside new contoured woofers.
    const { assembly, id } = addWoofer(emptyAssembly(ids), { anchor: 'below', shape: { kind: 'piston' } }, ids);
    useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly });
    const design = useDesignStore.getState().design;
    adoptDesignBuild({
      model: { model_id: 'design-assembly:wgu_x', producer: 'design-assembly', display_name: 'x' },
      record: { ingest_id: 'wgi_9', producer: 'design-assembly', model_id: 'design-assembly:wgu_x', manifest_sha256: 'm', artifact_sha256: 'a',
        msh_text: mesh, design: { design_uid: 'wgu_x', name: 'x', revision: 1, content_sha256: '', assembly_sha256: '', geometry_sha256: '' },
        radiators: [], signal_groups: [], source_tags: {},
        sources: [{ id: 'horn-throat', role: 'HF', radiator_id: 'horn-throat', segment_id: null }, { id: `${id}/piston`, role: 'LF', radiator_id: id, segment_id: 'piston' }],
        excitations: [{ id: 'horn-throat', source_ids: ['horn-throat'], motion: 'normal', patch_weights: {} },
          { id, source_ids: [`${id}/piston`], motion: 'axial', patch_weights: {} }] } },
      { designUid: 'wgu_x', revision: 1, contentKey: assemblyContentKey(design), load: currentDocumentLoad() });
    useSolveOptionsStore.getState().setAccuracy('accurate');
    useSolveOptionsStore.getState().setEngine(engine);
    const snapshot = designAssemblySnapshot();
    const prepared = { ...snapshot, press: modelSolvePress(snapshot, 'Speaker1', 'p1') };
    expect(planSubmission(snapshot).options).toEqual(snapshot.setup.options);
    expect(planSubmission(snapshot).geometry.ingest_id).toBe(prepared.press.prepared_id);
    expect(prepared.press.label).toBe('Speaker1');
    const { label: _label, ...legacyPress } = prepared.press;
    expect(JSON.stringify(legacyPress)).toMatchSnapshot();
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
    const source_ids = [`${id}/cap`, `${id}/cone`];
    const record = { msh_text: mesh, source_tags: {}, ingest_id: 'wgi_9', model_id: 'design-assembly:wgu_x', sources: [{ id: 'horn-throat', role: 'HF' }, ...source_ids.map(id => ({ id, role: 'LF' }))],
      excitations: [{ id: 'horn-throat', source_ids: ['horn-throat'], motion: 'normal' }, { id, source_ids, motion: 'axial' }] };
    const fetcher = (async (url: string) => new Response(JSON.stringify(url.endsWith('/context')
      ? { minimum_clearance_mm: 0.1, clearances: {} }
      : { status: 'published', prepared: record, model: { model_id: 'design-assembly:wgu_x', producer: 'design-assembly', display_name: 'x' } }), { status: 200 })) as unknown as typeof fetch;
    const press = modelSolvePress(await prepareDesignAssembly(fetcher), 'Speaker1', 'p1');
    const geometry = (press.setup as unknown as { geometry: { drive_channels: Array<{ id: string; source_ids: string[] }> } }).geometry;
    expect(geometry.drive_channels.map(channel => channel.id)).toEqual(['horn-throat', id]);
    expect(geometry.drive_channels[1].source_ids).toEqual(source_ids);
    // The run keeps the design it was pressed from, so Results can tie it to this design.
    expect(press.design).toEqual(serializeDesign(useDesignStore.getState().design));
  });

  it('refuses a design without an assembly', async () => {
    useDesignStore.getState().replaceDocument(designForFamily('R-OSSE'));
    await expect(prepareDesignAssembly(fetch)).rejects.toThrow('no Sources and baffle assembly');
  });
});
