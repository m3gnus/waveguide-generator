import { create, type StoreApi, type UseBoundStore } from 'zustand';
import {
  buildFieldPlaneRequest,
  fetchFieldPlane,
  FieldPlaneHttpError,
  type DecodedFieldPlane,
  type FieldPlaneSpec,
} from '../api/fieldPlane';
import { fetchJobResults, type JobResults } from '../api/results';
import type { FrameScene } from './frameScene';

export type FieldPlaneStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface FieldPlaneStore {
  enabled: boolean;
  jobId: string | null;
  plane: FieldPlaneSpec | null;
  frequencyIndex: number;
  frequenciesHz: number[];
  status: FieldPlaneStatus;
  error: string | null;
  field: DecodedFieldPlane | null;
  enable: (jobId: string, defaultPlane: FieldPlaneSpec) => void;
  selectJob: (jobId: string, defaultPlane: FieldPlaneSpec) => void;
  disable: () => void;
  setFrequencyIndex: (frequencyIndex: number) => void;
  reportUnavailable: (reason: string) => void;
  retry: () => void;
}

interface RememberedPlane {
  plane: FieldPlaneSpec;
  frequencyIndex: number | null;
}

interface FieldPlaneStoreDependencies {
  fetchPlane?: typeof fetchFieldPlane;
  fetchResults?: (jobId: string) => Promise<JobResults>;
}

const TARGET_FREQUENCY_HZ = 1_000;
let requestCounter = 0;

function nextRequestId(): string {
  requestCounter += 1;
  return `field-plane-${Date.now().toString(36)}-${requestCounter.toString(36)}`;
}

export function nearestFieldPlaneFrequencyIndex(frequenciesHz: readonly number[], targetHz = TARGET_FREQUENCY_HZ): number {
  if (!frequenciesHz.length) return 0;
  let nearest = 0;
  let distance = Math.abs(frequenciesHz[0] - targetHz);
  for (let index = 1; index < frequenciesHz.length; index += 1) {
    const nextDistance = Math.abs(frequenciesHz[index] - targetHz);
    if (nextDistance < distance) {
      nearest = index;
      distance = nextDistance;
    }
  }
  return nearest;
}

function planeSpan(minimum: number, maximum: number, unitsPerMetre: number): number {
  const size = Math.max(0, maximum - minimum);
  const extentFromOrigin = Math.max(Math.abs(minimum), Math.abs(maximum));
  const sceneSpan = Math.max(size * 2, extentFromOrigin * 2, unitsPerMetre * 0.01);
  return Math.min(100, sceneSpan / unitsPerMetre);
}

/** Default H-plane in solver metres. A FrameScene declares the only unit
 * conversion used here; the request itself never contains viewport units. */
export function defaultFieldPlane(scene: FrameScene): FieldPlaneSpec {
  return {
    origin_m: [0, 0, 0],
    axis_u: [1, 0, 0],
    axis_v: [0, 0, 1],
    width_m: planeSpan(scene.bounds.min.x, scene.bounds.max.x, scene.unitsPerMetre),
    height_m: planeSpan(scene.bounds.min.z, scene.bounds.max.z, scene.unitsPerMetre),
    nx: 96,
    ny: 96,
  };
}

export function fieldPlaneErrorMessage(reason: unknown): string {
  if (reason instanceof FieldPlaneHttpError) {
    switch (reason.status) {
      case 404: return 'field-plane result was not found';
      case 409: return 'field-plane result changed; select the completed run again';
      case 410: return 're-solve to enable field planes';
      case 422: return 'field planes are unavailable for this solve';
      case 429: return 'field-plane request was superseded; try again';
      case 503: return 'waiting for solve to finish';
      case 504: return 'field-plane evaluation timed out';
      default: return reason.status >= 400 && reason.status < 500
        ? 'field-plane request was rejected'
        : 'field-plane evaluation failed';
    }
  }
  if (reason instanceof Error && reason.name === 'AbortError') return 'field-plane request cancelled';
  if (reason instanceof TypeError) return 'could not connect to the field-plane service';
  return reason instanceof Error && reason.message
    ? `field-plane response error: ${reason.message}`
    : 'field-plane evaluation failed';
}

