import { Box3, Vector3 } from 'three';
import { describe, expect, it, vi } from 'vitest';
import {
  FIELD_PLANE_ORDERING,
  FieldPlaneHttpError,
  type FieldPlaneRequest,
  type FieldPlaneSpec,
  type DecodedFieldPlane,
} from '../api/fieldPlane';
import type { FrameScene } from './frameScene';
import {
  createFieldPlaneStore,
  defaultFieldPlane,
  fieldPlaneErrorMessage,
  nearestFieldPlaneFrequencyIndex,
} from './fieldPlaneStore';

const plane: FieldPlaneSpec = {
  origin_m: [0, 0, 0],
  axis_u: [1, 0, 0],
  axis_v: [0, 0, 1],
  width_m: 0.4,
  height_m: 0.8,
  nx: 96,
  ny: 96,
};

function response(jobId: string, request: FieldPlaneRequest): DecodedFieldPlane {
  const count = request.plane.nx * request.plane.ny;
  return {
    header: {
      version: 1,
      request_id: request.request_id,
      job_id: jobId,
      frequency_index: request.frequency_index,
      frequency_hz: [200, 800, 1_600][request.frequency_index] ?? 200,
      nx: request.plane.nx,
      ny: request.plane.ny,
      ordering: FIELD_PLANE_ORDERING,
      phase_convention: 'solver_exp_plus_ikr',
      pressure_unit: 'Pa',
      response_id: 'system',
      geometry_sha256: 'geometry',
    },
    real: new Float32Array(count),
    imag: new Float32Array(count),
  };
}

describe('field-plane state', () => {
  it('chooses the solved frequency nearest 1 kHz and refetches on selection change', async () => {
    const fetchPlane = vi.fn(async (jobId: string, request: FieldPlaneRequest) => response(jobId, request));
    const store = createFieldPlaneStore({
      fetchPlane,
      fetchResults: async () => ({ frequencies: [200, 800, 1_600] }),
    });

    store.getState().enable('job-1', plane);
    await vi.waitFor(() => expect(store.getState().status).toBe('ready'));
    expect(store.getState().frequencyIndex).toBe(1);
    expect(fetchPlane).toHaveBeenCalledTimes(1);

    store.getState().setFrequencyIndex(2);
    await vi.waitFor(() => expect(store.getState().status).toBe('ready'));
    expect(store.getState().field?.header.frequency_index).toBe(2);
    expect(fetchPlane).toHaveBeenCalledTimes(2);
  });

  it('remembers the ephemeral frequency selection for the same job', async () => {
    const fetchPlane = vi.fn(async (jobId: string, request: FieldPlaneRequest) => response(jobId, request));
    const store = createFieldPlaneStore({
      fetchPlane,
      fetchResults: async () => ({ frequencies: [200, 800, 1_600] }),
    });

    store.getState().enable('job-1', plane);
    await vi.waitFor(() => expect(store.getState().status).toBe('ready'));
    store.getState().setFrequencyIndex(2);
    await vi.waitFor(() => expect(store.getState().status).toBe('ready'));
    store.getState().disable();
    store.getState().enable('job-1', { ...plane, width_m: 9 });
    await vi.waitFor(() => expect(store.getState().status).toBe('ready'));

    expect(store.getState().frequencyIndex).toBe(2);
    expect(store.getState().plane?.width_m).toBe(plane.width_m);
  });
});

describe('field-plane defaults and status copy', () => {
  it('uses explicit parametric scene units when sizing the origin-centred H-plane', () => {
    const scene: FrameScene = {
      surfaces: [],
      bounds: new Box3(new Vector3(-50, -30, 0), new Vector3(50, 30, 200)),
      unitsPerMetre: 1_000,
      hasCurvature: false,
    };

    expect(defaultFieldPlane(scene)).toMatchObject({
      origin_m: [0, 0, 0],
      axis_u: [1, 0, 0],
      axis_v: [0, 0, 1],
      width_m: 0.2,
      height_m: 0.4,
      nx: 96,
      ny: 96,
    });
    expect(nearestFieldPlaneFrequencyIndex([100, 900, 1_500])).toBe(1);
  });

  it('maps every server error class to actionable viewport copy', () => {
    expect(fieldPlaneErrorMessage(new FieldPlaneHttpError(404, 'missing'))).toBe('field-plane result was not found');
    expect(fieldPlaneErrorMessage(new FieldPlaneHttpError(409, 'stale'))).toContain('changed');
    expect(fieldPlaneErrorMessage(new FieldPlaneHttpError(410, 'pruned'))).toBe('re-solve to enable field planes');
    expect(fieldPlaneErrorMessage(new FieldPlaneHttpError(422, 'unsupported'))).toContain('unavailable');
    expect(fieldPlaneErrorMessage(new FieldPlaneHttpError(429, 'superseded'))).toContain('superseded');
    expect(fieldPlaneErrorMessage(new FieldPlaneHttpError(503, 'busy'))).toBe('waiting for solve to finish');
    expect(fieldPlaneErrorMessage(new FieldPlaneHttpError(504, 'timeout'))).toContain('timed out');
  });
});
