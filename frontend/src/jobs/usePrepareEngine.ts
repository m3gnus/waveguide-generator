import { useState, useSyncExternalStore } from 'react';
import { prepareBeatRuntime, type EngineCapability } from './actions';

// Shared across dock roots: repeated presses join the same preparation POST.
const pending = new Map<string, Promise<void>>();
const listeners = new Set<() => void>();
const emit = () => listeners.forEach(listener => listener());
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
const snapshot = () => [...pending.keys()].join(',');

export function usePrepareEngine(engines: readonly EngineCapability[], refresh: () => void | Promise<void>) {
  useSyncExternalStore(subscribe, snapshot, snapshot);
  const [failure, setFailure] = useState<{ message: string; engine: EngineCapability | undefined } | null>(null);
  const current = engines.find(engine => engine.name === failure?.engine?.name);
  const error = failure && current?.runtime_state === failure.engine?.runtime_state
    && current?.reason === failure.engine?.reason && current?.available === failure.engine?.available
    && current?.runtime_preparable === failure.engine?.runtime_preparable ? failure.message : null;
  const prepare = async (name: string): Promise<boolean> => {
    setFailure(null);
    let request = pending.get(name);
    if (!request) {
      request = (async () => {
        try { await prepareBeatRuntime(name); }
        finally { await refresh(); }
      })();
      pending.set(name, request);
      emit();
      void request.finally(() => { pending.delete(name); emit(); }).catch(() => undefined);
    }
    try { await request; return true; }
    catch (reason) {
      setFailure({ message: reason instanceof Error ? reason.message : String(reason), engine: engines.find(engine => engine.name === name) });
      return false;
    }
  };
  return { prepare, error, starting: [...pending.keys()][0] ?? null,
    running: pending.size > 0 || engines.some(engine => engine.runtime_state === 'preparing') };
}
