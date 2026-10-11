import { useState } from 'react';
import { prepareBeatRuntime, type EngineCapability } from '../jobs/actions';

/** Runtime reasons stay readable and actionable beside the backend picker. */
export function BeatRuntimePreparation({ engines, selected, refreshCapabilities }: {
  engines: readonly EngineCapability[];
  selected: string;
  refreshCapabilities: () => void | Promise<void>;
}) {
  const [starting, setStarting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const running = starting !== null || engines.some((engine) => engine.runtime_state === 'preparing');
  const runtimes = engines.filter((engine) => engine.name.startsWith('beat-')
    && (engine.name === selected || !engine.available));

  async function prepare(engine: string) {
    setError(null);
    setStarting(engine);
    try {
      await prepareBeatRuntime(engine);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
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
              onClick={() => { void prepare(engine.name); }}>
              {state === 'failed' ? 'Retry' : 'Prepare'} {engine.label || engine.name}
            </button>}
      </div>;
    })}
    {error && <p className="field-error" role="alert">{error}</p>}
  </div>;
}
