import { beforeEach, describe, expect, it } from 'vitest';
import type { CadReturnBundle, CadReturnIngestRecord } from '../api/cadlink';
import { acknowledgedFindingWire, resetCadReturnStore, unacknowledgedBlocking, useCadReturnStore } from './cadReturn';

const bundle: CadReturnBundle = {
  name: 'speaker.wgreturn', bundlePath: 'wgreturn/speaker.wgreturn', modifiedAt: '2026-08-11T00:00:00Z', readable: true,
  documentName: 'Speaker', requestId: null, sourceCount: 2, instanceCount: 1,
  sources: [
    { id: 'source-mf', role: 'MF', required: true, suggestedResolutionMm: 8, defaultDriveChannelId: 'drive-mf' },
    { id: 'source-hf', role: 'HF', required: false, suggestedResolutionMm: 3, defaultDriveChannelId: 'drive-hf' },
  ],
};

function record(id = 'wgi_one'): CadReturnIngestRecord {
  return {
    ingest_id: id, created_at: '', return_id: '', manifest_sha256: 'sha256:m', artifact_sha256: 'sha256:a', report_sha256: `sha256:${id}`,
    acoustic_domain: 'free-space', scope: { status: 'degraded', degraded_skip_count: 1 }, sources: [],
    mesh_sizes: { rigid_size_mm: 8, transition_mm: 8, source_size_mm: { 'source-mf': 8, 'source-hf': 3 } }, skipped_source_ids: [],
    freshness: { verdict: 'per-instance', instances: [] },
    findings: [{ id: 'finding-a', kind: 'freshness', blocking: true }],
    symmetry: {}, healing: {}, sizing_estimate: {}, polar_grid_derivation: {}, tag_map: {},
  };
}

