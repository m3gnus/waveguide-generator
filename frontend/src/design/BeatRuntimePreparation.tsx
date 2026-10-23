import type { EngineCapability } from '../jobs/actions';
import { usePrepareEngine } from '../jobs/usePrepareEngine';

/** Runtime reasons stay readable and actionable beside the backend picker. */
export function BeatRuntimePreparation({ engines, selected, refreshCapabilities }: {
  engines: readonly EngineCapability[];
  selected: string;
  refreshCapabilities: () => void | Promise<void>;
}) {
  const { prepare, starting, running, error } = usePrepareEngine(engines, refreshCapabilities);
  const runtimes = engines.filter((engine) => engine.name.startsWith('beat-')
    && (engine.name === selected || engine.runtime_preparable
      || engine.runtime_state === 'preparing' || engine.runtime_state === 'failed'));

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
