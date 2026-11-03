import * as httpError from '../api/httpError';
import { activityStore } from '../shell/activity';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { addWoofer, commitAssembly, emptyAssembly, setPlacement } from './assembly';
import {
  designModelCurrent, assemblyPreviewLabel, prepareDesignModel, requestAssemblyBuild, resetAssemblyBuild, useAssemblyBuildStore,
} from './assemblyBuild';
import { designForFamily, resetDesignStore, useDesignStore } from './design';
import { useDesignModelStore } from './importedModel';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { mesh } from '../shell/cadlink/selection.fixtures';

let counter = 0;
const ids = () => `00000000-0000-4000-8000-${String(counter += 1).padStart(12, '0')}`;

function deferredFetcher() {
  const pending: Array<{ body: Record<string, unknown>; resolve: (value: Response) => void }> = [];
  const fetcher = ((_url: RequestInfo | URL, init?: RequestInit) => new Promise<Response>(resolve => {
    pending.push({ body: JSON.parse(String(init?.body)), resolve });
  })) as typeof fetch;
  const answer = (index: number, prepared: Record<string, unknown> | null, status = 200) => pending[index].resolve(new Response(
    JSON.stringify(status !== 200 ? { error: { code: 'assembly_refused', message: 'Set an enclosure depth.' } }
      : prepared ? { status: 'published', reused: false, model: { model_id: 'design-assembly:x', producer: 'design-assembly', display_name: 'x' }, prepared }
        : { status: 'superseded' }), { status }));
  return { fetcher, pending, answer };
}

const record = (id: string) => ({ ingest_id: id, producer: 'design-assembly', radiators: [], sources: [], source_tags: {}, msh_text: mesh });

