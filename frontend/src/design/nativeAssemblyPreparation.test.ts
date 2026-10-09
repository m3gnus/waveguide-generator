import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { buildImportedSubmission, importedSubmissionBlocker } from '../jobs/importedSubmission';
import { buildCadProjectSetup } from '../shell/cadSetupPublisher';
import { channelAcceptsDriver, resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import fixture from '../viewport/test-fixtures/tagged_sources-small.msh?raw';
import { prepareNativeAssembly } from './nativeAssemblyPreparation';

const record = {
  ingest_id: 'wgi_native', created_at: '2026-10-09T00:00:00Z',
  manifest_sha256: 'sha256:manifest', artifact_sha256: 'sha256:step', report_sha256: 'sha256:report',
  mesh_content_sha256: 'sha256:mesh', sources: [
    { id:'horn/piston', role:'HF' }, { id:'woofer/cone', role:'LF' }, { id:'woofer/surround', role:'LF' },
  ], findings: [], skipped_source_ids: [], mesh_sizes: { rigid_size_mm:4, transition_mm:3,
    source_size_mm:{ 'horn/piston':2, 'woofer/cone':1, 'woofer/surround':.5 } },
  mesh:{ stats:{ triangle_count:2 } }, symmetry:{ cut_planes:[] }, polar_grid_derivation:{},
  native_source:{ required_features:['native-source-contour-v1','native-shared-horn-woofer-v1','native-phase-plug-passages-v1'], channels:[
    { id:'hf', source_ids:['horn/piston'], motion:'normal', physical_source_id:'horn', patch_weights:{ 'horn/piston':1 } },
    { id:'lf', source_ids:['woofer/cone','woofer/surround'], motion:'axial', physical_source_id:'woofer', patch_weights:{ 'woofer/cone':-.5,'woofer/surround':0 } },
  ] },
} as unknown as CadReturnIngestRecord;

describe('native assembly imported workspace handoff', () => {
  beforeEach(() => { resetCadReturnStore(); resetSolveOptionsStore(); importedMeshStore.clear(); workspaceModeStore.setMode('parametric'); });
  const prepare = () => Promise.resolve({ ingestion:structuredClone(record) });
  const fetcher = vi.fn<typeof fetch>(async () => new Response(fixture));

  it('uses the real imported submission builder with exact weights, identities and features', async () => {
    await prepareNativeAssembly(prepare, fetcher);
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe(record.ingest_id);
    expect(importedMeshStore.getSnapshot().cadSolver?.artifactToken).toBe(record.mesh_content_sha256);
    const submission = buildImportedSubmission();
    expect(submission.geometry.required_features).toEqual(record.native_source!.required_features);
    expect(submission.geometry.drive_channels).toEqual(record.native_source!.channels);
    expect(submission.geometry.mesh).toEqual(record.mesh_sizes);
    expect(submission.options.engine).toBe('metal');
    expect(useSolveOptionsStore.getState().engine).toBe('metal');
    expect(useCadReturnStore.getState().isIngestSettled()).toBe(true);
    expect(importedSubmissionBlocker()).toBeNull();
    expect(channelAcceptsDriver(useCadReturnStore.getState().driveChannels[0])).toBe(false);
    expect(buildCadProjectSetup(undefined,undefined,undefined,'unrelated-cad-project')).toBeNull();
  });

  it('hydrates a negotiated horn-only artifact with its signed source weights', async () => {
    const horn = structuredClone(record);
    horn.sources = [horn.sources[0]];
    horn.native_source!.channels = [horn.native_source!.channels![0]];
    horn.native_source!.channels[0].patch_weights!['horn/piston'] = -.5;
    horn.native_source!.required_features.push('native-general-horn-attachment-v1');
    horn.mesh_sizes.source_size_mm = { 'horn/piston': 2 };
    await prepareNativeAssembly(async () => ({ ingestion: horn }), fetcher);
    const submission = buildImportedSubmission();
    expect(submission.geometry.required_features).toContain('native-general-horn-attachment-v1');
    expect(submission.geometry.drive_channels).toEqual(horn.native_source!.channels);
    expect(useCadReturnStore.getState().selectedBundle?.name).toBe('Native horn');
  });

  it('keeps previous valid geometry and solve state when fetching the new mesh fails', async () => {
    await prepareNativeAssembly(prepare, fetcher);
    const previous = useCadReturnStore.getState(); const scene = importedMeshStore.getSnapshot().cad;
    await expect(prepareNativeAssembly(prepare, async () => new Response('',{status:409}))).rejects.toThrow('verified');
    expect(useCadReturnStore.getState()).toBe(previous);
    expect(importedMeshStore.getSnapshot().cad).toBe(scene);
    expect(importedSubmissionBlocker()).toBeNull();
  });

  it('discards a prepared mesh after later viewport intent supersedes it', async () => {
    let resolve!: (value: { ingestion:CadReturnIngestRecord }) => void;
    const pending = prepareNativeAssembly(() => new Promise((r) => { resolve = r; }), fetcher);
    importedMeshStore.showParametric();
    resolve({ ingestion:structuredClone(record) });
    await expect(pending).rejects.toThrow('superseded');
    expect(useCadReturnStore.getState().ingestRecord).toBeNull();
    expect(importedMeshStore.getSnapshot().showing).toBe('parametric');
    expect(useSolveOptionsStore.getState().engine).toBe('auto');
  });

  it('requires authoring again after source assignment or motion changes', async () => {
    await prepareNativeAssembly(prepare, fetcher);
    useCadReturnStore.getState().setChannelMotion('hf','axial');
    expect(importedSubmissionBlocker()).toContain('prepare it again');
  });

  it('compares channel fields independently of object-key order', async () => {
    await prepareNativeAssembly(prepare, fetcher);
    const channels = useCadReturnStore.getState().driveChannels.map((c) => ({
      patch_weights: Object.fromEntries(Object.entries(c.patch_weights!).reverse()),
      physical_source_id: c.physical_source_id, motion: c.motion, source_ids: c.source_ids, id: c.id,
    }));
    useCadReturnStore.setState({ driveChannels: channels });
    expect(importedSubmissionBlocker()).toBeNull();
    useCadReturnStore.setState({ driveChannels: channels.map((c, i) => i ? {
      ...c, patch_weights: { ...c.patch_weights, 'woofer/cone': .5 },
    } : c) });
    expect(importedSubmissionBlocker()).toContain('prepare it again');
  });
});
