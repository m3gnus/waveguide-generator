import { useEffect, useState } from 'react';
import { prepareBeatRuntime, type EngineCapability } from '../jobs/actions';

/** Runtime reasons stay readable and actionable beside the backend picker. */
export function BeatRuntimePreparation({ engines, selected, refreshCapabilities }: {
  engines: readonly EngineCapability[];
  selected: string;
  refreshCapabilities: () => void | Promise<void>;
}) {
  const [starting, setStarting] = useState<string | null>(null);
  const [error, setError] = useState<{ message: string; engine: EngineCapability } | null>(null);
  const running = starting !== null || engines.some((engine) => engine.runtime_state === 'preparing');
  const runtimes = engines.filter((engine) => engine.name.startsWith('beat-')
    && (engine.name === selected || engine.runtime_preparable
      || engine.runtime_state === 'preparing' || engine.runtime_state === 'failed'));

  useEffect(() => {
    if (!error) return;
    const current = engines.find((engine) => engine.name === error.engine.name);
    if (!current || current.runtime_state !== error.engine.runtime_state
      || current.reason !== error.engine.reason || current.available !== error.engine.available
      || current.runtime_preparable !== error.engine.runtime_preparable) setError(null);
  }, [engines, error]);

  async function prepare(engine: EngineCapability) {
    setError(null);
    setStarting(engine.name);
    try {
      await prepareBeatRuntime(engine.name);
    } catch (failure) {
      setError({ message: failure instanceof Error ? failure.message : String(failure), engine });
      setStarting(null);
    } finally {
      // A 409 can mean preparation started in another window. Refresh also
      // enables the existing lifecycle poll in that case.
      await refreshCapabilities();
      setStarting(null);
    }
  }

  if (!runtimes.length && !error) return null;

  return <div className="beat-runtime-preparation">
    {runtimes.map((engine) => {
      const state = starting === engine.name ? 'preparing' : engine.runtime_state;
      return <div key={engine.name} className="section-note" role="status">
        <b>{engine.label || engine.name}</b>{state ? ` · ${state}` : ''}
        <p>{starting === engine.name ? 'Starting runtime preparation…' : engine.reason}</p>
        {state === 'preparing'
          ? <p>Preparation can take several minutes. This runtime becomes selectable when ready.</p>
          : engine.runtime_preparable && <button type="button" disabled={running}
              onClick={() => { void prepare(engine); }}>
              {state === 'failed' ? 'Retry' : 'Prepare'} {engine.label || engine.name}
            </button>}
      </div>;
    })}
    {error && <p className="field-error" role="alert">{error.message}</p>}
  </div>;
}
