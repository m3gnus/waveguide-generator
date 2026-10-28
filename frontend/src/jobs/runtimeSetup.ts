import type { EngineCapability } from './actions';

type RuntimeRow = Pick<EngineCapability, 'name' | 'reason' | 'runtime_state' | 'runtime_preparable' | 'runtime_detail'>;

/**
 * A BEAT runtime's setup state in plain words (docs/WORDING.md), for the solver
 * picker and the solve status line alike. `null` for engines without a runtime
 * to set up, and for a runtime this installation cannot set up.
 *
 * BEAT CPU's first setup is a 280 MB Julia download and a ~4 minute compile.
 */
export function runtimeSetupText(engine: RuntimeRow, where: 'picker' | 'status' = 'picker'): string | null {
  if (!engine.name.startsWith('beat-')) return null;
  switch (engine.runtime_state) {
    case 'ready': return 'ready';
    case 'preparing': return `Setting up…${engine.runtime_detail ? ` (${engine.runtime_detail})` : ''}`;
    case 'failed': return `Setup failed: ${engine.runtime_detail || engine.reason || 'reason unknown'}`;
    case 'unprepared': {
      if (!engine.runtime_preparable) return null;
      const time = engine.name === 'beat-cpu' ? ' (about 5 min)' : '';
      return where === 'picker' ? `Not set up: select to set up${time}` : `Not set up${time}`;
    }
    default: return null;
  }
}