export function createFieldPlaneStore(
  dependencies: FieldPlaneStoreDependencies = {},
): UseBoundStore<StoreApi<FieldPlaneStore>> {
  const fetchPlane = dependencies.fetchPlane ?? fetchFieldPlane;
  const fetchResults = dependencies.fetchResults ?? fetchJobResults;
  const remembered = new Map<string, RememberedPlane>();
  let activationGeneration = 0;
  let requestController: AbortController | null = null;

  return create<FieldPlaneStore>((set, get) => {
    const load = (jobId: string, plane: FieldPlaneSpec, frequencyIndex: number): void => {
      requestController?.abort();
      const controller = new AbortController();
      requestController = controller;
      const request = buildFieldPlaneRequest({
        requestId: nextRequestId(),
        plane,
        frequencyIndex,
      });
      set({ status: 'loading', error: null, field: null });
      void fetchPlane(jobId, request, fetch, controller.signal)
        .then((field) => {
          if (controller.signal.aborted) return;
          const current = get();
          if (!current.enabled || current.jobId !== jobId || current.frequencyIndex !== frequencyIndex) return;
          if (
            field.header.request_id !== request.request_id
            || field.header.job_id !== jobId
            || field.header.frequency_index !== frequencyIndex
            || field.header.nx !== plane.nx
            || field.header.ny !== plane.ny
          ) throw new Error('response metadata does not match the field-plane request');
          set({ status: 'ready', error: null, field });
        })
        .catch((reason: unknown) => {
          if (controller.signal.aborted) return;
          const current = get();
          if (!current.enabled || current.jobId !== jobId || current.frequencyIndex !== frequencyIndex) return;
          set({ status: 'error', error: fieldPlaneErrorMessage(reason), field: null });
        });
    };

    const activate = (jobId: string, suppliedDefault: FieldPlaneSpec): void => {
      activationGeneration += 1;
      const generation = activationGeneration;
      requestController?.abort();
      requestController = null;
      const saved = remembered.get(jobId);
      const plane = saved?.plane ?? suppliedDefault;
      set({
        enabled: true,
        jobId,
        plane,
        frequencyIndex: saved?.frequencyIndex ?? 0,
        frequenciesHz: [],
        status: 'loading',
        error: null,
        field: null,
      });
      void fetchResults(jobId)
        .then((results) => {
          const frequenciesHz = [...results.frequencies];
          if (!frequenciesHz.length || frequenciesHz.some((value) => !Number.isFinite(value) || value <= 0)) {
            throw new Error('the selected result has no valid solved frequencies');
          }
          const current = get();
          if (generation !== activationGeneration || !current.enabled || current.jobId !== jobId) return;
          const frequencyIndex = saved?.frequencyIndex === null || saved?.frequencyIndex === undefined
            ? nearestFieldPlaneFrequencyIndex(frequenciesHz)
            : Math.max(0, Math.min(frequenciesHz.length - 1, saved.frequencyIndex));
          remembered.set(jobId, { plane, frequencyIndex });
          set({ frequenciesHz, frequencyIndex });
          load(jobId, plane, frequencyIndex);
        })
        .catch((reason: unknown) => {
          const current = get();
          if (generation !== activationGeneration || !current.enabled || current.jobId !== jobId) return;
          set({ status: 'error', error: fieldPlaneErrorMessage(reason), field: null });
        });
    };

    return {
      enabled: false,
      jobId: null,
      plane: null,
      frequencyIndex: 0,
      frequenciesHz: [],
      status: 'idle',
      error: null,
      field: null,
      enable: activate,
      selectJob: (jobId, suppliedDefault) => {
        const current = get();
        if (current.enabled && current.jobId === jobId) return;
        activate(jobId, suppliedDefault);
      },
      disable: () => {
        activationGeneration += 1;
        requestController?.abort();
        requestController = null;
        set({
          enabled: false,
          jobId: null,
          plane: null,
          frequencyIndex: 0,
          frequenciesHz: [],
          status: 'idle',
          error: null,
          field: null,
        });
      },
      setFrequencyIndex: (frequencyIndex) => {
        const current = get();
        if (
          !current.enabled
          || !current.jobId
          || !current.plane
          || !Number.isSafeInteger(frequencyIndex)
          || frequencyIndex < 0
          || frequencyIndex >= current.frequenciesHz.length
          || frequencyIndex === current.frequencyIndex
        ) return;
        remembered.set(current.jobId, { plane: current.plane, frequencyIndex });
        set({ frequencyIndex });
        load(current.jobId, current.plane, frequencyIndex);
      },
      reportUnavailable: (reason) => {
        activationGeneration += 1;
        requestController?.abort();
        requestController = null;
        set({ jobId: null, plane: null, frequenciesHz: [], status: 'error', error: reason, field: null });
      },
      retry: () => {
        const current = get();
        if (!current.enabled || !current.jobId || !current.plane || !current.frequenciesHz.length) return;
        load(current.jobId, current.plane, current.frequencyIndex);
      },
    };
  });
}

export const useFieldPlaneStore = createFieldPlaneStore();
