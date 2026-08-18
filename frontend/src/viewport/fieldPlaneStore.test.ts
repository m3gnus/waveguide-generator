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
import { DEFAULT_VIEWER_PREFERENCES } from '../viewerprefs/viewerPreferences';
import { maxFieldSplDb } from './fieldPlaneColor';
import {
  createFieldPlaneStore,
  defaultFieldPlane,
  fieldPlaneCacheKey,
  fieldPlaneErrorMessage,
  FieldPlaneLruCache,
  LatestFieldPlaneRequestQueue,
  nearestFieldPlaneFrequencyIndex,
  shouldApplyFieldPlaneGeneration,
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

function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolvePromise: (() => void) | undefined;
  const promise = new Promise<void>((resolve) => { resolvePromise = resolve; });
  return { promise, resolve: () => resolvePromise?.() };
}

describe('field-plane state', () => {
  it('keeps display, lock, animation, and isoline state client-side', async () => {
    const updatePreferences = vi.fn();
    const fetchPlane = vi.fn(async (jobId: string, request: FieldPlaneRequest) => response(jobId, request));
    const store = createFieldPlaneStore({
      fetchPlane,
      fetchResults: async () => ({ frequencies: [800] }),
      preferences: {
        getSnapshot: () => ({
          ...DEFAULT_VIEWER_PREFERENCES,
          fieldPlaneDisplayMode: 'phase',
          fieldPlaneRangeLocked: true,
          fieldPlaneAnimationSpeed: 1.8,
        }),
        update: updatePreferences,
      },
    });

    expect(store.getState()).toMatchObject({
      displayMode: 'phase',
      rangeLocked: true,
      animationSpeed: 1.8,
      animating: false,
      isolines: false,
    });

    store.getState().enable('job-1', plane);
    await vi.waitFor(() => expect(store.getState().status).toBe('ready'));
    store.getState().setIsolines(true);
    store.getState().setAnimating(true);
    store.getState().setAnimationSpeed(2.5);

    expect(store.getState()).toMatchObject({
      displayMode: 'instantaneous',
      animating: true,
      isolines: true,
      animationSpeed: 2.5,
    });
    expect(fetchPlane).toHaveBeenCalledOnce();
    expect(updatePreferences).toHaveBeenCalledWith({ fieldPlaneDisplayMode: 'instantaneous' });
    expect(updatePreferences).toHaveBeenCalledWith({ fieldPlaneAnimationSpeed: 2.5 });

    store.getState().setDisplayMode('spl');
    expect(store.getState()).toMatchObject({ displayMode: 'spl', animating: false });
    store.getState().disable();
    expect(store.getState().animating).toBe(false);
  });

  it('freezes dynamic per-mode windows while locked and refreshes them on unlock', async () => {
    const fetchPlane = vi.fn(async (jobId: string, request: FieldPlaneRequest) => {
      const field = response(jobId, request);
      field.real[0] = request.frequency_index === 1 ? 1 : 10;
      return field;
    });
    const store = createFieldPlaneStore({
      fetchPlane,
      fetchResults: async () => ({ frequencies: [200, 800, 1_600] }),
      preferences: {
        getSnapshot: () => ({ ...DEFAULT_VIEWER_PREFERENCES }),
        update: vi.fn(),
      },
    });

    store.getState().enable('job-1', plane);
    await vi.waitFor(() => expect(store.getState().status).toBe('ready'));
    const splMaximum = store.getState().windows.spl?.maximum;
    const pressureMaximum = store.getState().windows.instantaneous?.maximum;
    store.getState().setRangeLocked(true);
    store.getState().setFrequencyIndex(2);
    await vi.waitFor(() => expect(store.getState().field?.header.frequency_index).toBe(2));

    expect(store.getState().windows.spl?.maximum).toBe(splMaximum);
    expect(store.getState().windows.instantaneous?.maximum).toBe(pressureMaximum);

    store.getState().setRangeLocked(false);
    expect(store.getState().windows.spl?.maximum).toBeGreaterThan(splMaximum ?? -Infinity);
    expect(store.getState().windows.instantaneous?.maximum).toBe(10);
  });

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

    store.getState().setFrequencyIndex(1);
    expect(store.getState().field?.header.frequency_index).toBe(1);
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

  it('fully disables the overlay when its selected job becomes unavailable', async () => {
    const store = createFieldPlaneStore({
      fetchPlane: async (jobId, request) => response(jobId, request),
      fetchResults: async () => ({ frequencies: [800] }),
    });
    store.getState().enable('job-1', plane);
    await vi.waitFor(() => expect(store.getState().status).toBe('ready'));
    store.getState().setAnimating(true);

    store.getState().reportUnavailable('disabled by selected job');

    expect(store.getState()).toMatchObject({
      enabled: false,
      jobId: null,
      plane: null,
      field: null,
      animating: false,
      error: 'disabled by selected job',
    });
  });

  it('keeps one drag request running and collapses pending transforms to the latest', async () => {
    const dragGate = deferred();
    const fetchPlane = vi.fn(async (jobId: string, request: FieldPlaneRequest) => {
      if (request.plane.nx === 48 && request.plane.origin_m[0] === 0.1) {
        await dragGate.promise;
      }
      return response(jobId, request);
    });
    const store = createFieldPlaneStore({
      fetchPlane,
      fetchResults: async () => ({ frequencies: [800] }),
    });
    store.getState().enable('job-1', plane);
    await vi.waitFor(() => expect(store.getState().status).toBe('ready'));

    store.getState().beginPlaneDrag();
    store.getState().updatePlaneDrag({ ...plane, origin_m: [0.1, 0, 0] });
    await vi.waitFor(() => expect(fetchPlane).toHaveBeenCalledTimes(2));
    store.getState().updatePlaneDrag({ ...plane, origin_m: [0.2, 0, 0] });
    store.getState().updatePlaneDrag({ ...plane, origin_m: [0.3, 0, 0] });
    expect(fetchPlane).toHaveBeenCalledTimes(2);

    dragGate.resolve();
    await vi.waitFor(() => expect(fetchPlane).toHaveBeenCalledTimes(3));
    expect(fetchPlane.mock.calls[2][1].plane.origin_m).toEqual([0.3, 0, 0]);
    expect(fetchPlane.mock.calls[2][1].plane.nx).toBe(48);

    await vi.waitFor(() => expect(store.getState().status).toBe('ready'));
    store.getState().endPlaneDrag();
    await vi.waitFor(() => expect(fetchPlane).toHaveBeenCalledTimes(4));
    expect(fetchPlane.mock.calls[3][1].plane.origin_m).toEqual([0.3, 0, 0]);
    expect(fetchPlane.mock.calls[3][1].plane.nx).toBe(96);
  });

  it('freezes the normalized reference across drag fields and releases it on drag end', async () => {
    const fetchPlane = vi.fn(async (jobId: string, request: FieldPlaneRequest) => {
      const field = response(jobId, request);
      field.real[0] = request.plane.nx === 48 ? 10 : request.plane.origin_m[0] === 0 ? 1 : 100;
      return field;
    });
    const store = createFieldPlaneStore({
      fetchPlane,
      fetchResults: async () => ({ frequencies: [800] }),
    });
    store.getState().enable('job-1', plane);
    await vi.waitFor(() => expect(store.getState().status).toBe('ready'));
    const initialReference = maxFieldSplDb(
      store.getState().field?.real ?? new Float32Array(),
      store.getState().field?.imag ?? new Float32Array(),
    );

    store.getState().beginPlaneDrag();
    expect(store.getState().frozenNormalizationDb).toBeCloseTo(initialReference, 5);
    store.getState().updatePlaneDrag({ ...plane, origin_m: [0.1, 0, 0] });
    await vi.waitFor(() => expect(store.getState().field?.header.nx).toBe(48));

    expect(maxFieldSplDb(
      store.getState().field?.real ?? new Float32Array(),
      store.getState().field?.imag ?? new Float32Array(),
    )).toBeGreaterThan(initialReference);
    expect(store.getState().frozenNormalizationDb).toBeCloseTo(initialReference, 5);

    store.getState().endPlaneDrag();
    expect(store.getState().frozenNormalizationDb).toBeNull();
    await vi.waitFor(() => expect(store.getState().field?.header.nx).toBe(96));
    expect(store.getState().frozenNormalizationDb).toBeNull();
  });

  it('never freezes normalization for setPlane updates', async () => {
    const store = createFieldPlaneStore({
      fetchPlane: async (jobId, request) => {
        const field = response(jobId, request);
        field.real[0] = request.plane.origin_m[0] === 0 ? 1 : 2;
        return field;
      },
      fetchResults: async () => ({ frequencies: [800] }),
    });
    store.getState().enable('job-1', plane);
    await vi.waitFor(() => expect(store.getState().status).toBe('ready'));

    store.getState().setPlane({ ...plane, origin_m: [0.1, 0, 0] });
    expect(store.getState()).toMatchObject({ dragging: false, frozenNormalizationDb: null });
    await vi.waitFor(() => expect(store.getState().field?.real[0]).toBe(2));
    expect(store.getState().frozenNormalizationDb).toBeNull();

    store.getState().beginPlaneDrag();
    expect(store.getState().frozenNormalizationDb).not.toBeNull();
    store.getState().setPlane(plane);
    expect(store.getState()).toMatchObject({ dragging: false, frozenNormalizationDb: null });
  });

  it('clears a frozen normalization reference on cancellation, job change, and disable', async () => {
    const store = createFieldPlaneStore({
      fetchPlane: async (jobId, request) => {
        const field = response(jobId, request);
        field.real[0] = jobId === 'job-1' ? 1 : 2;
        return field;
      },
      fetchResults: async () => ({ frequencies: [800] }),
    });
    store.getState().enable('job-1', plane);
    await vi.waitFor(() => expect(store.getState().status).toBe('ready'));

    store.getState().beginPlaneDrag();
    expect(store.getState().frozenNormalizationDb).not.toBeNull();
    store.getState().cancelPending();
    expect(store.getState().frozenNormalizationDb).toBeNull();

    store.getState().beginPlaneDrag();
    store.getState().selectJob('job-2', plane);
    expect(store.getState().frozenNormalizationDb).toBeNull();
    await vi.waitFor(() => expect(store.getState().jobId).toBe('job-2'));
    await vi.waitFor(() => expect(store.getState().status).toBe('ready'));

    store.getState().beginPlaneDrag();
    expect(store.getState().frozenNormalizationDb).not.toBeNull();
    store.getState().disable();
    expect(store.getState().frozenNormalizationDb).toBeNull();
  });

  it('does not let an older in-flight response replace a newer cached field', async () => {
    const movedGate = deferred();
    const fetchPlane = vi.fn(async (jobId: string, request: FieldPlaneRequest) => {
      if (request.plane.nx === 48 && request.plane.origin_m[0] === 0.1) {
        await movedGate.promise;
      }
      const field = response(jobId, request);
      field.real[0] = request.plane.origin_m[0];
      return field;
    });
    const store = createFieldPlaneStore({
      fetchPlane,
      fetchResults: async () => ({ frequencies: [800] }),
    });
    store.getState().enable('job-1', plane);
    await vi.waitFor(() => expect(store.getState().status).toBe('ready'));
    store.getState().beginPlaneDrag();
    store.getState().updatePlaneDrag(plane);
    await vi.waitFor(() => expect(store.getState().field?.header.nx).toBe(48));

    store.getState().updatePlaneDrag({ ...plane, origin_m: [0.1, 0, 0] });
    await vi.waitFor(() => expect(fetchPlane).toHaveBeenCalledTimes(3));
    store.getState().updatePlaneDrag(plane);
    const cachedGeneration = store.getState().lastAppliedGeneration;
    expect(store.getState().field?.real[0]).toBe(0);

    movedGate.resolve();
    await vi.waitFor(() => expect(store.getState().status).toBe('ready'));
    expect(store.getState().lastAppliedGeneration).toBe(cachedGeneration);
    expect(store.getState().field?.real[0]).toBe(0);
  });

  it('clears cached transforms when the response geometry hash changes', async () => {
    const fetchPlane = vi.fn(async (jobId: string, request: FieldPlaneRequest) => {
      const field = response(jobId, request);
      field.header.geometry_sha256 = request.plane.origin_m[0] === 0 ? 'geometry-a' : 'geometry-b';
      return field;
    });
    const store = createFieldPlaneStore({
      fetchPlane,
      fetchResults: async () => ({ frequencies: [800] }),
    });
    store.getState().enable('job-1', plane);
    await vi.waitFor(() => expect(store.getState().status).toBe('ready'));
    store.getState().setPlane({ ...plane, origin_m: [0.1, 0, 0] });
    await vi.waitFor(() => expect(store.getState().geometrySha256).toBe('geometry-b'));
    store.getState().setPlane(plane);
    await vi.waitFor(() => expect(fetchPlane).toHaveBeenCalledTimes(3));
  });
});

