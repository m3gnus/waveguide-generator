import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { jobsSocket } from '../api/jobsSocket';
import {
  getCapabilities,
  type EngineCapability,
  type EngineSelection,
} from './actions';
import {
  activeBackendCapability,
  migratedLegacyBeatEngine,
  plannedBackendCapabilities,
} from '../design/backendSupport';
import { capabilityFingerprint } from './capabilityFingerprint';
import { preferencesStore } from '../prefs/preferences';
import { useSolveOptionsStore } from '../stores/solveOptions';

/**
 * One shared capability query for the whole app.
 *
 * Three components need this list -- the status bar, the job coordinator and
 * the solver-options section -- and each used to fetch it independently, so a
 * cold load issued three identical requests and every Dockview panel remount
 * issued another. They now share `appQueryClient`'s single in-flight request
 * and its cache.
 *
 * Most results do not change within a server process: `EngineRegistry` probes
 * once and memoises them. The one live transition is BEAT CPU preparation;
 * its explicit lifecycle flag temporarily enables the short poll below.
 * BEMPP qualification and server-owned timeout retries use bounded backoff.
 * Everything else uses the long stale time so panel remounts stay cache-only.
 *
 * What a long staleTime cannot do is notice a *new* process. It only marks data
 * stale; it never refetches on a timer, so a focused tab whose server restarted
 * could sit on a dead engine list until something happened to remount. That is
 * what `useCapabilityRefreshOnReconnect` is for: the jobs socket coming back
 * after a drop is the one reliable signal that we are talking to a new server.
 */
export const CAPABILITIES_QUERY_KEY = ['capabilities'] as const;

/** Long enough that panel remounts never refetch within a working session. */
export const CAPABILITIES_STALE_MS = 5 * 60_000;

/** Stable identity so consumers do not re-render on an unchanged empty result. */
const NO_ENGINES: readonly EngineCapability[] = Object.freeze([]);
const NO_ENGINE_SELECTION: Readonly<EngineSelection> = Object.freeze({
  default: 'auto',
  resolvedDefault: null,
  full3dOrder: Object.freeze([]),
});
const plannerSupportByClient = new WeakMap<QueryClient, string>();

/** Twice the server budget allows scheduling/HTTP overhead. Older servers: 15 minutes. */
export const OPENCL_POLL_SAFETY_FACTOR = 2;
export const OPENCL_POLL_FALLBACK_MS = 15 * 60_000;

// All mounted consumers share the lifecycle clock, including manual refresh.
const qualificationClocks = new WeakMap<QueryClient, {
  started: number | null;
  generation: number;
  listeners: Set<() => void>;
}>();

export interface CapabilitiesSnapshot {
  hostPlatform: string | null;
  engines: readonly EngineCapability[];
  engineSelection: Readonly<EngineSelection>;
  /** A human-readable reason, or null while loading or once loaded. */
  error: string | null;
  isLoading: boolean;
  qualificationRefreshNeeded: boolean;
  refreshCapabilities: () => void;
}

const subscribeConnection = (listener: () => void) => jobsSocket.subscribe(listener);
const connectionSnapshot = () => jobsSocket.getSnapshot().connection;

export function useCapabilities(): CapabilitiesSnapshot {
  const connection = useSyncExternalStore(subscribeConnection, connectionSnapshot);
  const backendLost = connection === 'reconnecting' || connection === 'disconnected';
  const client = useQueryClient();
  let clock = qualificationClocks.get(client);
  if (!clock) {
    clock = { started: null, generation: 0, listeners: new Set() };
    qualificationClocks.set(client, clock);
  }
  const pollingClock = clock;
  const subscribe = useCallback((listener: () => void) => {
    pollingClock.listeners.add(listener);
    return () => { pollingClock.listeners.delete(listener); };
  }, [pollingClock]);
  const getGeneration = useCallback(() => pollingClock.generation, [pollingClock]);
  const generation = useSyncExternalStore(subscribe, getGeneration);
  const [ceilingReached, setCeilingReached] = useState(false);
  const pollCeiling = (seconds: number | undefined) => (
    typeof seconds === 'number' && Number.isFinite(seconds) && seconds > 0
      ? seconds * 1000 * OPENCL_POLL_SAFETY_FACTOR : OPENCL_POLL_FALLBACK_MS
  );
  const { data, error, isError, isPending } = useQuery({
    queryKey: CAPABILITIES_QUERY_KEY,
    queryFn: ({ signal }) => getCapabilities(fetch, signal),
    retry: 1,
    staleTime: CAPABILITIES_STALE_MS,
    // This explicit server lifecycle covers delayed hardware inventory too.
    // Terminal ready, failed and skipped answers all stop the timer.
    refetchInterval: (query) => {
      const pending = query.state.data?.engines?.some(qualificationPending);
      if (pending) {
        pollingClock.started ??= Date.now();
        const remaining = pollCeiling(query.state.data?.opencl_qualification_max_seconds)
          - (Date.now() - pollingClock.started);
        if (remaining > 0) {
          return Math.min(1000 * 2 ** Math.min(query.state.dataUpdateCount - 1, 4), 10_000, remaining);
        }
      } else {
        pollingClock.started = null;
      }
      return query.state.data?.cpuPreparationInFlight ? 1000 : false;
    },
  });
  const pending = data?.engines?.some(qualificationPending) ?? false;
  const ceiling = pollCeiling(data?.opencl_qualification_max_seconds);
  useEffect(() => {
    setCeilingReached(false);
    if (!pending) return;
    pollingClock.started ??= Date.now();
    const timer = setTimeout(() => setCeilingReached(true),
      Math.max(0, ceiling - (Date.now() - pollingClock.started)));
    return () => clearTimeout(timer);
  }, [pending, ceiling, generation, pollingClock]);
  const refreshCapabilities = useCallback(() => {
    pollingClock.started = Date.now();
    pollingClock.generation += 1;
    for (const listener of pollingClock.listeners) listener();
    void client.refetchQueries({ queryKey: CAPABILITIES_QUERY_KEY });
  }, [client, pollingClock]);
  const onshapeOffered = data === undefined ? null : data.onshape === true;
  useEffect(() => {
    if (onshapeOffered !== null) preferencesStore.setOnshapeAvailable(onshapeOffered);
  }, [onshapeOffered]);
  const plannerSupport = data
    ? `${data.engineSelection?.resolvedDefault ?? ''}|${capabilityFingerprint(data.engines ?? NO_ENGINES)}`
    : null;
  useEffect(() => {
    if (plannerSupport === null) return;
    const previous = plannerSupportByClient.get(client);
    if (previous !== undefined && previous !== plannerSupport) {
      void client.invalidateQueries({ queryKey: ['solve-plan'] });
    }
    plannerSupportByClient.set(client, plannerSupport);
  }, [client, plannerSupport]);
  return {
    hostPlatform: data?.hostPlatform ?? null,
    engines: data?.engines ?? NO_ENGINES,
    engineSelection: data?.engineSelection ?? NO_ENGINE_SELECTION,
    error: backendLost ? 'Backend connection interrupted. Reconnecting; refresh capabilities when it returns.'
      : isError ? (error instanceof Error ? error.message : String(error)) : null,
    isLoading: isPending,
    qualificationRefreshNeeded: pending && (ceilingReached || isError || backendLost),
    refreshCapabilities,
  };
}

