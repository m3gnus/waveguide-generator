import { beforeEach, describe, expect, it } from 'vitest';
import { addWoofer, commitAssembly, emptyAssembly, setPlacement } from './assembly';
import {
  assemblyBuildCurrent, assemblyPreviewLabel, assemblyRunStale, requestAssemblyBuild, resetAssemblyBuild, useAssemblyBuildStore,
} from './assemblyBuild';
import { designForFamily, resetDesignStore, useDesignStore } from './design';

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

const record = (id: string) => ({ ingest_id: id, producer: 'design-assembly', radiators: [], msh_text: '$MeshFormat' });

describe('assembly background build', () => {
  beforeEach(() => {
    counter = 0;
    resetDesignStore();
    useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), enclosure: { ...designForFamily('R-OSSE').enclosure, depth: 200 } });
    resetAssemblyBuild();
    commitAssembly(addWoofer(emptyAssembly(ids), { anchor: 'below' }, ids).assembly);
  });

  it('publishes only the newest captured revision, whatever order answers arrive in', async () => {
    const { fetcher, pending, answer } = deferredFetcher();
    const first = requestAssemblyBuild(fetcher);
    const before = useDesignStore.getState().designRevision;
    const woofer = useDesignStore.getState().design.assembly!.woofers![0].id;
    commitAssembly(setPlacement(useDesignStore.getState().design.assembly!, woofer, { anchor: 'below', gap_mm: 25 }));
    const second = requestAssemblyBuild(fetcher);
    expect(pending.map(item => item.body.design_revision)).toEqual([before, before + 1]);
    answer(1, record('wgi_new'));
    await second;
    answer(0, record('wgi_old'));
    expect(await first).toEqual({ status: 'superseded' });
    const state = useAssemblyBuildStore.getState();
    expect(state.status).toBe('published');
    expect(state.published?.record.ingest_id).toBe('wgi_new');
    expect(assemblyBuildCurrent(state, useDesignStore.getState().design)).toBe(true);
  });

  it('labels the last valid compile and marks runs stale after an edit or a refusal', async () => {
    const { fetcher, answer } = deferredFetcher();
    const build = requestAssemblyBuild(fetcher);
    answer(0, record('wgi_one'));
    await build;
    const design = () => useDesignStore.getState().design;
    expect(assemblyPreviewLabel(useAssemblyBuildStore.getState(), design())).toBeNull();
    expect(assemblyRunStale('wgi_one', useAssemblyBuildStore.getState(), design())).toBe(false);
    const woofer = design().assembly!.woofers![0].id;
    commitAssembly(setPlacement(design().assembly!, woofer, { anchor: 'below', gap_mm: 40 }));
    expect(assemblyRunStale('wgi_one', useAssemblyBuildStore.getState(), design())).toBe(true);
    const refused = requestAssemblyBuild(fetcher);
    answer(1, null, 422);
    await expect(refused).rejects.toThrow('Set an enclosure depth.');
    const state = useAssemblyBuildStore.getState();
    expect(state).toMatchObject({ status: 'refused', refusal: 'Set an enclosure depth.' });
    expect(state.published?.record.ingest_id).toBe('wgi_one');
    expect(assemblyPreviewLabel(state, design())).toMatch(/earlier revision/);
    useDesignStore.getState().undo();
    expect(assemblyPreviewLabel(useAssemblyBuildStore.getState(), design())).toBeNull();
  });

  it('drops an answer for a document that was replaced, and forgets the build on open', async () => {
    const { fetcher, answer } = deferredFetcher();
    const build = requestAssemblyBuild(fetcher);
    useDesignStore.getState().replaceDocument(designForFamily('OSSE'));
    answer(0, record('wgi_gone'));
    expect(await build).toEqual({ status: 'superseded' });
    expect(useAssemblyBuildStore.getState()).toMatchObject({ status: 'idle', published: null });
    expect(await requestAssemblyBuild(fetcher)).toBeNull();
  });
});