describe('field-plane cache and request primitives', () => {
  it('quantizes cache transforms to 1e-6 and separates geometry and grid identity', () => {
    const parts = {
      jobId: 'job-1',
      geometrySha256: 'geometry-a',
      responseId: 'system' as const,
      frequencyIndex: 2,
      plane,
    };
    const key = fieldPlaneCacheKey(parts);
    expect(fieldPlaneCacheKey({
      ...parts,
      plane: { ...plane, origin_m: [0.0000004, 0, 0] },
    })).toBe(key);
    expect(fieldPlaneCacheKey({ ...parts, geometrySha256: 'geometry-b' })).not.toBe(key);
    expect(fieldPlaneCacheKey({ ...parts, plane: { ...plane, nx: 48, ny: 48 } })).not.toBe(key);
  });

  it('evicts the least-recently-used decoded field', () => {
    const cache = new FieldPlaneLruCache<number>(2);
    cache.set('a', 1);
    cache.set('b', 2);
    expect(cache.get('a')).toBe(1);
    cache.set('c', 3);
    expect(cache.get('b')).toBeUndefined();
    expect(cache.get('a')).toBe(1);
    expect(cache.get('c')).toBe(3);
  });

  it('collapses queued work and rejects stale generations', () => {
    const queue = new LatestFieldPlaneRequestQueue<string>();
    expect(queue.enqueue('running')).toBe('running');
    expect(queue.enqueue('old pending')).toBeNull();
    expect(queue.enqueue('latest pending')).toBeNull();
    expect(queue.complete('running')).toBe('latest pending');
    expect(shouldApplyFieldPlaneGeneration(3, 4)).toBe(false);
    expect(shouldApplyFieldPlaneGeneration(4, 4)).toBe(true);
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
