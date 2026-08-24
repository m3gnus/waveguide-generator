import { create, type StoreApi, type UseBoundStore } from 'zustand';
import {
  buildFieldPlaneRequest,
  fetchFieldPlane,
  FieldPlaneHttpError,
  type DecodedFieldPlane,
  type FieldPlaneRequest,
  type FieldPlaneResponseId,
  type FieldPlaneSpec,
} from '../api/fieldPlane';
import { fetchJobResults, type ResultData } from '../api/results';
import { channelLabel } from '../results/channelLabel';
import { combinedChannelId, combineMetadataOf, type ResultPayload } from '../results/types';
import { viewerPreferences, type ViewerPreferences } from '../viewerprefs/viewerPreferences';
import type { FrameScene } from './frameScene';
import {
  defaultFieldPlaneWindow,
  fieldPlaneWindowForMode,
  maxFieldSplDb,
  type FieldPlaneDisplayMode,
  type FieldPlaneValueWindow,
} from './fieldPlaneColor';
import { fieldPlanePreset } from './fieldPlaneMath';

export type FieldPlaneStatus = 'idle' | 'loading' | 'ready' | 'error';

/** One selectable per-driver view: a synthesis member with its display name. */
export interface FieldPlaneMemberResponse {
  id: string;
  label: string;
}

export type FieldPlaneWindows = Record<FieldPlaneDisplayMode, FieldPlaneValueWindow | null>;

export interface FieldPlaneRenderingPreferences {
  fieldPlaneDisplayMode: FieldPlaneDisplayMode;
  fieldPlaneRangeLocked: boolean;
  fieldPlaneAnimationSpeed: number;
}

export interface FieldPlaneStore {
  enabled: boolean;
  dragging: boolean;
  jobId: string | null;
  geometrySha256: string | null;
  synthesisRevision: string | null;
  plane: FieldPlaneSpec | null;
  frequencyIndex: number;
  frequenciesHz: number[];
  responseId: FieldPlaneResponseId;
  memberResponses: FieldPlaneMemberResponse[];
  generation: number;
  lastAppliedGeneration: number;
  status: FieldPlaneStatus;
  error: string | null;
  lastErrorCode: string | null;
  lastSupersededBy: string | null;
  field: DecodedFieldPlane | null;
  frozenNormalizationDb: number | null;
  displayMode: FieldPlaneDisplayMode;
  rangeLocked: boolean;
  animationSpeed: number;
  animating: boolean;
  isolines: boolean;
  windows: FieldPlaneWindows;
  enable: (jobId: string, defaultPlane: FieldPlaneSpec) => void;
  selectJob: (jobId: string, defaultPlane: FieldPlaneSpec) => void;
  disable: () => void;
  setFrequencyIndex: (frequencyIndex: number) => void;
  setResponseId: (responseId: FieldPlaneResponseId) => void;
  setPlane: (plane: FieldPlaneSpec) => void;
  beginPlaneDrag: () => void;
  updatePlaneDrag: (plane: FieldPlaneSpec) => void;
  endPlaneDrag: (plane?: FieldPlaneSpec) => void;
  cancelPending: () => void;
  resume: () => void;
  reportUnavailable: (reason: string) => void;
  retry: () => void;
  retryIfBlocked: () => void;
  invalidateSynthesis: (revision?: string) => void;
  setDisplayMode: (mode: FieldPlaneDisplayMode) => void;
  setRangeLocked: (locked: boolean) => void;
  setAnimationSpeed: (speed: number) => void;
  setAnimating: (animating: boolean) => void;
  setIsolines: (enabled: boolean) => void;
  applyRenderingPreferences: (preferences: FieldPlaneRenderingPreferences) => void;
}

interface RememberedPlane {
  plane: FieldPlaneSpec;
  frequencyIndex: number | null;
  responseId: FieldPlaneResponseId;
}

interface FieldPlaneStoreDependencies {
  fetchPlane?: typeof fetchFieldPlane;
  fetchResults?: (jobId: string) => Promise<ResultData>;
  cacheCapacity?: number;
  preferences?: {
    getSnapshot: () => ViewerPreferences;
    update: (patch: Partial<ViewerPreferences>) => void;
  };
}

