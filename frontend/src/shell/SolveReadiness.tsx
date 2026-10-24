import { useOptionalSolveControl } from './JobsCoordinator';
import type { SolveReadinessState } from '../jobs/solveReadiness';
import { resolveEngineLabel } from './solveProgress';
import { workspaceNavigation } from './workspaceNavigation';

/** Every Solve surface renders the server's answer through this one view. */
export function SolveReadiness({ readiness, held = false }: { readiness?: SolveReadinessState | null; held?: boolean }) {
  const control = useOptionalSolveControl();
  const blocker = readiness === undefined ? control?.readiness : readiness;
  const engineBlocked = !!blocker?.engine;
  const visible = !engineBlocked || held || control?.readinessRequested;
  const waiting = control?.waitingForEngine === true;
  const message = waiting ? `${resolveEngineLabel(blocker?.engine ?? control?.waitingEngine) || blocker?.engine || control?.waitingEngine} is getting ready. The solve starts when it is ready.` : visible ? blocker?.message ?? (control?.disabled ? control.title : null) : null;
  if (!message && !control?.actionError) return null;
  const order = (remedy: NonNullable<SolveReadinessState['remedies']>[number]) =>
    remedy.kind === 'use_engine' ? remedy.same_solver ? 1 : 2 : remedy.kind === 'settings' ? 3 : 0;
  const remedies = [...(visible ? blocker?.remedies ?? [] : [])].sort((a, b) => order(a) - order(b));
  const heldPrepare = held && remedies.some(remedy => remedy.kind === 'prepare' && !remedy.disabled_reason);
  return <div className={`solve-readiness solve-notice${waiting ? '' : ' solve-notice-blocked'}`} role="status" aria-live="polite">
    {message && <span>{message}</span>}
    {waiting && <button type="button" onClick={() => control?.stopWaiting()}>Stop</button>}
    {remedies.map((remedy, index) => {
      if ((heldPrepare && remedy.kind !== 'prepare') || (waiting && remedy.kind === 'prepare')) return null;
      switch (remedy.kind) {
        case 'prepare': {
          const label = resolveEngineLabel(remedy.engine) || remedy.engine;
          if (remedy.disabled_reason) return message?.includes(remedy.disabled_reason) ? null : <span key={index}>{remedy.disabled_reason}</span>;
          return <button type="button" key={index} disabled={!control || !!control.preparingEngine}
            onClick={() => { void control?.prepareAndSolve(remedy.engine); }}>
            {control?.preparingEngine === remedy.engine ? `Preparing ${label}…` : `Prepare ${label} and solve`}
          </button>;
        }
        case 'use_engine': {
          const label = resolveEngineLabel(remedy.engine) || remedy.engine;
          return <button type="button" key={index} disabled={!control} onClick={() => control?.useEngine(remedy.engine)}>
            Use {label}
          </button>;
        }
        case 'wait': return held && !waiting ? <button type="button" key={index}
          onClick={() => { if (blocker?.engine) void control?.prepareAndSolve(blocker.engine); }}>Wait and solve</button> : null;
        case 'settings': return waiting ? null : <button type="button" key={index} onClick={() => workspaceNavigation.navigate('simulation')}>Open settings</button>;
      }
    })}
    {!heldPrepare && remedies.some(remedy => remedy.kind === 'use_engine' && !remedy.same_solver)
      && <span className="solve-readiness-note">Another solver’s results will differ.</span>}
    {control?.actionError && <span>{control.actionError}</span>}
    {control?.preparationError && <span role="alert">{control.preparationError}</span>}
  </div>;
}
