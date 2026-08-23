import { beforeEach, describe, expect, it } from 'vitest';
import type { CadReturnBundle, CadReturnIngestRecord } from '../api/cadlink';
import { expandLegacy, toWire, withDelayMode } from '../results/crossoverSpec';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { buildImportedSubmission, importedSubmissionBlocker, widenPolarToDerivation } from './importedSubmission';

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
      combineSpec: expandLegacy(['drive-mf', 'drive-hf'], [1_250], false, true),
      channelDrivers: {
        'drive-hf': {
          enabled: true,
          fields: { sd_cm2: 80, bl_t_m: 7.2, re_ohm: 5.8, le_mh: 0.4, mmd_g: 12, cms_m_per_n: 0.0003 },
          preset: null,
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
    // The crossover travels as the per-channel v2 spec now. Pinning it inside
    // the byte snapshot would make every unrelated wire change re-baseline a
    // long nested object, so it is asserted on its own below and elided here.
    const legacyBytes = JSON.stringify({
      ...submission,
      geometry: { ...submission.geometry, combine: undefined },
    });

    expect(legacyBytes).toMatchInlineSnapshot(`"{"geometry":{"type":"imported","ingest_id":"wgi_wire_fixture","manifest_sha256":"sha256:manifest","artifact_sha256":"sha256:artifact","drive_channels":[{"id":"drive-hf","source_ids":["source-hf"],"motion":"normal","driver":{"sd_cm2":80,"bl_t_m":7.2,"re_ohm":5.8,"le_mh":0.4,"mmd_g":12,"cms_m_per_n":0.0003}},{"id":"drive-mf","source_ids":["source-mf"],"motion":"axial"}],"drive_voltage_v":4,"mesh":{"rigid_size_mm":4.5,"transition_mm":2.5,"source_size_mm":{"source-hf":1.75,"source-mf":3.5}},"acknowledged_findings":["sha256:report:accepted-area-drift"],"skipped_source_ids":["source-lf"],"exterior_only":true},"options":{"engine":"metal","solver_mode":"auto","symmetry":"auto","mesh_validation_mode":"warn","verbose":true,"frequency_spacing":"log","polar_config":{"angle_range":[0,180,37],"angle_step":5,"distance":2,"norm_angle":5,"inclination":45,"enabled_axes":["horizontal","vertical","diagonal"],"observation_origin":"mouth","spherical_sampling":false,"field_plane":true},"frequency_range":[180,18000],"num_frequencies":37}}"`);
    expect(submission.geometry.combine).toEqual(
      toWire(expandLegacy(['drive-mf', 'drive-hf'], [1_250], false, true)),
    );

    useCadReturnStore.getState().updateCombineSpec((spec) => withDelayMode(spec, 'manual'));
    expect(buildImportedSubmission(useCadReturnStore.getState()).geometry.combine?.channels?.['drive-hf'].delay)
      .toEqual({ mode: 'manual', ms: 0 });
  });

  it('names the picked driver on the wire, merging the preset under the edits', () => {
    useCadReturnStore.setState({
      selectedBundle: bundle,
      ingestRecord: record,
      sourceSizesMm: { 'source-hf': 2, 'source-mf': 4 },
      driveChannels: [{ id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' }],
      channelDrivers: {
        'drive-hf': {
          enabled: true,
          fields: { bl_t_m: 11.9, count: 2 },
          preset: {
            id: 'Acme::HD-1::8',
            label: 'Acme HD-1',
            source: 'database',
            kind: 'cd',
            z_ohm: 8,
            base: { sd_cm2: 26, bl_t_m: 12.4, re_ohm: 6.2, mms_g: 2.4, fs_hz: 620 },
          },
        },
      },
      needsIngest: false,
    });

    const driver = buildImportedSubmission(useCadReturnStore.getState())
      .geometry.drive_channels.find((channel) => channel.id === 'drive-hf')?.driver;

    expect(driver).toEqual({
      sd_cm2: 26, bl_t_m: 11.9, re_ohm: 6.2, mms_g: 2.4, fs_hz: 620, count: 2, label: 'Acme HD-1',
    });
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

describe('widening a polar grid onto the ingestion derivation', () => {
  const derivation = (diagonalPinned: boolean) => ({
    axes: {
      horizontal: { minimum_deg: 0, maximum_deg: 180 },
      vertical: { minimum_deg: -180, maximum_deg: 180 },
      diagonal: diagonalPinned
        ? { minimum_deg: -180, maximum_deg: 180 }
        : { minimum_deg: 0, maximum_deg: 180 },
    },
  });
  const options = (enabledAxes: string[], inclination: number) => ({
    polar_config: { angle_range: [0, 180, 37] as [number, number, number], enabled_axes: enabledAxes, inclination },
  }) as unknown as Parameters<typeof widenPolarToDerivation>[0];

  it('defaults the inclination of a diagonal the user never enabled', () => {
    // A y0-asymmetric speaker pins the diagonal, so the submission enables a
    // plane the form had switched off -- and with it an angle field the form
    // had disabled. Sending that stale angle refuses the whole solve.
    const wire = options(['horizontal', 'vertical'], 35);
    widenPolarToDerivation(wire, derivation(true));

    expect(wire.polar_config).toMatchObject({
      enabled_axes: ['horizontal', 'vertical', 'diagonal'],
      inclination: 45,
      angle_range: [-180, 180, 73],
    });
  });

  it('keeps an inclination the user chose for a diagonal they enabled themselves', () => {
    const wire = options(['horizontal', 'vertical', 'diagonal'], 35);
    widenPolarToDerivation(wire, derivation(true));

    expect(wire.polar_config).toMatchObject({ inclination: 35 });
  });

  it('leaves an unpinned diagonal alone', () => {
    const wire = options(['horizontal'], 35);
    widenPolarToDerivation(wire, derivation(false));

    expect(wire.polar_config).toMatchObject({ enabled_axes: ['horizontal', 'vertical'], inclination: 35 });
  });
});
