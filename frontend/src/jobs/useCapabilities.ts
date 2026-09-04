import { useEffect, useRef } from 'react';
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
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
  axisymmetricRunner: '',
});
const plannerSupportByClient = new WeakMap<QueryClient, string>();

export interface CapabilitiesSnapshot {
  engines: readonly EngineCapability[];
  engineSelection: Readonly<EngineSelection>;
  /** A human-readable reason, or null while loading or once loaded. */
  error: string | null;
  isLoading: boolean;
}

export function useCapabilities(): CapabilitiesSnapshot {
  const client = useQueryClient();
  const { data, error, isError, isPending } = useQuery({
    queryKey: CAPABILITIES_QUERY_KEY,
    queryFn: () => getCapabilities(),
    retry: 1,
    staleTime: CAPABILITIES_STALE_MS,
    // This explicit server lifecycle covers delayed hardware inventory too.
    // Terminal ready, failed and skipped answers all stop the timer.
    refetchInterval: (query) => query.state.data?.cpuPreparationInFlight ? 1000 : false,
  });
  const plannerSupport = data
    ? `${data.engineSelection?.resolvedDefault ?? ''}|${(data.engines ?? NO_ENGINES)
      .map((engine) => `${engine.name}:${engine.available ? 1 : 0}`)
      .join(',')}`
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
    engines: data?.engines ?? NO_ENGINES,
    engineSelection: data?.engineSelection ?? NO_ENGINE_SELECTION,
    error: isError ? (error instanceof Error ? error.message : String(error)) : null,
    isLoading: isPending,
  };
}

/** Full capability record for controls whose support is version-dependent. */
export function useActiveBackendCapability(): EngineCapability | null {
  const engine = useSolveOptionsStore((state) => state.engine);
  const { engines, engineSelection } = useCapabilities();
  return activeBackendCapability(engine, engines, engineSelection);
}

/** Candidates the server may select for the current explicit/AUTO request. */
export function usePlannedBackendCapabilities(): readonly EngineCapability[] {
  const engine = useSolveOptionsStore((state) => state.engine);
  const solverMode = useSolveOptionsStore((state) => state.solverMode);
  const { engines, engineSelection } = useCapabilities();
  return plannedBackendCapabilities(engine, engines, engineSelection, solverMode);
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
