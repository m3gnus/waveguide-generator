import { useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getCapabilities,
  type EngineCapability,
  type EngineSelection,
} from './actions';
import { activeBackendCapability, activeBackendName, plannedBackendCapabilities } from '../design/backendSupport';
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
 * The result never changes within a server *process*: `EngineRegistry` probes
 * once and memoises for its lifetime (`server/engines/registry.py`). So the
 * staleTime below is long -- refetching inside one process can only return
 * identical bytes.
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

export interface CapabilitiesSnapshot {
  engines: readonly EngineCapability[];
  engineSelection: Readonly<EngineSelection>;
  /** A human-readable reason, or null while loading or once loaded. */
  error: string | null;
  isLoading: boolean;
}

export function useCapabilities(): CapabilitiesSnapshot {
  const { data, error, isError, isPending } = useQuery({
    queryKey: CAPABILITIES_QUERY_KEY,
    queryFn: () => getCapabilities(),
    retry: 1,
    staleTime: CAPABILITIES_STALE_MS,
  });
  return {
    engines: data?.engines ?? NO_ENGINES,
    engineSelection: data?.engineSelection ?? NO_ENGINE_SELECTION,
    error: isError ? (error instanceof Error ? error.message : String(error)) : null,
    isLoading: isPending,
  };
}

/**
 * The backend a solve would use right now, for controls that gate on it.
 *
 * Null while the probe is in flight and whenever no backend is available at
 * all; `backendSupports` treats that as "do not hide anything", so a slow or
 * failed probe degrades to the pre-gating behaviour rather than to an empty
 * panel. Rides the same shared query as everything else, so calling it from
 * every rendered field costs one cached read.
 */
export function useActiveBackend(): string | null {
  const engine = useSolveOptionsStore((state) => state.engine);
  const { engines, engineSelection } = useCapabilities();
  return activeBackendName(engine, engines, engineSelection);
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
  const { engines, engineSelection } = useCapabilities();
  return plannedBackendCapabilities(engine, engines, engineSelection);
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
    }
    hasConnected.current = true;
  }, [connection, client]);
}
