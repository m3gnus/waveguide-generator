import { beforeEach, describe, expect, it } from 'vitest';
import type { CadReturnBundle, CadReturnIngestRecord } from '../api/cadlink';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { buildImportedSubmission, importedSubmissionBlocker } from './importedSubmission';

const bundle = {
  name: 'three-way.wgreturn', bundlePath: 'wgreturn/three-way.wgreturn', modifiedAt: '2026-08-13T12:00:00Z', readable: true,
  documentName: 'Three way', requestId: null, sourceCount: 3, instanceCount: 1,
  sources: [
    { id: 'source-hf', role: 'HF', required: true, suggestedResolutionMm: 2, defaultDriveChannelId: 'drive-hf' },
    { id: 'source-mf', role: 'MF', required: true, suggestedResolutionMm: 4, defaultDriveChannelId: 'drive-mf' },
    { id: 'source-lf', role: 'LF', required: false, suggestedResolutionMm: 8, defaultDriveChannelId: 'drive-lf' },
  ],
} satisfies CadReturnBundle;

const record = {
  ingest_id: 'wgi_wire_fixture', manifest_sha256: 'sha256:manifest', artifact_sha256: 'sha256:artifact', report_sha256: 'sha256:report',
  findings: [], evidence: { fem_air_volumes: [] }, skipped_source_ids: ['source-lf'],
  mesh_sizes: { rigid_size_mm: 5, transition_mm: 3, source_size_mm: { 'source-hf': 2, 'source-mf': 4 } },
  symmetry: { cut_planes: ['x0'], planes: {} }, polar_grid_derivation: {}, role_findings: [],
} as unknown as CadReturnIngestRecord;

describe('imported solve submission wire', () => {
  beforeEach(() => { resetCadReturnStore(); resetSolveOptionsStore(); });

  it('preserves the pre-Slice-C payload bytes apart from the explicit align field', () => {
    useCadReturnStore.setState({
      selectedBundle: bundle,
      ingestRecord: record,
      acknowledgedFindingIds: ['accepted-area-drift'],
      sourceSizesMm: { 'source-hf': 1.75, 'source-mf': 3.5, 'source-lf': 7 },
      rigidSizeMm: 4.5,
      transitionMm: 2.5,
      skippedSourceIds: ['source-lf'],
      driveChannels: [
        { id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' },
        { id: 'drive-mf', source_ids: ['source-mf'], motion: 'axial' },
      ],
      exteriorOnly: true,
      combineEnabled: true,
      combineCrossoversHz: { 'drive-mf→drive-hf': 1_250 },
      combineLevelMatch: false,
      channelDrivers: {
        'drive-hf': {
          enabled: true,
          fields: { sd_cm2: 80, bl_t_m: 7.2, re_ohm: 5.8, le_mh: 0.4, mmd_g: 12, cms_m_per_n: 0.0003 },
        },
      },
      driveVoltageV: 4,
      frequencyStartHz: 180,
      frequencyEndHz: 18_000,
      frequencyCount: 37,
      needsIngest: false,
    });
    useSolveOptionsStore.getState().setVerbose(true);

    const submission = buildImportedSubmission(useCadReturnStore.getState());
    const { align: _sliceCAddition, ...legacyCombine } = submission.geometry.combine!;
    const legacyBytes = JSON.stringify({
      ...submission,
      geometry: { ...submission.geometry, combine: legacyCombine },
    });

    expect(legacyBytes).toMatchInlineSnapshot(`"{"geometry":{"type":"imported","ingest_id":"wgi_wire_fixture","manifest_sha256":"sha256:manifest","artifact_sha256":"sha256:artifact","drive_channels":[{"id":"drive-hf","source_ids":["source-hf"],"motion":"normal","driver":{"sd_cm2":80,"bl_t_m":7.2,"re_ohm":5.8,"le_mh":0.4,"mmd_g":12,"cms_m_per_n":0.0003}},{"id":"drive-mf","source_ids":["source-mf"],"motion":"axial"}],"drive_voltage_v":4,"mesh":{"rigid_size_mm":4.5,"transition_mm":2.5,"source_size_mm":{"source-hf":1.75,"source-mf":3.5}},"acknowledged_findings":["sha256:report:accepted-area-drift"],"skipped_source_ids":["source-lf"],"exterior_only":true,"combine":{"members":["drive-mf","drive-hf"],"crossovers_hz":[1250],"level_match":false}},"options":{"engine":"metal","symmetry":"auto","mesh_validation_mode":"warn","verbose":true,"frequency_spacing":"log","polar_config":{"angle_range":[0,180,37],"angle_step":5,"distance":2,"norm_angle":5,"inclination":45,"enabled_axes":["horizontal","vertical","diagonal"],"observation_origin":"mouth","spherical_sampling":false,"field_plane":true},"frequency_range":[180,18000],"num_frequencies":37}}"`);
    expect(submission.geometry.combine?.align).toBe(true);

    useCadReturnStore.getState().setCombineAlign(false);
    expect(buildImportedSubmission(useCadReturnStore.getState()).geometry.combine?.align).toBe(false);
  });

  it('does not gate a solve on the informational unlinked finding', () => {
    const unlinkedRecord = {
      ...record,
      freshness: { verdict: 'unlinked' as const, instances: [], finding_id: 'unlinked-mode' },
      findings: [{
        id: 'unlinked-mode', kind: 'freshness', blocking: false, verdict: 'unlinked',
      }],
    };
    const store = useCadReturnStore.getState();
    store.selectBundle({ ...bundle, instanceCount: 0 });
    store.applyIngest(unlinkedRecord, store.beginIngestIntent());

    expect(importedSubmissionBlocker(useCadReturnStore.getState())).toBeNull();
    expect(buildImportedSubmission(useCadReturnStore.getState()).geometry.acknowledged_findings).toEqual([]);
  });
});
