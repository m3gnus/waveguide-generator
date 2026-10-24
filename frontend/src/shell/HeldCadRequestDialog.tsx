import { useEffect, useRef, useState } from 'react';
import { jobsSocket } from '../api/jobsSocket';
import { dismissCadJob, isHeldCadJob, latestCadJobs, useCadJobs } from '../jobs/cadSolve';
import { useModalDialogFocus } from './dialogFocus';
import { jobsCoordinatorBridge } from './JobsCoordinator';
import { workspaceNavigation } from './workspaceNavigation';

let openRequest: ((id: string) => void) | null = null;
export function openHeldRequest(id: string): void { openRequest?.(id); }

/** One modal owns held-request choices, including requests arriving from Fusion. */
export function HeldCadRequestDialog() {
  const jobs = useCadJobs();
  const [seen, setSeen] = useState<Set<string>>(() => new Set());
  const [requested, setRequested] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const inFlight = useRef(false);
  const [error, setError] = useState<{ id: string; message: string } | null>(null);
  useEffect(() => {
    openRequest = setRequested;
    return () => { openRequest = null; };
  }, []);
  const held = latestCadJobs(jobs).filter(isHeldCadJob);
  const job = held.find(item => item.id === requested) ?? held.find(item => !seen.has(item.id));
  const close = () => {
    if (!job || inFlight.current) return;
    setSeen(previous => new Set(previous).add(job.id));
    setRequested(null);
  };
  const dialog = useModalDialogFocus<HTMLDivElement>({ open: !!job, onClose: close });
  if (!job) return null;
  const state = job.cad_state;
  const options = state?.options?.length ? state.options : [
    { id: 'review', label: 'Review request' }, { id: 'dismiss', label: 'Dismiss request' },
  ];
  const choose = async (id: string) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(job.id);
    setError(null);
    try {
      if (id === 'dismiss') await dismissCadJob(job);
      else if (id === 'solve_anyway' || id === 'retry') {
        const child = await jobsCoordinatorBridge.getSnapshot().continueRun(job.id, { mesh_limit_override: id === 'solve_anyway' });
        if (!child) throw new Error('Request could not continue');
        await jobsSocket.refresh();
      } else if (id === 'review') {
        if (state?.reason === 'engine_unavailable') workspaceNavigation.navigate('results');
        else workspaceNavigation.reveal('cad-requests');
      }
      setSeen(previous => new Set(previous).add(job.id));
      setRequested(null);
    } catch {
      setError({ id: job.id, message: 'WG could not apply that choice. Please try again.' });
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  };
  return <div className="modal-backdrop">
    <div ref={dialog} className="update-dialog" role="dialog" aria-modal="true" aria-labelledby="held-request-title" aria-busy={busy === job.id}>
      <header><div>
        <h2 id="held-request-title">Waiting for you</h2>
        {state?.snapshot?.document_name && <p>{state.snapshot.document_name}</p>}
      </div></header>
      <div className="update-dialog-body">
        <p>{state?.message || 'This request needs your choice before WG can solve it.'}</p>
        {state?.next_step && <p>{state.next_step}</p>}
        {error?.id === job.id && <p role="alert">{error.message}</p>}
      </div>
      <footer>
        <button disabled={busy !== null} onClick={close}>Later</button>
        {options.map(option => <button key={option.id} disabled={busy !== null} onClick={() => void choose(option.id)}>{option.label}</button>)}
      </footer>
    </div>
  </div>;
}
