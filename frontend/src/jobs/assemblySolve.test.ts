import { beforeEach, describe, expect, it } from 'vitest';
import { addWoofer, emptyAssembly } from '../stores/assembly';
import { assemblyContentKey, resetAssemblyBuild, useAssemblyBuildStore } from '../stores/assemblyBuild';
import { designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { solveAssembly } from './assemblySolve';

let counter = 0;
const ids = () => `00000000-0000-4000-8000-${String(counter += 1).padStart(12, '0')}`;

describe('assembly Solve press', () => {
  beforeEach(() => { counter = 0; resetDesignStore(); resetAssemblyBuild(); });

  it('presses the model Solve on the current compiled record with the assembly setup', async () => {
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
    const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
    const fetcher = (async (url: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(url), body: JSON.parse(String(init?.body)) });
      return new Response(JSON.stringify({ job_id: 'job-1' }), { status: 200 });
    }) as typeof fetch;
    const result = await solveAssembly(fetcher, () => 'p1');
    expect(result).toEqual({ jobId: 'job-1', notice: null });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe('/api/models/design-assembly%3Awgu_x/solve');
    const body = calls[0].body as { prepared_id: string; client_request_id: string; setup: { schema_version: number; geometry: Record<string, unknown> } };
    expect(body.prepared_id).toBe('wgi_9');
    expect(body.client_request_id).toBe('assembly-p1');
    expect(body.setup.schema_version).toBe(1);
    expect(body.setup.geometry.required_features).toEqual(['wg-design-assembly-v1']);
    expect((body.setup.geometry.drive_channels as Array<{ id: string }>).map(channel => channel.id)).toEqual(['horn-throat', id]);
  });

  it('refuses a design without an assembly', async () => {
    useDesignStore.getState().replaceDocument(designForFamily('R-OSSE'));
    await expect(solveAssembly(fetch)).rejects.toThrow('no Sources and baffle assembly');
  });
});
