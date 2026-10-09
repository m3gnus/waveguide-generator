import type { CadReturnBundle, CadReturnIngestRecord } from '../api/cadlink';
import { useCadReturnStore } from '../stores/cadReturn';
import { workspaceModeStore } from '../stores/workspaceMode';
import { useSolveOptionsStore } from '../stores/solveOptions';
import { createImportedMeshScene } from '../viewport/importedMesh';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { parseMSH } from '../viewport/mshParser';

/** Publish only after the immutable solve mesh has been fetched and parsed.
 * Failed or superseded preparation leaves the prior selected geometry intact. */
export async function prepareNativeAssembly(
  prepare: () => Promise<{ ingestion: CadReturnIngestRecord }>,
  fetcher: typeof fetch = fetch,
): Promise<CadReturnIngestRecord> {
  const previous = useCadReturnStore.getState();
  const viewportGeneration = importedMeshStore.beginIntent();
  const { ingestion: record } = await prepare();
  const channels = record.native_source?.channels;
  if (!channels || ![1, 2].includes(channels.length)
      || (channels.length === 1 && !record.native_source?.required_features.includes('native-general-horn-attachment-v1')))
    throw new Error('The prepared assembly has no verified physical source contract.');
  const response = await fetcher(`/api/cadlink/ingest/${encodeURIComponent(record.ingest_id)}/mesh`);
  if (!response.ok) throw new Error('The prepared mesh could not be verified. Previous geometry retained.');
  const mesh = parseMSH(await response.text());
  const current = useCadReturnStore.getState();
  if (!importedMeshStore.isCurrentGeneration(viewportGeneration)
      || current.selectedBundle !== previous.selectedBundle || current.ingestRecord !== previous.ingestRecord) {
    throw new Error('A later geometry selection superseded this preparation.');
  }
  const bundle: CadReturnBundle = {
    name: channels.length === 1 ? 'Native horn' : 'Native horn and woofer', bundlePath: record.ingest_id, bundleOrigin: 'native',
    modifiedAt: record.created_at, readable: true, documentName: channels.length === 1 ? 'Native horn' : 'Native horn and woofer',
    requestId: null, sourceCount: record.sources.length, instanceCount: 0, declaredCutPlanes: [],
    sources: record.sources.map((source) => ({ id: source.id, role: source.role, required: true,
      suggestedResolutionMm: record.mesh_sizes.source_size_mm[source.id],
      defaultDriveChannelId: channels.find((channel) => channel.source_ids.includes(source.id))!.id })),
  };
  const scene = createImportedMeshScene(bundle.name, mesh, 'cad', record.ingest_id, [], {
    fullDomain: true, artifactToken: record.mesh_content_sha256,
    solvedTriangleCount: record.mesh?.stats.triangle_count,
  });
  current.selectBundle(bundle, null);
  useCadReturnStore.setState({ driveChannels: structuredClone(channels), channelDrivers: {},
    combineSpec: null, combineEnabled: null, areaDriftOverrides: [], areaDriftSourceIds: [],
    exteriorOnly: false, passiveCardioid: { ...current.passiveCardioid, enabled: false, coupled: false } });
  const generation = useCadReturnStore.getState().beginIngestIntent();
  useCadReturnStore.getState().applyIngest(record, generation);
  importedMeshStore.setCad(scene, viewportGeneration);
  importedMeshStore.setCadSolver(scene, viewportGeneration, false);
  useSolveOptionsStore.getState().setEngine('metal');
  workspaceModeStore.setMode('cad');
  return record;
}