describe('assembly background build', () => {
  beforeEach(() => {
    activityStore.reset();
    counter = 0;
    resetDesignStore();
    useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), enclosure: { ...designForFamily('R-OSSE').enclosure, depth: 200 } });
    resetAssemblyBuild();
    commitAssembly(addWoofer(emptyAssembly(ids), { anchor: 'below' }, ids).assembly);
  });

  it.each([200, 422])('registers one shared build through success or failure (%s)', async status => {
    const { fetcher, answer } = deferredFetcher();
    const build = requestAssemblyBuild(fetcher);
    expect(activityStore.getSnapshot()).toMatchObject([{ kind: 'assembly-build', label: 'Building the mesh', cancel: expect.any(Function) }]);
    const settled = status === 200 ? build : expect(build).rejects.toThrow('Set an enclosure depth.');
    answer(0, status === 200 ? record('activity') : null, status);
    await settled;
    expect(activityStore.getSnapshot()).toHaveLength(0);
    expect(activityStore.typicalMs('assembly-build') === null).toBe(status !== 200);
  });

  it('Stop restores the entire previous build state and keeps the prepared model', async () => {
    useAssemblyBuildStore.setState({ status: 'refused', refusal: 'Previous refusal', failureDetail: 'Previous detail' });
    const before = useAssemblyBuildStore.getState();
    const model = useDesignModelStore.getState();
    let signal: AbortSignal | null | undefined;
    const fetcher: typeof fetch = (_url, init) => new Promise((_resolve, reject) => {
      signal = init?.signal;
      signal?.addEventListener('abort', () => reject(signal?.reason), { once: true });
    });
    const classify = vi.spyOn(httpError, 'isAbortError');
    const build = requestAssemblyBuild(fetcher);
    expect(useAssemblyBuildStore.getState().status).toBe('building');
    activityStore.getSnapshot()[0].cancel!();
    await expect(build).resolves.toEqual({ status: 'superseded' });
    expect(signal?.aborted).toBe(true);
    expect(classify).toHaveBeenCalledWith(expect.objectContaining({ name: 'AbortError' }));
    classify.mockRestore();
    expect(useAssemblyBuildStore.getState()).toEqual(before);
    expect(useDesignModelStore.getState()).toBe(model);
    expect(activityStore.getSnapshot()).toEqual([]);
    expect(activityStore.typicalMs('assembly-build')).toBeNull();
  });

  it.each([200, 422])('Stop during the response body restores the pre-press build state (%s)', async status => {
    useAssemblyBuildStore.setState({ status: 'refused', refusal: 'Previous refusal', failureDetail: 'Previous detail' });
    const before = useAssemblyBuildStore.getState();
    const model = useDesignModelStore.getState();
    const reading = vi.fn();
    const fetcher: typeof fetch = async (_url, init) => ({
      ok: status === 200, status,
      json: () => new Promise((_resolve, reject) => {
        reading();
        const signal = init!.signal!;
        signal.addEventListener('abort', () => reject(signal.reason), { once: true });
      }),
      headers: new Headers(),
    }) as Response;
    const build = requestAssemblyBuild(fetcher);
    await vi.waitFor(() => expect(reading).toHaveBeenCalledOnce());
    activityStore.getSnapshot()[0].cancel!();
    await expect(build).resolves.toEqual({ status: 'superseded' });
    expect(useAssemblyBuildStore.getState()).toEqual(before);
    expect(useDesignModelStore.getState()).toBe(model);
    expect(activityStore.getSnapshot()).toEqual([]);
    expect(activityStore.typicalMs('assembly-build')).toBeNull();
  });

  it('publishes only the newest captured revision, whatever order answers arrive in', async () => {
    const { fetcher, pending, answer } = deferredFetcher();
    const first = requestAssemblyBuild(fetcher);
    const before = useDesignStore.getState().designRevision;
    const woofer = useDesignStore.getState().design.assembly!.woofers![0].id;
    commitAssembly(setPlacement(useDesignStore.getState().design.assembly!, woofer, { anchor: 'below', gap_mm: 25 }));
    const second = requestAssemblyBuild(fetcher);
    expect(pending.map(item => item.body.design_revision)).toEqual([before, before + 1]);
    expect(pending[0].body.build_session).toBe(pending[1].body.build_session);
    answer(1, record('wgi_new'));
    await second;
    answer(0, record('wgi_old'));
    expect(await first).toEqual({ status: 'superseded' });
    const state = useAssemblyBuildStore.getState();
    expect(state.status).toBe('published');
    expect(useDesignModelStore.getState().prepared?.ingest_id).toBe('wgi_new');
    expect(designModelCurrent(useDesignStore.getState().design)).toBe(true);
  });

  it('labels the last valid compile and marks runs stale after an edit or a refusal', async () => {
    const { fetcher, answer } = deferredFetcher();
    const build = requestAssemblyBuild(fetcher);
    answer(0, record('wgi_one'));
    await build;
    const design = () => useDesignStore.getState().design;
    expect(assemblyPreviewLabel(useAssemblyBuildStore.getState(), useDesignModelStore.getState(), design())).toBeNull();
    expect(designModelCurrent(design())).toBe(true);
    const woofer = design().assembly!.woofers![0].id;
    commitAssembly(setPlacement(design().assembly!, woofer, { anchor: 'below', gap_mm: 40 }));
    expect(designModelCurrent(design())).toBe(false);
    const previous = useDesignModelStore.getState();
    const refused = requestAssemblyBuild(fetcher);
    answer(1, null, 422);
    await expect(refused).rejects.toThrow('Set an enclosure depth.');
    const state = useAssemblyBuildStore.getState();
    expect(useDesignModelStore.getState()).toBe(previous);
    expect(state).toMatchObject({ status: 'refused', refusal: 'Set an enclosure depth.' });
    expect(useDesignModelStore.getState().prepared?.ingest_id).toBe('wgi_one');
    expect(assemblyPreviewLabel(state, useDesignModelStore.getState(), design())).toMatch(/earlier revision/);
    useDesignStore.getState().undo();
    expect(assemblyPreviewLabel(useAssemblyBuildStore.getState(), useDesignModelStore.getState(), design())).toBeNull();
  });

  it('shares one build per revision between Build, Mesh and Solve, and reuses a current one', async () => {
    const { fetcher, pending, answer } = deferredFetcher();
    const first = requestAssemblyBuild(fetcher);
    expect(prepareDesignModel(fetcher)).toBe(first);
    expect(pending).toHaveLength(1);
    answer(0, record('wgi_one'));
    await first;
    expect(await prepareDesignModel(fetcher)).toMatchObject({ status: 'published', reused: true, prepared: { ingest_id: 'wgi_one' } });
    expect(pending).toHaveLength(1);
    const scene = importedMeshStore.getSnapshot().solver;
    expect(scene).toMatchObject({ ingestId: 'wgi_one', modelKey: 'prepared:wgi_one' });
    const woofer = useDesignStore.getState().design.assembly!.woofers![0].id;
    commitAssembly(setPlacement(useDesignStore.getState().design.assembly!, woofer, { anchor: 'below', gap_mm: 30 }));
    const reused = prepareDesignModel(fetcher);
    expect(pending).toHaveLength(2);
    // The server can return a deep copy: identity is the prepared id.
    answer(1, structuredClone(record('wgi_one')));
    await reused;
    expect(importedMeshStore.getSnapshot().solver).toBe(scene);
    expect(designModelCurrent(useDesignStore.getState().design)).toBe(true);
  });

  it('drops an answer for a document that was replaced, and forgets the build on open', async () => {
    const { fetcher, answer } = deferredFetcher();
    const build = requestAssemblyBuild(fetcher);
    useDesignStore.getState().replaceDocument(designForFamily('OSSE'));
    answer(0, record('wgi_gone'));
    expect(await build).toEqual({ status: 'superseded' });
    expect(useAssemblyBuildStore.getState()).toMatchObject({ status: 'idle' });
    expect(useDesignModelStore.getState()).toMatchObject({ phase: 'empty', prepared: null });
    expect(await requestAssemblyBuild(fetcher)).toBeNull();
  });
});

it('clears only the reset design session scene on New/Open and fences freshness by load', async () => {
  resetDesignStore();
  commitAssembly(emptyAssembly(ids));
  const { fetcher, answer } = deferredFetcher();
  const build = requestAssemblyBuild(fetcher);
  answer(0, record('wgi_reset'));
  await build;
  const slot = useDesignModelStore.getState();
  const design = useDesignStore.getState().design;
  expect(designModelCurrent(design)).toBe(true);
  importedMeshStore.showSolver();
  const generation = importedMeshStore.captureGeneration();
  useDesignStore.getState().replaceDocument(design);
  expect(designModelCurrent(design, slot)).toBe(false);
  expect(useDesignModelStore.getState()).toMatchObject({ phase: 'empty', prepared: null });
  expect(importedMeshStore.getSnapshot()).toMatchObject({ solver: null, showing: 'parametric' });
  expect(importedMeshStore.captureGeneration()).toBe(generation);
  const next = requestAssemblyBuild(fetcher);
  answer(1, record('wgi_again'));
  await next;
  const otherScene = { ...importedMeshStore.getSnapshot().solver!, ingestId: null, modelKey: 'horn' };
  importedMeshStore.setSolver(otherScene);
  resetDesignStore();
  expect(importedMeshStore.getSnapshot().solver).toBe(otherScene);
  expect(useDesignModelStore.getState().prepared).toBeNull();
});
