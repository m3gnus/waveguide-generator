import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { dismissCadJob, isCadChoiceJob, latestCadJobs, useCadJobs } from '../jobs/cadSolve';
import { cadWorkspace } from '../stores/cadWorkspace';
import { durableSettings } from '../stores/durableSettings';
import { meshCeilingChoice } from '../stores/meshCeilingChoice';
import { useModalDialogFocus } from './dialogFocus';
import { jobsCoordinatorBridge } from './JobsCoordinator';
import { workspaceNavigation } from './workspaceNavigation';

let openRequest: ((id: string) => void) | null = null;
export function openHeldRequest(id: string): void { openRequest?.(id); }

/** Requests held before this window opened stay behind the toolbar chip and
 * Results; only a request held during this session opens the pop-up itself. */
let sessionStartedAt = Date.now();
export function resetHeldRequestSessionForTests(startedAt = Date.now()): void {
  sessionStartedAt = startedAt;
  writeLater([]);
}

/** "Later" holds for the request, across restarts, not only this window. */
const LATER_LIMIT = 200;
function readLater(): string[] {
  try {
    const value: unknown = JSON.parse(durableSettings.get('cadHeldLater') ?? '[]');
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : [];
  } catch { return []; }
}
function writeLater(ids: string[]): void {
  try { durableSettings.set('cadHeldLater', JSON.stringify(ids.slice(-LATER_LIMIT))); } catch { /* a convenience */ }
}

function heldThisSession(job: JobItem): boolean {
  const at = Date.parse(job.completed_at ?? job.created_at ?? '');
  return Number.isFinite(at) && at >= sessionStartedAt;
}

const GENERIC_NEXT_STEP = 'This request needs your choice before WG can solve it.';

/** One modal owns held-request choices: Fusion requests WG holds for the user,
 * and a Prepare simulation the triangle ceiling holds in this window. It shows
 * only the user-facing next step and the choices, never a raw reason. */
export function HeldCadRequestDialog() {
  const jobs = useCadJobs();
  const ceiling = useSyncExternalStore(meshCeilingChoice.subscribe, meshCeilingChoice.getSnapshot, meshCeilingChoice.getSnapshot);
  const [later, setLater] = useState<Set<string>>(() => new Set(readLater()));
  const [requested, setRequested] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const inFlight = useRef(false);
  const [error, setError] = useState<{ id: string; message: string } | null>(null);
  useEffect(() => {
    openRequest = setRequested;
    return () => { openRequest = null; };
  }, []);
  const held = latestCadJobs(jobs).filter(isCadChoiceJob);
  const job = held.find(item => item.id === requested)
    ?? (ceiling ? undefined : held.find(item => !later.has(item.id) && heldThisSession(item)));
  const id = job?.id ?? (ceiling ? `ceiling:${ceiling.bundlePath}` : null);
  const remember = (requestId: string) => {
    setLater(previous => {
      const next = new Set(previous).add(requestId);
      writeLater([...next]);
      return next;
    });
  };
  const close = () => {
    if (!id || inFlight.current) return;
    if (job) remember(job.id);
    else meshCeilingChoice.clear();
    setRequested(null);
  };
  const dialog = useModalDialogFocus<HTMLDivElement>({ open: !!id, onClose: close });
  if (!id) return null;
  const state = job?.cad_state;
  const name = job ? state?.snapshot?.document_name : ceiling?.name;
  const nextStep = job ? state?.next_step || GENERIC_NEXT_STEP : ceiling!.nextStep;
  const options = job
    ? state?.options?.length ? state.options : [{ id: 'review', label: 'Review request' }, { id: 'dismiss', label: 'Dismiss request' }]
    : [{ id: 'solve_anyway', label: 'Solve anyway' }];
  const choose = async (option: string) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(id);
    setError(null);
    try {
      if (!job) {
        // The browser Prepare path: prepare again, accepting the ceiling.
        meshCeilingChoice.clear();
        await cadWorkspace.getSnapshot().ingest({ meshLimitOverride: true });
      } else if (option === 'dismiss') await dismissCadJob(job);
      else if (option === 'solve_anyway' || option === 'retry') {
        const child = await jobsCoordinatorBridge.getSnapshot().continueRun(job.id, { mesh_limit_override: option === 'solve_anyway' });
        if (!child) throw new Error('Request could not continue');
        await jobsSocket.refresh();
      } else if (option === 'review') {
        workspaceNavigation.reveal('cad-requests');
      }
      if (job) remember(job.id);
      setRequested(null);
    } catch {
      setError({ id, message: 'WG could not apply that choice. Please try again.' });
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  };
  return <div className="modal-backdrop">
    <div ref={dialog} className="update-dialog" role="dialog" aria-modal="true" aria-labelledby="held-request-title" aria-busy={busy === id}>
      <header><div>
        <h2 id="held-request-title">Waiting for you</h2>
        {name && <p>{name}</p>}
      </div></header>
      <div className="update-dialog-body">
        <p>{nextStep}</p>
        {error?.id === id && <p role="alert">{error.message}</p>}
      </div>
      <footer>
        <button disabled={busy !== null} onClick={close}>Later</button>
        {options.map(option => <button key={option.id} disabled={busy !== null} onClick={() => void choose(option.id)}>{option.label}</button>)}
      </footer>
    </div>
  </div>;
}
