import type { CompiledAssembly } from '../api/assembly';
import { mesh } from '../shell/cadlink/selection.fixtures';
import { assemblyContentKey } from '../stores/assemblyBuild';
import { currentDocumentLoad, useDesignStore } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { adoptDesignBuild } from '../stores/importedModel';

export const compiledDesignAssembly = () => ({
  status: 'published' as const, reused: false,
  model: { model_id: 'design-assembly:one', producer: 'design-assembly' as const, display_name: 'Assembly' },
  prepared: {
    ingest_id: 'wgi_live', model_id: 'design-assembly:one', producer: 'design-assembly',
    manifest_sha256: 'manifest', artifact_sha256: 'artifact', msh_text: mesh, source_tags: {},
    design: { design_uid: useDocumentStore.getState().designUid ?? '', name: 'Assembly', revision: 1,
      content_sha256: '', assembly_sha256: '', geometry_sha256: '' },
    radiators: [], signal_groups: [],
    sources: [{ id: 'horn-throat', role: 'HF', radiator_id: 'horn-throat', segment_id: null }],
    excitations: [{ id: 'horn-throat', source_ids: ['horn-throat'], motion: 'normal', patch_weights: {} }],
  } satisfies CompiledAssembly,
});

export function adoptCurrentDesignAssembly() {
  const { design, designRevision } = useDesignStore.getState();
  const result = compiledDesignAssembly();
  adoptDesignBuild({ model: result.model, record: result.prepared }, {
    designUid: useDocumentStore.getState().designUid ?? null, revision: designRevision,
    load: currentDocumentLoad(), contentKey: assemblyContentKey(design),
  });
  return result;
}