export interface FieldPlaneCacheKeyParts {
  jobId: string;
  geometrySha256: string;
  synthesisRevision: string | null;
  responseId: FieldPlaneResponseId;
  frequencyIndex: number;
  plane: FieldPlaneSpec;
}

interface PendingFieldPlaneRequest {
  generation: number;
  jobId: string;
  plane: FieldPlaneSpec;
  frequencyIndex: number;
  responseId: FieldPlaneResponseId;
  coarsened: boolean;
  request: FieldPlaneRequest;
}

type DisplayedFieldPlaneRequest = Pick<
  PendingFieldPlaneRequest,
  'jobId' | 'plane' | 'frequencyIndex' | 'responseId' | 'coarsened'
>;

const TARGET_FREQUENCY_HZ = 1_000;
const DRAG_GRID_SIZE = 48;
const DEFAULT_CACHE_CAPACITY = 24;
let requestCounter = 0;

function initialFieldPlaneWindows(): FieldPlaneWindows {
  return {
    spl: null,
    normalized: defaultFieldPlaneWindow('normalized'),
    phase: defaultFieldPlaneWindow('phase'),
    instantaneous: null,
  };
}

export function updateFieldPlaneWindows(
  windows: FieldPlaneWindows,
  field: DecodedFieldPlane,
  locked: boolean,
): FieldPlaneWindows {
  return {
    spl: !locked || windows.spl === null
      ? fieldPlaneWindowForMode('spl', field.real, field.imag)
      : windows.spl,
    normalized: defaultFieldPlaneWindow('normalized'),
    phase: defaultFieldPlaneWindow('phase'),
    instantaneous: !locked || windows.instantaneous === null
      ? fieldPlaneWindowForMode('instantaneous', field.real, field.imag)
      : windows.instantaneous,
  };
}

function nextRequestId(): string {
  requestCounter += 1;
  return `field-plane-${Date.now().toString(36)}-${requestCounter.toString(36)}`;
}

function clonePlane(plane: FieldPlaneSpec): FieldPlaneSpec {
  return {
    ...plane,
    origin_m: [...plane.origin_m],
    axis_u: [...plane.axis_u],
    axis_v: [...plane.axis_v],
  };
}

function samePlaneTransform(left: FieldPlaneSpec, right: FieldPlaneSpec): boolean {
  return left.width_m === right.width_m
    && left.height_m === right.height_m
    && left.origin_m.every((value, index) => value === right.origin_m[index])
    && left.axis_u.every((value, index) => value === right.axis_u[index])
    && left.axis_v.every((value, index) => value === right.axis_v[index]);
}

function quantized(value: number): string {
  const rounded = Math.round(value * 1e6) / 1e6;
  return (Object.is(rounded, -0) ? 0 : rounded).toFixed(6);
}

export function fieldPlaneCacheKey({
  jobId,
  geometrySha256,
  synthesisRevision,
  responseId,
  frequencyIndex,
  plane,
}: FieldPlaneCacheKeyParts): string {
  const transform = [...plane.origin_m, ...plane.axis_u, ...plane.axis_v, plane.width_m, plane.height_m]
    .map(quantized)
    .join(',');
  return `${jobId}|${geometrySha256}|${synthesisRevision ?? 'legacy'}|${responseId}|${frequencyIndex}|${transform}|${plane.nx}x${plane.ny}`;
}

export class FieldPlaneLruCache<Value> {
  private readonly entries = new Map<string, Value>();

  constructor(private readonly capacity = DEFAULT_CACHE_CAPACITY) {
    if (!Number.isSafeInteger(capacity) || capacity < 1) throw new Error('Field-plane cache capacity must be positive');
  }

  get size(): number { return this.entries.size; }

  get(key: string): Value | undefined {
    const value = this.entries.get(key);
    if (value === undefined) return undefined;
    this.entries.delete(key);
    this.entries.set(key, value);
    return value;
  }