describe('CAD return store', () => {
  beforeEach(resetCadReturnStore);

  it('initializes complete sizes and one default channel per source', () => {
    useCadReturnStore.getState().selectBundle(bundle);
    const state = useCadReturnStore.getState();
    expect(state.sourceSizesMm).toEqual({ 'source-mf': 8, 'source-hf': 3 });
    expect(state.rigidSizeMm).toBe(8);
    expect(state.driveChannels.map(({ id, source_ids }) => ({ id, source_ids }))).toEqual([
      { id: 'drive-mf', source_ids: ['source-mf'] },
      { id: 'drive-hf', source_ids: ['source-hf'] },
    ]);
  });

  it('uses a sub-millimetre coarsest source suggestion without a 1 mm floor', () => {
    useCadReturnStore.getState().selectBundle({
      ...bundle,
      sources: bundle.sources.map((source, index) => ({ ...source, suggestedResolutionMm: index ? 0.15 : 0.4 })),
    });
    expect(useCadReturnStore.getState().rigidSizeMm).toBe(0.4);
    expect(useCadReturnStore.getState().transitionMm).toBe(0.4);
  });

  it('gates on every blocking finding and resets acknowledgements on re-ingest', () => {
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().applyIngest(record(), useCadReturnStore.getState().beginIngestIntent());
    expect(unacknowledgedBlocking(useCadReturnStore.getState())).toEqual(['finding-a']);
    useCadReturnStore.getState().acknowledge('finding-a', true);
    expect(unacknowledgedBlocking(useCadReturnStore.getState())).toEqual([]);
    expect(acknowledgedFindingWire(record(), ['finding-a'])).toEqual(['sha256:wgi_one:finding-a']);
    useCadReturnStore.getState().applyIngest(record('wgi_two'), useCadReturnStore.getState().beginIngestIntent());
    expect(useCadReturnStore.getState().acknowledgedFindingIds).toEqual([]);
    expect(unacknowledgedBlocking(useCadReturnStore.getState())).toEqual(['finding-a']);
  });

  it('supports explicit grouping and removes optional skipped sources from channels', () => {
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().setSourceChannel('source-hf', 'drive-mf');
    expect(useCadReturnStore.getState().driveChannels).toEqual([
      { id: 'drive-mf', source_ids: ['source-mf', 'source-hf'], motion: 'normal' },
    ]);
    useCadReturnStore.getState().setSkipped('source-hf', true);
    expect(useCadReturnStore.getState().driveChannels[0].source_ids).toEqual(['source-mf']);
    expect(useCadReturnStore.getState().needsIngest).toBe(true);
  });

  it('carries the solve setup across a same-inventory arrival but never acknowledgements', () => {
    const store = useCadReturnStore.getState();
    store.selectBundle(bundle);
    store.applyIngest(record(), store.beginIngestIntent());
    store.acknowledge('finding-a', true);
    store.setSourceSize('source-hf', 2.25);
    store.setSourceChannel('source-hf', 'drive-mf');
    store.setExteriorOnly(true);
    store.setCombineEnabled(true);
    store.setChannelDriverEnabled('drive-mf', true);
    store.setSweep({ frequencyStartHz: 300, frequencyEndHz: 12_000, frequencyCount: 31 });

    const arrived = {
      ...bundle,
      modifiedAt: '2026-08-11T02:00:00Z',
      documentName: 'Speaker v2',
      // New suggestions must not clobber the user's chosen sizes.
      sources: bundle.sources.map((source) => ({ ...source, suggestedResolutionMm: 5 })),
    };
    expect(useCadReturnStore.getState().selectArrivedBundle(arrived)).toBe('carried');

    const state = useCadReturnStore.getState();
    expect(state.selectedBundle?.documentName).toBe('Speaker v2');
    expect(state.sourceSizesMm['source-hf']).toBe(2.25);
    expect(state.driveChannels).toEqual([
      { id: 'drive-mf', source_ids: ['source-mf', 'source-hf'], motion: 'normal' },
    ]);
    expect(state.exteriorOnly).toBe(true);
    expect(state.combineEnabled).toBe(true);
    expect(state.channelDrivers['drive-mf']?.enabled).toBe(true);
    expect(state.frequencyStartHz).toBe(300);
    // The new geometry re-earns its evidence.
    expect(state.ingestRecord).toBeNull();
    expect(state.acknowledgedFindingIds).toEqual([]);
    expect(state.needsIngest).toBe(true);
  });

  it('resets fully when an arrival changes the source inventory', () => {
    const store = useCadReturnStore.getState();
    store.selectBundle(bundle);
    store.setSourceSize('source-hf', 2.25);
    store.setCombineEnabled(true);

    const arrived = {
      ...bundle,
      modifiedAt: '2026-08-11T02:00:00Z',
      sourceCount: 1,
      sources: [bundle.sources[0]],
    };
    expect(useCadReturnStore.getState().selectArrivedBundle(arrived)).toBe('reset');
    const state = useCadReturnStore.getState();
    expect(state.sourceSizesMm).toEqual({ 'source-mf': 8 });
    expect(state.combineEnabled).toBe(false);
  });

  it('marks a refreshed changed listing stale while preserving sizing edits', () => {
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().applyIngest(record(), useCadReturnStore.getState().beginIngestIntent());
    useCadReturnStore.getState().setSourceSize('source-hf', 2.25);
    useCadReturnStore.getState().refreshSelectedBundle({
      ...bundle,
      modifiedAt: '2026-08-11T01:00:00Z',
      sources: bundle.sources.map((source) => source.id === 'source-hf'
        ? { ...source, suggestedResolutionMm: 2.5 }
        : source),
    });
    const state = useCadReturnStore.getState();
    expect(state.needsIngest).toBe(true);
    expect(state.ingestStaleReason).toContain('source inventory or source sizing suggestions changed');
    expect(state.sourceSizesMm['source-hf']).toBe(2.25);
    expect(state.acknowledgedFindingIds).toEqual([]);
  });
});
