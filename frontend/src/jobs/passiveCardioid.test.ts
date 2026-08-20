import { beforeEach, describe, expect, it } from 'vitest';
import type { CadReturnBundle, CadReturnIngestRecord } from '../api/cadlink';
import {
  assignableChannelIds,
  normalizePassiveCardioid,
  passiveCardioidBlocker,
  passiveCardioidWire,
  PASSIVE_CARDIOID_CHANNEL_ID,
  PASSIVE_CARDIOID_DEFAULTS,
  resetCadReturnStore,
  useCadReturnStore,
  type PassiveCardioidForm,
} from '../stores/cadReturn';
import { CAD_CARDIOID_FIELD_CONTROLS } from '../design/cadControlRegistry';
import { resetSolveOptionsStore } from '../stores/solveOptions';
import { buildImportedSubmission, importedSubmissionBlocker } from './importedSubmission';
import { explainImportedRefusal } from './importedRefusals';

const bundle = {
  name: 'cardioid.wgreturn', bundlePath: 'wgreturn/cardioid.wgreturn', modifiedAt: '2026-08-19T12:00:00Z', readable: true,
  documentName: 'Cardioid', requestId: null, sourceCount: 2, instanceCount: 1,
  sources: [
    { id: 'MF', role: 'MF', required: true, suggestedResolutionMm: 4, defaultDriveChannelId: 'drive-mf' },
    { id: 'PORT_EXIT', role: 'MF', required: true, suggestedResolutionMm: 4, defaultDriveChannelId: 'drive-port' },
  ],
} satisfies CadReturnBundle;

const record = {
  ingest_id: 'wgi_cardioid', manifest_sha256: 'sha256:manifest', artifact_sha256: 'sha256:artifact', report_sha256: 'sha256:report',
  findings: [], evidence: { fem_air_volumes: [] }, skipped_source_ids: [],
  mesh_sizes: { rigid_size_mm: 5, transition_mm: 3, source_size_mm: { MF: 4, PORT_EXIT: 4 } },
  symmetry: { cut_planes: [], planes: {} }, polar_grid_derivation: {}, role_findings: [],
} as unknown as CadReturnIngestRecord;

/** The reference run of docs/reference/CARDIOID-INPUT-CONTRACT.md. */
const complete: PassiveCardioidForm = {
  enabled: true,
  rearVolumeL: 6,
  portLengthMm: 25,
  modelPortAreaM2: 0.05,
  bemPortAreaM2: 0.009471859930646809,
  portAreaSource: 'user',
  foamResistancePaSM3: 10_000,
  invertPort: true,
  coupled: false,
};

function seedReturn(passiveCardioid: PassiveCardioidForm = { ...PASSIVE_CARDIOID_DEFAULTS }): void {
  useCadReturnStore.setState({
    selectedBundle: bundle,
    ingestRecord: record,
    sourceSizesMm: { MF: 4, PORT_EXIT: 4 },
    rigidSizeMm: 5,
    transitionMm: 3,
    driveChannels: [
      { id: 'drive-mf', source_ids: ['MF'], motion: 'normal' },
      { id: 'drive-port', source_ids: ['PORT_EXIT'], motion: 'normal' },
    ],
    passiveCardioid,
    needsIngest: false,
  });
}