  set(key: string, value: Value): void {
    this.entries.delete(key);
    this.entries.set(key, value);
    while (this.entries.size > this.capacity) {
      const oldest = this.entries.keys().next().value as string | undefined;
      if (oldest === undefined) break;
      this.entries.delete(oldest);
    }
  }

  clear(): void { this.entries.clear(); }
}

/** Pure one-running/one-latest coordinator. Enqueue replaces only the pending
 * item; completing an obsolete item cannot disturb a newer active request. */
export class LatestFieldPlaneRequestQueue<Value> {
  private active: Value | null = null;
  private queued: Value | null = null;

  get hasWork(): boolean { return this.active !== null || this.queued !== null; }

  isActive(value: Value): boolean { return this.active === value; }

  enqueue(value: Value): Value | null {
    if (this.active === null) {
      this.active = value;
      return value;
    }
    this.queued = value;
    return null;
  }

  complete(value: Value): Value | null {
    if (this.active !== value) return null;
    const next = this.queued;
    this.active = next;
    this.queued = null;
    return next;
  }

  dropQueued(): void { this.queued = null; }

  clear(): void {
    this.active = null;
    this.queued = null;
  }
}

export function shouldApplyFieldPlaneGeneration(generation: number, lastAppliedGeneration: number): boolean {
  return generation >= lastAppliedGeneration;
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

/**
 * The per-driver views a run offers, derived from its stored synthesis.
 *
 * Only crossover combine members qualify: the server weights a
 * `member:<id>` request by the stored combine (filter/gain/delay/polarity),
 * so a run without a combined channel — a single-channel solve above all —
 * has no member views and the selector stays hidden.
 */
export function fieldPlaneMemberResponses(results: ResultData): FieldPlaneMemberResponse[] {
  const payload = results as ResultPayload;
  const combinedId = combinedChannelId(payload);
  if (combinedId === null) return [];
  const combine = combineMetadataOf(payload.channels?.[combinedId] as ResultPayload | undefined);
  const members = combine?.members ?? [];
  if (members.length < 2) return [];
  return members.map((id) => ({ id, label: channelLabel(payload, id) }));
}

function validResponseId(
  responseId: FieldPlaneResponseId,
  memberResponses: readonly FieldPlaneMemberResponse[],
): FieldPlaneResponseId {
  if (responseId.startsWith('member:')) {
    const memberId = responseId.slice('member:'.length);
    return memberResponses.some(({ id }) => id === memberId) ? responseId : 'system';
  }
  return responseId;
}

/** Default H-plane in solver metres. A FrameScene declares the only unit
 * conversion used here; the request itself never contains viewport units. */
export function defaultFieldPlane(scene: FrameScene): FieldPlaneSpec {
  const seed: FieldPlaneSpec = {
    origin_m: [0, 0, 0],
    axis_u: [1, 0, 0],
    axis_v: [0, 0, 1],
    width_m: 0.01,
    height_m: 0.01,
    nx: 192,
    ny: 192,
  };
  return fieldPlanePreset(seed, 'h', {
    min: scene.bounds.min.toArray(),
    max: scene.bounds.max.toArray(),
    unitsPerMetre: scene.unitsPerMetre,
  });
}

export function fieldPlaneErrorMessage(reason: unknown): string {
  if (reason instanceof FieldPlaneHttpError) {
    switch (reason.status) {
      case 404: return 'field-plane result was not found';
      case 409: return 'field-plane result changed; select the completed run again';
      case 410: return 're-solve to enable field planes';
      case 422: return reason.remedy
        ? `${reason.message} ${reason.remedy}`
        : 'field planes are unavailable for this solve';
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
  const preferenceStore = dependencies.preferences ?? viewerPreferences;
  const renderingDefaults = preferenceStore.getSnapshot();
  const remembered = new Map<string, RememberedPlane>();
  const cache = new FieldPlaneLruCache<DecodedFieldPlane>(dependencies.cacheCapacity);
  const requestQueue = new LatestFieldPlaneRequestQueue<PendingFieldPlaneRequest>();
  let activationGeneration = 0;
  let requestGeneration = 0;
  let lastAppliedGeneration = 0;
  let requestController: AbortController | null = null;
  let cacheJobId: string | null = null;
  let cacheGeometrySha256: string | null = null;
  let cacheSynthesisRevision: string | null = null;
  let displayedRequest: DisplayedFieldPlaneRequest | null = null;

  return create<FieldPlaneStore>((set, get) => {
    const cancelRequests = (): void => {
      requestGeneration += 1;
      requestQueue.clear();
      requestController?.abort();
      requestController = null;
      const current = get();
      set({
        generation: requestGeneration,
        status: current.field ? 'ready' : current.enabled ? 'idle' : current.status,
        error: null,
        lastErrorCode: null,
        frozenNormalizationDb: null,
      });
    };

    const startRequest = (pending: PendingFieldPlaneRequest): void => {
      const controller = new AbortController();
      requestController = controller;
      set({ status: 'loading', error: null, lastErrorCode: null });
      void fetchPlane(pending.jobId, pending.request, fetch, controller.signal)
        .then((field) => {
          if (controller.signal.aborted) return;
          const current = get();
          if (
            !shouldApplyFieldPlaneGeneration(pending.generation, lastAppliedGeneration)
            || !current.enabled
            || current.jobId !== pending.jobId
            || pending.generation < current.generation
          ) return;
          if (
            field.header.request_id !== pending.request.request_id
            || field.header.job_id !== pending.jobId
            || field.header.frequency_index !== pending.frequencyIndex
            || field.header.response_id !== pending.responseId
            || field.header.nx !== pending.plane.nx
            || field.header.ny !== pending.plane.ny
          ) throw new Error('response metadata does not match the field-plane request');

          if (cacheGeometrySha256 !== null && cacheGeometrySha256 !== field.header.geometry_sha256) cache.clear();
          cacheGeometrySha256 = field.header.geometry_sha256;
          cacheSynthesisRevision = field.header.synthesis_revision;
          cacheJobId = pending.jobId;
          cache.set(fieldPlaneCacheKey({
            jobId: pending.jobId,
            geometrySha256: field.header.geometry_sha256,
            synthesisRevision: field.header.synthesis_revision,
            responseId: pending.responseId,
            frequencyIndex: pending.frequencyIndex,
            plane: pending.plane,
          }), field);
          lastAppliedGeneration = pending.generation;
          displayedRequest = {
            jobId: pending.jobId,
            plane: clonePlane(pending.plane),
            frequencyIndex: pending.frequencyIndex,
            responseId: pending.responseId,
            coarsened: pending.coarsened,
          };
          set({
            geometrySha256: field.header.geometry_sha256,
            synthesisRevision: field.header.synthesis_revision,
            lastAppliedGeneration,
            status: 'ready',
            error: null,
            lastErrorCode: null,
            field,
            windows: updateFieldPlaneWindows(current.windows, field, current.rangeLocked),
          });
        })
        .catch((reason: unknown) => {
          if (controller.signal.aborted) return;
          const current = get();
          if (reason instanceof FieldPlaneHttpError && reason.status === 429) {
            set({ lastSupersededBy: reason.replacementRequestId });
            if (!current.enabled || current.jobId !== pending.jobId || pending.generation < current.generation) return;
            set({ status: current.field ? 'ready' : 'idle', error: null, lastErrorCode: null });
            return;
          }
          if (!current.enabled || current.jobId !== pending.jobId || pending.generation < current.generation) return;
          set({
            status: 'error',
            error: fieldPlaneErrorMessage(reason),
            lastErrorCode: reason instanceof FieldPlaneHttpError ? reason.code : null,
          });
        })
        .finally(() => {
          if (requestController === controller) requestController = null;
          const wasActive = requestQueue.isActive(pending);
          const next = requestQueue.complete(pending);
          if (next) {
            startRequest(next);
            return;
          }
          const current = get();
          if (wasActive && !requestQueue.hasWork && current.status === 'loading') {
            set({ status: current.field ? 'ready' : 'idle' });
          }
        });
    };

    const load = (
      jobId: string,
      plane: FieldPlaneSpec,
      frequencyIndex: number,
      responseId: FieldPlaneResponseId = 'system',
      coarsened = false,
    ): void => {
      requestGeneration += 1;
      const generation = requestGeneration;
      set({ generation, status: 'loading', error: null, lastErrorCode: null });

      if (cacheJobId === jobId && cacheGeometrySha256 !== null) {
        const cached = cache.get(fieldPlaneCacheKey({
          jobId,
          geometrySha256: cacheGeometrySha256,
          synthesisRevision: cacheSynthesisRevision,
          responseId,
          frequencyIndex,
          plane,
        }));
        if (cached) {
          requestQueue.dropQueued();
          lastAppliedGeneration = generation;
          displayedRequest = {
            jobId,
            plane: clonePlane(plane),
            frequencyIndex,
            responseId,
            coarsened,
          };
          set({
            geometrySha256: cacheGeometrySha256,
            synthesisRevision: cacheSynthesisRevision,
            lastAppliedGeneration,
            status: 'ready',
            error: null,
            lastErrorCode: null,
            field: cached,
            windows: updateFieldPlaneWindows(get().windows, cached, get().rangeLocked),
          });
          return;
        }
      }

      let request: FieldPlaneRequest;
      try {
        request = buildFieldPlaneRequest({
          requestId: nextRequestId(),
          plane,
          frequencyIndex,
          responseId,
        });
      } catch (reason) {
        set({
          status: 'error',
          error: fieldPlaneErrorMessage(reason),
          lastErrorCode: reason instanceof FieldPlaneHttpError ? reason.code : null,
        });
        return;
      }
      const pending = {
        generation,
        jobId,
        plane: clonePlane(plane),
        frequencyIndex,
        responseId,
        coarsened,
        request,
      };
      const ready = requestQueue.enqueue(pending);
      if (ready) startRequest(ready);
    };

    const requestCurrentPlane = (current: FieldPlaneStore, plane: FieldPlaneSpec): void => {
      if (!current.jobId) return;
      const coarsened = current.dragging;
      const requestPlane = coarsened
        ? { ...clonePlane(plane), nx: DRAG_GRID_SIZE, ny: DRAG_GRID_SIZE }
        : clonePlane(plane);
      load(current.jobId, requestPlane, current.frequencyIndex, current.responseId, coarsened);
    };

    const remember = (
      jobId: string,
      plane: FieldPlaneSpec,
      frequencyIndex: number,
      responseId: FieldPlaneResponseId,
    ): void => {
      remembered.set(jobId, { plane: clonePlane(plane), frequencyIndex, responseId });
    };

    const activate = (jobId: string, suppliedDefault: FieldPlaneSpec): void => {
      activationGeneration += 1;
      const generation = activationGeneration;
      cancelRequests();
      displayedRequest = null;
      if (cacheJobId !== jobId) {
        cache.clear();
        cacheJobId = jobId;
        cacheGeometrySha256 = null;
        cacheSynthesisRevision = null;
      }
      const saved = remembered.get(jobId);
      const plane = clonePlane(saved?.plane ?? suppliedDefault);
      set({
        enabled: true,
        dragging: false,
        jobId,
        geometrySha256: cacheGeometrySha256,
        synthesisRevision: cacheSynthesisRevision,
        plane,
        frequencyIndex: saved?.frequencyIndex ?? 0,
        frequenciesHz: [],
        responseId: 'system',
        memberResponses: [],
        status: 'loading',
        error: null,
        lastErrorCode: null,
        lastSupersededBy: null,
        field: null,
        frozenNormalizationDb: null,
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
          const memberResponses = fieldPlaneMemberResponses(results);
          const responseId = validResponseId(saved?.responseId ?? 'system', memberResponses);
          remember(jobId, plane, frequencyIndex, responseId);
          set({ frequenciesHz, frequencyIndex, memberResponses, responseId });
          load(jobId, plane, frequencyIndex, responseId);
        })
        .catch((reason: unknown) => {
          const current = get();
          if (generation !== activationGeneration || !current.enabled || current.jobId !== jobId) return;
          set({
            status: 'error',
            error: fieldPlaneErrorMessage(reason),
            lastErrorCode: reason instanceof FieldPlaneHttpError ? reason.code : null,
          });
        });
    };

    return {
      enabled: false,
      dragging: false,
      jobId: null,
      geometrySha256: null,
      synthesisRevision: null,
      plane: null,
      frequencyIndex: 0,
      frequenciesHz: [],
      responseId: 'system',
      memberResponses: [],
      generation: 0,
      lastAppliedGeneration: 0,
      status: 'idle',
      error: null,
      lastErrorCode: null,
      lastSupersededBy: null,
      field: null,
      frozenNormalizationDb: null,
      displayMode: renderingDefaults.fieldPlaneDisplayMode,
      rangeLocked: renderingDefaults.fieldPlaneRangeLocked,
      animationSpeed: renderingDefaults.fieldPlaneAnimationSpeed,
      animating: false,
      isolines: false,
      windows: initialFieldPlaneWindows(),
      enable: activate,
      selectJob: (jobId, suppliedDefault) => {
        const current = get();
        if (current.enabled && current.jobId === jobId) return;
        activate(jobId, suppliedDefault);
      },
      disable: () => {
        activationGeneration += 1;
        cancelRequests();
        displayedRequest = null;
        set({
          enabled: false,
          dragging: false,
          jobId: null,
          geometrySha256: null,
          synthesisRevision: null,
          plane: null,
          frequencyIndex: 0,
          frequenciesHz: [],
          responseId: 'system',
          memberResponses: [],
          status: 'idle',
          error: null,
          lastErrorCode: null,
          lastSupersededBy: null,
          field: null,
          frozenNormalizationDb: null,
          animating: false,
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
        remember(current.jobId, current.plane, frequencyIndex, current.responseId);
        set({ frequencyIndex });
        requestCurrentPlane({ ...current, frequencyIndex }, current.plane);
      },
      setResponseId: (responseId) => {
        const current = get();
        if (!current.enabled || !current.jobId || !current.plane) return;
        const next = validResponseId(responseId, current.memberResponses);
        if (next !== responseId || next === current.responseId) return;
        remember(current.jobId, current.plane, current.frequencyIndex, next);
        set({ responseId: next });
        requestCurrentPlane({ ...current, responseId: next }, current.plane);
      },
      setPlane: (plane) => {
        const current = get();
        if (!current.enabled || !current.jobId) return;
        const next = clonePlane(plane);
        remember(current.jobId, next, current.frequencyIndex, current.responseId);
        set({ plane: next, dragging: false, frozenNormalizationDb: null });
        requestCurrentPlane({ ...current, plane: next, dragging: false }, next);
      },
      beginPlaneDrag: () => {
        const current = get();
        if (!current.enabled || current.dragging || !current.jobId || !current.plane) return;
        set({
          dragging: true,
          frozenNormalizationDb: current.field
            ? maxFieldSplDb(current.field.real, current.field.imag)
            : null,
        });
      },
      updatePlaneDrag: (plane) => {
        const current = get();
        if (!current.enabled || !current.dragging || !current.jobId || !current.plane) return;
        const next = { ...clonePlane(plane), nx: current.plane.nx, ny: current.plane.ny };
        remember(current.jobId, next, current.frequencyIndex, current.responseId);
        set({ plane: next });
        requestCurrentPlane(current, next);
      },
      endPlaneDrag: (plane) => {
        const current = get();
        if (!current.enabled || !current.jobId || !current.plane) return;
        const next = clonePlane(plane ?? current.plane);
        remember(current.jobId, next, current.frequencyIndex, current.responseId);
        set({ plane: next, dragging: false, frozenNormalizationDb: null });
        if (
          current.field
          && displayedRequest
          && !requestQueue.hasWork
          && !displayedRequest.coarsened
          && displayedRequest.jobId === current.jobId
          && displayedRequest.frequencyIndex === current.frequencyIndex
          && displayedRequest.responseId === current.field.header.response_id
          && displayedRequest.responseId === current.responseId
          && displayedRequest.plane.nx === next.nx
          && displayedRequest.plane.ny === next.ny
          && current.field.header.nx === next.nx
          && current.field.header.ny === next.ny
          && samePlaneTransform(displayedRequest.plane, next)
        ) return;
        requestCurrentPlane({ ...current, plane: next, dragging: false }, next);
      },
      cancelPending: () => {
        activationGeneration += 1;
        cancelRequests();
        set({ dragging: false, frozenNormalizationDb: null });
      },
      resume: () => {
        const current = get();
        if (!current.enabled || !current.jobId || !current.plane) return;
        if (!current.frequenciesHz.length) {
          activate(current.jobId, current.plane);
          return;
        }
        requestCurrentPlane(current, current.plane);
      },
      reportUnavailable: (reason) => {
        activationGeneration += 1;
        cancelRequests();
        displayedRequest = null;
        set({
          enabled: false,
          dragging: false,
          jobId: null,
          geometrySha256: null,
          synthesisRevision: null,
          plane: null,
          frequenciesHz: [],
          responseId: 'system',
          memberResponses: [],
          status: 'error',
          error: reason,
          lastErrorCode: null,
          lastSupersededBy: null,
          field: null,
          frozenNormalizationDb: null,
          animating: false,
        });
      },
      retry: () => {
        const current = get();
        if (!current.enabled || !current.jobId || !current.plane || !current.frequenciesHz.length) return;
        requestCurrentPlane(current, current.plane);
      },
      retryIfBlocked: () => {
        const current = get();
        if (current.status !== 'error' || current.lastErrorCode !== 'solve_running_or_queued') return;
        if (!current.enabled || !current.jobId || !current.plane || !current.frequenciesHz.length) return;
        requestCurrentPlane(current, current.plane);
      },
      invalidateSynthesis: (revision) => {
        const current = get();
        cache.clear();
        if (revision !== undefined) {
          cacheSynthesisRevision = revision;
          set({ synthesisRevision: revision });
        }
        if (!current.enabled || !current.jobId || !current.plane || !current.frequenciesHz.length) return;
        requestCurrentPlane(current, current.plane);
      },
      setDisplayMode: (displayMode) => {
        const current = get();
        if (displayMode === current.displayMode) return;
        set({ displayMode, animating: displayMode === 'instantaneous' ? current.animating : false });
        preferenceStore.update({ fieldPlaneDisplayMode: displayMode });
      },
      setRangeLocked: (rangeLocked) => {
        const current = get();
        if (rangeLocked === current.rangeLocked) return;
        set({
          rangeLocked,
          windows: !rangeLocked && current.field
            ? updateFieldPlaneWindows(current.windows, current.field, false)
            : current.windows,
        });
        preferenceStore.update({ fieldPlaneRangeLocked: rangeLocked });
      },
      setAnimationSpeed: (animationSpeed) => {
        if (!Number.isFinite(animationSpeed) || animationSpeed < 0.1 || animationSpeed > 4) return;
        if (animationSpeed === get().animationSpeed) return;
        set({ animationSpeed });
        preferenceStore.update({ fieldPlaneAnimationSpeed: animationSpeed });
      },
      setAnimating: (animating) => {
        const current = get();
        if (!current.enabled || (animating === current.animating && (!animating || current.displayMode === 'instantaneous'))) return;
        if (animating) {
          set({ animating: true, displayMode: 'instantaneous' });
          if (current.displayMode !== 'instantaneous') {
            preferenceStore.update({ fieldPlaneDisplayMode: 'instantaneous' });
          }
          return;
        }
        set({ animating: false });
      },
      setIsolines: (isolines) => {
        if (isolines !== get().isolines) set({ isolines });
      },
      applyRenderingPreferences: (preferences) => {
        const current = get();
        const unlocked = current.rangeLocked && !preferences.fieldPlaneRangeLocked;
        set({
          displayMode: preferences.fieldPlaneDisplayMode,
          rangeLocked: preferences.fieldPlaneRangeLocked,
          animationSpeed: preferences.fieldPlaneAnimationSpeed,
          animating: preferences.fieldPlaneDisplayMode === 'instantaneous' ? current.animating : false,
          windows: unlocked && current.field
            ? updateFieldPlaneWindows(current.windows, current.field, false)
            : current.windows,
        });
      },
    };
  });
}

export const useFieldPlaneStore = createFieldPlaneStore();
