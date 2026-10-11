import type { CadReturnBundle, CadReturnIngestRecord } from '../../api/cadlink';
import type { JobItem } from '../../api/jobsSocket';

export const bundle: CadReturnBundle = {
  name: 'speaker.wgreturn', bundlePath: 'wgreturn/speaker.wgreturn',
  modifiedAt: '2026-08-11T00:00:00Z', readable: true, documentName: 'Speaker', requestId: null,
  sourceCount: 1, instanceCount: 1, designIds: [],
  sources: [{ id: 'source-hf', role: 'HF', required: true, suggestedResolutionMm: 4,
    defaultDriveChannelId: 'drive-hf' }],
};

export const record: CadReturnIngestRecord = {
  ingest_id: 'wgi_fixture', created_at: '', return_id: '',
  manifest_sha256: `sha256:${'1'.repeat(64)}`, artifact_sha256: `sha256:${'2'.repeat(64)}`,
  report_sha256: `sha256:${'3'.repeat(64)}`, acoustic_domain: 'free-space',
  scope: { status: 'clean', degraded_skip_count: 0 },
  sources: [{ id: 'source-hf', role: 'HF', required: true, instance_id: null,
    default_drive_channel_id: 'drive-hf', suggested_resolution_mm: 4 }],
  mesh_sizes: { rigid_size_mm: 4, transition_mm: 4, source_size_mm: { 'source-hf': 4 } },
  skipped_source_ids: [], freshness: { verdict: 'per-instance', instances: [] }, findings: [],
  symmetry: { planes: {}, cut_planes: [] }, healing: {}, sizing_estimate: {},
  polar_grid_derivation: {}, tag_map: {},
};

export const mesh = [
  '$MeshFormat', '2.2 0 8', '$EndMeshFormat', '$Nodes', '3',
  '1 0 0 0', '2 1 0 0', '3 0 1 0', '$EndNodes', '$Elements', '1',
  '1 2 2 1 1 1 2 3', '$EndElements', '',
].join('\n');

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

export function job(ingestId = record.ingest_id): JobItem {
  return {
    id: `job-${ingestId}`, run_number: 42, config_summary: { geometry_type: 'imported' },
    cad_source: { ingest_id: ingestId, document_name: 'Archived speaker' },
    cad_setup: { drive_voltage_v: 8, max_drive_voltage_v: 20,
      drive_channels: [{ id: 'archive-hf', source_ids: ['source-hf'], motion: 'normal' }],
      exterior_only: true },
    solve_options: { frequencies_hz: [100, 500, 1000], frequency_spacing: 'log',
      mesh_validation_mode: 'strict', verbose: false },
  } as unknown as JobItem;
}
