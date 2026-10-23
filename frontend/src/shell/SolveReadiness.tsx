import { useOptionalSolveControl } from './JobsCoordinator';
import type { SolveReadinessState } from '../jobs/solveReadiness';
import { resolveEngineLabel } from './solveProgress';
import { workspaceNavigation } from './workspaceNavigation';

/** Every Solve surface renders the server's answer through this one view. */
export function SolveReadiness({ readiness, held = false }: { readiness?: SolveReadinessState | null; held?: boolean }) {
  const control = useOptionalSolveControl();
  const blocker = readiness === undefined ? control?.readiness : readiness;
  const message = blocker?.message ?? (control?.disabled ? control.title : null);
  if (!message) return null;
  const order = (remedy: NonNullable<SolveReadinessState['remedies']>[number]) =>
    remedy.kind === 'use_engine' ? remedy.same_solver ? 1 : 2 : remedy.kind === 'settings' ? 3 : 0;
  const remedies = [...(blocker?.remedies ?? [])].sort((a, b) => order(a) - order(b));
  const heldPrepare = held && remedies.some(remedy => remedy.kind === 'prepare' && !remedy.disabled_reason);
  return <div className="solve-readiness solve-notice solve-notice-blocked" role="status" aria-live="polite">
    <span>{message}</span>
    {remedies.map((remedy, index) => {
      if (heldPrepare && remedy.kind !== 'prepare') return null;
      switch (remedy.kind) {
        case 'prepare': {
          const label = resolveEngineLabel(remedy.engine) || remedy.engine;
          if (remedy.disabled_reason) return message.includes(remedy.disabled_reason) ? null : <span key={index}>{remedy.disabled_reason}</span>;
          return <button type="button" key={index} disabled={!control || !!control.preparingEngine}
            onClick={() => { void (held ? control?.prepareAndSolve(remedy.engine) : control?.prepareEngine(remedy.engine)); }}>
            {control?.preparingEngine === remedy.engine ? `Preparing ${label}…` : `Prepare ${label}${held ? ' and solve' : ''}`}
          </button>;
        }
        case 'use_engine': {
          const label = remedy.label.replace(/\s*\(?different solver — results will differ\)?\s*$/, '').replace(/^Use /, '');
          return <button type="button" key={index} disabled={!control} onClick={() => control?.useEngine(remedy.engine)}>
            Use {label}{!remedy.same_solver && ' (different solver — results will differ)'}
          </button>;
        }
        case 'wait': return <button type="button" key={index} disabled>Wait</button>;
        case 'settings': return <button type="button" key={index} onClick={() => workspaceNavigation.navigate('simulation')}>Open settings</button>;
      }
    })}
    {control?.preparationError && <span role="alert">{control.preparationError}</span>}
  </div>;
}