describe('passive cardioid opt-in boundary', () => {
  beforeEach(() => { resetCadReturnStore(); resetSolveOptionsStore(); });

  it('sends no cardioid key at all while the section is off', () => {
    seedReturn();
    const geometry = buildImportedSubmission(useCadReturnStore.getState()).geometry as unknown as Record<string, unknown>;
    expect(Object.keys(geometry).filter((key) => key.includes('cardioid') || key.includes('port_area'))).toEqual([]);
    // Both booleans have server defaults; sending them without a rear volume
    // is itself a refusal, so an untouched form must stay silent about them.
    expect(geometry.passive_cardioid_invert_port).toBeUndefined();
    expect(geometry.passive_cardioid_coupled).toBeUndefined();
  });

  it('refuses a half-filled form instead of falling back to the pre-campaign path', () => {
    seedReturn({ ...complete, portLengthMm: null, foamResistancePaSM3: null });
    const blocker = importedSubmissionBlocker(useCadReturnStore.getState());
    expect(blocker).toContain('Port length');
    expect(blocker).toContain('Foam resistance');
    expect(() => buildImportedSubmission(useCadReturnStore.getState())).toThrow(/Port length/);
  });

  it('refuses a rear volume that is not a positive number', () => {
    seedReturn({ ...complete, rearVolumeL: 0 });
    expect(importedSubmissionBlocker(useCadReturnStore.getState())).toContain('Rear volume');
    seedReturn({ ...complete, rearVolumeL: 6 });
    expect(importedSubmissionBlocker(useCadReturnStore.getState())).toBeNull();
  });

  it('accepts zero port length and zero foam resistance, which the server bounds with ge', () => {
    seedReturn({ ...complete, portLengthMm: 0, foamResistancePaSM3: 0 });
    expect(importedSubmissionBlocker(useCadReturnStore.getState())).toBeNull();
    const geometry = buildImportedSubmission(useCadReturnStore.getState()).geometry;
    expect(geometry.passive_cardioid_port_length_mm).toBe(0);
    expect(geometry.passive_cardioid_foam_resistance_pa_s_m3).toBe(0);
  });

  it('states a rear volume and never a compliance', () => {
    // chamber_compliance_m3_per_pa is derived by the solver as V/(rho c^2).
    // Offering it as an input would ask for a number the solve computes, and
    // two ways to state the same chamber is one too many.
    seedReturn(complete);
    const keys = Object.keys(
      buildImportedSubmission(useCadReturnStore.getState()).geometry as unknown as Record<string, unknown>,
    );
    expect(keys).toContain('passive_cardioid_rear_volume_l');
    expect(keys.filter((key) => key.includes('complian'))).toEqual([]);
    expect(Object.keys(PASSIVE_CARDIOID_DEFAULTS).filter((key) => /complian/i.test(key))).toEqual([]);
    // The rail offers exactly these five numbers and no sixth way to say the
    // same thing about the chamber.
    expect(CAD_CARDIOID_FIELD_CONTROLS.map(({ formKey }) => formKey)).toEqual([
      'rearVolumeL', 'portLengthMm', 'modelPortAreaM2', 'bemPortAreaM2', 'foamResistancePaSM3',
    ]);
  });

  it('sends the complete set together once the whole form is filled', () => {
    seedReturn(complete);
    expect(buildImportedSubmission(useCadReturnStore.getState()).geometry).toMatchObject({
      passive_cardioid_rear_volume_l: 6,
      passive_cardioid_port_length_mm: 25,
      model_port_area_m2: 0.05,
      bem_port_area_m2: 0.009471859930646809,
      port_area_source: 'user',
      passive_cardioid_foam_resistance_pa_s_m3: 10_000,
      passive_cardioid_invert_port: true,
      passive_cardioid_coupled: false,
    });
  });
});

describe('passive cardioid port areas are two fields', () => {
  beforeEach(() => { resetCadReturnStore(); resetSolveOptionsStore(); });

  it('carries the physical and the BEM area independently', () => {
    seedReturn(complete);
    const geometry = buildImportedSubmission(useCadReturnStore.getState()).geometry;
    // Conflating them is a measured ~40% error on the volume-velocity ratio,
    // so the wire must never resolve one from the other under 'user'.
    expect(geometry.model_port_area_m2).not.toBe(geometry.bem_port_area_m2);
    expect(geometry.model_port_area_m2).toBe(0.05);
    expect(geometry.bem_port_area_m2).toBeCloseTo(0.009471859930646809, 15);
  });

  it('keeps editing one area from moving the other under user provenance', () => {
    seedReturn(complete);
    useCadReturnStore.getState().setPassiveCardioid({ bemPortAreaM2: 0.0123 });
    expect(useCadReturnStore.getState().passiveCardioid.modelPortAreaM2).toBe(0.05);
  });

  it('drives the model area from the BEM area under bem_aperture provenance', () => {
    seedReturn(complete);
    useCadReturnStore.getState().setPassiveCardioid({ portAreaSource: 'bem_aperture' });
    const form = useCadReturnStore.getState().passiveCardioid;
    // The server compares with rel_tol=1e-12, which two typed numbers cannot
    // survive; the equal values here are the same float, not a near miss.
    expect(form.modelPortAreaM2).toBe(form.bemPortAreaM2);

    useCadReturnStore.getState().setPassiveCardioid({ bemPortAreaM2: 0.0074 });
    const geometry = buildImportedSubmission(useCadReturnStore.getState()).geometry;
    expect(geometry.port_area_source).toBe('bem_aperture');
    expect(geometry.model_port_area_m2).toBe(0.0074);
    expect(geometry.bem_port_area_m2).toBe(0.0074);
  });

  it('switching to bem_aperture pulls a drifted model area back into line', () => {
    expect(normalizePassiveCardioid({ ...complete, portAreaSource: 'bem_aperture' })).toMatchObject({
      modelPortAreaM2: complete.bemPortAreaM2,
    });
    // Switching back leaves the value alone: it is the user's again.
    expect(normalizePassiveCardioid({ ...complete, portAreaSource: 'user' }).modelPortAreaM2).toBe(0.05);
  });

  it('re-imposes the equality on the wire even from state that never went through the setter', () => {
    // Restored profiles and future writers do not all pass through
    // setPassiveCardioid, so the builder normalizes too rather than trusting
    // that the two areas were already reconciled somewhere upstream.
    seedReturn({ ...complete, portAreaSource: 'bem_aperture' });
    const geometry = buildImportedSubmission(useCadReturnStore.getState()).geometry;
    expect(geometry.model_port_area_m2).toBe(geometry.bem_port_area_m2);
  });
});