function qualificationPending(engine: EngineCapability): boolean {
  return engine.name === 'bempp' && (
    engine.qualification === 'pending' || engine.opencl_retry_pending === true
  );
}

/** Full capability record for controls whose support is version-dependent. */
export function useActiveBackendCapability(): EngineCapability | null {
  const engine = useSolveOptionsStore((state) => state.engine);
  const accuracy = useSolveOptionsStore((state) => state.accuracy);
  const { engines, engineSelection } = useCapabilities();
  return activeBackendCapability(accuracyEngine(engine, accuracy, engines), engines, engineSelection);
}

/** Match the server's BEAT GPU readiness order for UI capability previews. */
export function accuracyEngine(engine: string, accuracy: 'fast' | 'accurate', engines: readonly EngineCapability[]): string {
  if (engine !== 'auto' || accuracy === 'fast') return engine;
  return ['beat-metal', 'beat-cuda', 'beat-rocm', 'beat-cpu']
    .find((name) => engines.some((item) => item.name === name && item.available)) ?? 'beat-cpu';
}

/** Candidates the server may select for the current explicit/AUTO request. */
export function usePlannedBackendCapabilities(): readonly EngineCapability[] {
  const engine = useSolveOptionsStore((state) => state.engine);
  const accuracy = useSolveOptionsStore((state) => state.accuracy);
  const { engines, engineSelection } = useCapabilities();
  return plannedBackendCapabilities(accuracyEngine(engine, accuracy, engines), engines, engineSelection);
}

/**
 * Rewrite a stored bare `beat` selection to the BEAT variant it means here.
 *
 * BEAT's execution backends became separately selectable engines, so the one
 * name that used to cover all of them no longer matches any option the server
 * advertises. Left alone, the picker would render with nothing selected while
 * the store still said `beat` -- and the status bar would report an engine that
 * is not in the capability list. Submission itself is safe either way: the
 * server accepts the legacy name and resolves it the same way this does.
 *
 * Runs once per capability answer rather than on a timer, and only ever
 * narrows `beat` to a `beat-*`, so it cannot fight a user who then picks
 * something else.
 */
export function useLegacyBeatEngineMigration(): void {
  const engine = useSolveOptionsStore((state) => state.engine);
  const setEngine = useSolveOptionsStore((state) => state.setEngine);
  const { engines, engineSelection } = useCapabilities();
  useEffect(() => {
    const migrated = migratedLegacyBeatEngine(engine, engines, engineSelection);
    if (migrated !== null) setEngine(migrated);
  }, [engine, engines, engineSelection, setEngine]);
}

/**
 * Re-probe capabilities when the jobs socket comes back after a drop.
 *
 * A socket only reconnects because the server went away, and the process that
 * answers now may not have the engines the last one reported. The epoch in the
 * hello frame cannot stand in for this: `_EPOCHS` in `server/jobs/events.py` is
 * a per-process counter that restarts at 1, so a restarted server hands out the
 * same epoch the old one did.
 *
 * The first connection is deliberately not a refresh -- the query is already
 * loading at that point, and invalidating would just duplicate it.
 */
export function useCapabilityRefreshOnReconnect(connection: string): void {
  const client = useQueryClient();
  const hasConnected = useRef(false);
  useEffect(() => {
    if (connection !== 'connected') return;
    if (hasConnected.current) {
      void client.invalidateQueries({ queryKey: CAPABILITIES_QUERY_KEY });
      // Request-specific plans depend on the same process-local registry.
      // Reusing one across a restart could re-enable a solve against an engine
      // the replacement process no longer has.
      void client.invalidateQueries({ queryKey: ['solve-plan'] });
    }
    hasConnected.current = true;
  }, [connection, client]);
}
