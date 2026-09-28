import { beforeEach, describe, expect, it } from 'vitest';
import type { CadReturnBundle } from '../api/cadlink';
import { resetCadPreparationStore, useCadPreparationStore } from '../stores/cadPreparation';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDocumentStore } from '../stores/document';
import { DEFAULT_SOLVE_OPTIONS, resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import sharedDefaults from '../../../shared/solve-defaults.json';
import hfOnly from '../../../server/tests/fixtures/cad_default_setup/hf-only.json';
import lfMfHf from '../../../server/tests/fixtures/cad_default_setup/three-way-lf-mf-hf.json';
import threeWay from '../../../server/tests/fixtures/cad_default_setup/three-way-shared-channel.json';
import twoWay from '../../../server/tests/fixtures/cad_default_setup/two-way-accurate.json';
import { buildCadProjectSetup } from './cadSetupPublisher';

/**
 * WG's default settings are stated once, in shared/solve-defaults.json. A
 * first-time model Fusion sends is solved by the backend with them
 * (server/cadlink/default_setup.py); a first-time model solved here starts
 * from them. These fixtures are what the backend builds -- its own test,
 * server/tests/test_cad_default_setup.py, checks it against the same files --
 * so an untouched rail must record exactly the same setup.
 */
interface Fixture {
  sources: Array<{
    id: string;
    role: string;
    required: boolean;
    suggested_resolution_mm: number;
    default_drive_channel_id: string;
  }>;
  selection: { engine: string; accuracy: 'fast' | 'accurate' };
  setup: unknown;
}

function bundleOf(fixture: Fixture): CadReturnBundle {
  return {
    name: 'model.wgreturn',
    bundlePath: 'wgreturn/model.wgreturn',
    modifiedAt: '2026-09-28T10:00:00Z',
    readable: true,
    documentName: 'Model',
    requestId: null,
    sourceCount: fixture.sources.length,
    instanceCount: 1,
    designIds: [],
    sources: fixture.sources.map((source) => ({
      id: source.id,
      role: source.role,
      required: source.required,
      suggestedResolutionMm: source.suggested_resolution_mm,
      defaultDriveChannelId: source.default_drive_channel_id,
    })),
  };
}

describe('WG default CAD setup parity', () => {
  beforeEach(() => {
    localStorage.clear();
    resetCadReturnStore();
    resetCadPreparationStore();
    resetDocumentStore();
    resetSolveOptionsStore();
  });

  it.each([
    ['hf-only', hfOnly],
    ['two-way-accurate', twoWay],
    ['three-way-lf-mf-hf', lfMfHf],
    ['three-way-shared-channel', threeWay],
  ])('an untouched rail records the backend default setup: %s', (_name, raw) => {
    const fixture = raw as unknown as Fixture;
    useCadReturnStore.getState().selectBundle(bundleOf(fixture), 'wgl_model');
    useSolveOptionsStore.setState({ engine: fixture.selection.engine, accuracy: fixture.selection.accuracy });

    const built = buildCadProjectSetup(
      useCadReturnStore.getState(),
      useSolveOptionsStore.getState(),
      useCadPreparationStore.getState(),
      'wgl_model',
    );

    expect(built?.setup).toEqual(fixture.setup);
  });

  it('starts every store from the shared file', () => {
    const cad = useCadReturnStore.getState();
    expect([cad.frequencyStartHz, cad.frequencyEndHz, cad.frequencyCount])
      .toEqual([sharedDefaults.sweep.start_hz, sharedDefaults.sweep.end_hz, sharedDefaults.sweep.points]);
    expect(cad.driveVoltageV).toBe(sharedDefaults.cad.drive_voltage_v);
    expect(cad.exteriorOnly).toBe(sharedDefaults.cad.exterior_only);
    expect(useCadPreparationStore.getState().symmetryMode).toBe(sharedDefaults.cad.preparation_symmetry_mode);
    expect(DEFAULT_SOLVE_OPTIONS.polar).toEqual({
      angleStart: sharedDefaults.directivity.angle_start_deg,
      angleEnd: sharedDefaults.directivity.angle_end_deg,
      angleStep: sharedDefaults.directivity.angle_step_deg,
      distance: sharedDefaults.directivity.distance_m,
      normAngle: sharedDefaults.directivity.norm_angle_deg,
      diagonalAngle: sharedDefaults.directivity.diagonal_inclination_deg,
      enabledAxes: sharedDefaults.directivity.enabled_axes,
      observationOrigin: sharedDefaults.directivity.observation_origin,
      sphericalSampling: sharedDefaults.directivity.spherical_sampling,
      fieldPlane: sharedDefaults.directivity.field_plane,
    });
    expect(DEFAULT_SOLVE_OPTIONS.engine).toBe(sharedDefaults.solver.engine);
    expect(DEFAULT_SOLVE_OPTIONS.accuracy).toBe(sharedDefaults.solver.accuracy);
    expect(DEFAULT_SOLVE_OPTIONS.frequencySpacing).toBe(sharedDefaults.sweep.spacing);
    expect(DEFAULT_SOLVE_OPTIONS.groundPlane).toEqual(sharedDefaults.ground_plane);
  });
});