describe('passive cardioid reserved channel id', () => {
  beforeEach(() => { resetCadReturnStore(); resetSolveOptionsStore(); });

  it('withholds the reserved id from the assignable drive channels only when coupled', () => {
    const ids = ['drive-mf', PASSIVE_CARDIOID_CHANNEL_ID];
    expect(assignableChannelIds(ids, true)).toEqual(['drive-mf']);
    expect(assignableChannelIds(ids, false)).toEqual(ids);
  });

  it('blocks a coupled submission whose channel already claims the reserved id', () => {
    seedReturn({ ...complete, coupled: true });
    useCadReturnStore.setState({
      driveChannels: [
        { id: PASSIVE_CARDIOID_CHANNEL_ID, source_ids: ['MF'], motion: 'normal' },
        { id: 'drive-port', source_ids: ['PORT_EXIT'], motion: 'normal' },
      ],
    });
    const blocker = importedSubmissionBlocker(useCadReturnStore.getState());
    expect(blocker).toContain(PASSIVE_CARDIOID_CHANNEL_ID);
    expect(blocker).toContain('Reassign');
    expect(() => buildImportedSubmission(useCadReturnStore.getState())).toThrow(/reserves/);
  });

  it('leaves the same channel id alone when the campaign is not coupled', () => {
    seedReturn({ ...complete, coupled: false });
    useCadReturnStore.setState({
      driveChannels: [
        { id: PASSIVE_CARDIOID_CHANNEL_ID, source_ids: ['MF'], motion: 'normal' },
        { id: 'drive-port', source_ids: ['PORT_EXIT'], motion: 'normal' },
      ],
    });
    expect(importedSubmissionBlocker(useCadReturnStore.getState())).toBeNull();
  });

  it('reports no blocker while the section is off', () => {
    expect(passiveCardioidBlocker({
      passiveCardioid: { ...PASSIVE_CARDIOID_DEFAULTS },
      driveChannels: [{ id: PASSIVE_CARDIOID_CHANNEL_ID, source_ids: ['MF'], motion: 'normal' }],
    })).toBeNull();
  });
});

describe('passive cardioid wire helper', () => {
  it('returns nothing for a disabled or incomplete form', () => {
    expect(passiveCardioidWire({ ...PASSIVE_CARDIOID_DEFAULTS })).toBeNull();
    expect(passiveCardioidWire({ ...complete, enabled: false })).toBeNull();
    expect(passiveCardioidWire({ ...complete, modelPortAreaM2: null })).toBeNull();
  });
});

describe('imported topology refusals', () => {
  it('explains every passive_cardioid_topology condition in terms of a fix', () => {
    const explain = (message: string) => explainImportedRefusal(`passive_cardioid_topology: ${message}`);
    expect(explain('passive cardioid requires an ingestion source tag map')).toContain('Rebuild the mesh');
    expect(explain('passive cardioid requires PORT_EXIT aperture sources')).toContain('PORT_EXIT source');
    expect(explain('passive cardioid requires exactly one MF diaphragm source')).toContain('single source with the MF role');
    expect(explain(
      "coupled passive cardioid requires the MF diaphragm's drive channel to carry one driver model",
    )).toContain('Thiele-Small');
    expect(explain(
      'coupled passive cardioid requires all PORT_EXIT patches in one drive channel',
    )).toContain('same drive channel');
    // The stage label is advice-worthy, not something to read out loud.
    for (const message of [
      'passive cardioid requires PORT_EXIT aperture sources',
      'coupled passive cardioid requires all PORT_EXIT patches in one drive channel',
    ]) expect(explain(message)).not.toContain('passive_cardioid_topology');
  });

  it('passes other refusals through unchanged', () => {
    const other = 'mesh_sizes_mismatch: request mesh sizes do not match the sizes used to create the ingestion artifact';
    expect(explainImportedRefusal(other)).toBe(other);
    expect(explainImportedRefusal('passive_cardioid_topology: something nobody has mapped yet'))
      .toBe('passive_cardioid_topology: something nobody has mapped yet');
  });
});
