import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { dismissCadJob, isCadChoiceJob, latestCadJobs, useCadJobs } from '../jobs/cadSolve';
import { useCadReturnStore } from '../stores/cadReturn';
import { cadWorkspace } from '../stores/cadWorkspace';
import { durableSettings } from '../stores/durableSettings';
import { meshCeilingFacts, meshCeilingQuestion } from '../stores/cadModelStatusLine';
import { meshCeilingChoice } from '../stores/meshCeilingChoice';
import { useModalDialogFocus } from './dialogFocus';
import { jobsCoordinatorBridge } from './JobsCoordinator';
import { workspaceNavigation } from './workspaceNavigation';

let openRequest: ((id: string) => void) | null = null;
export function openHeldRequest(id: string): void { openRequest?.(id); }

/** Which held request the pop-up shows now (null: none), so the model's
 * status line does not offer Choose… for the question already on screen. */
let shownRequest: string | null = null;
const shownListeners = new Set<() => void>();
export const heldRequestShown = {
  subscribe(listener: () => void): () => void {
    shownListeners.add(listener);
    return () => { shownListeners.delete(listener); };
  },
  getSnapshot(): string | null { return shownRequest; },
};
function publishShown(id: string | null): void {
  if (shownRequest === id) return;
  shownRequest = id;
  shownListeners.forEach(listener => listener());
}

/** Requests held before this window opened stay behind the model's status
 * line (its Choose…, which the status bar also offers from every tab) and,
 * for a model not on screen, the toolbar chip; only a request held during
 * this session opens the pop-up itself. */
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
 * and a preparation the triangle ceiling holds in this window. It shows only
 * the user-facing next step and the choices, never a raw reason.
 *
 * The ceiling's question states its facts (the triangle count against WG's
 * limit, as the server measured them) and offers Solve anyway, Change in
 * Fusion and Later (owner decision M6), however the preparation started --
 * automatically, by Prepare, or by a Solve: Solve anyway prepares accepting
 * the ceiling and starts the solve. It applies only to the model it was
 * asked for, while that model is selected. WG has no channel that opens
 * Fusion's Sources & Mesh, so Change in Fusion says where to make the change:
 * for a request from Fusion, its own panel's Sources & Mesh… button (the
 * request stays waiting, as after Later); for this window's own question,
 * the Waveguide Generator menu in Fusion. */
export function HeldCadRequestDialog() {
  const jobs = useCadJobs();
  const pending = useSyncExternalStore(meshCeilingChoice.subscribe, meshCeilingChoice.getSnapshot, meshCeilingChoice.getSnapshot);
  const selectedPath = useCadReturnStore(state => state.selectedBundle?.bundlePath ?? null);
  const ceiling = pending && !pending.deferred && pending.bundlePath === selectedPath ? pending : null;
  const [later, setLater] = useState<Set<string>>(() => new Set(readLater()));
  const [requested, setRequested] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  /** "Change in Fusion" was pressed for this question: the pop-up says where, then lets it go. */
  const [changing, setChanging] = useState<string | null>(null);
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
    // Later: the model waits for the choice, and CAD Link and Results say so.
    else meshCeilingChoice.defer();
    setRequested(null);
    setChanging(null);
  };
  const dialog = useModalDialogFocus<HTMLDivElement>({ open: !!id, onClose: close });
  const shownJob = job?.id ?? null;
  useEffect(() => {
    publishShown(shownJob);
    return () => publishShown(null);
  }, [shownJob]);
  if (!id) return null;
  const state = job?.cad_state;
  const name = job ? state?.snapshot?.document_name : ceiling?.name;
  const nextStep = job ? state?.next_step || GENERIC_NEXT_STEP
    : `${ceiling!.nextStep} You can also solve it anyway.`;
  // The triangle ceiling states its facts and offers Solve anyway, Change in
  // Fusion (which lets the request go) and Later.
  const ceilingQuestion = !job || state?.reason === 'mesh_too_large'
    ? meshCeilingQuestion(name || 'This model', meshCeilingFacts(job ?? ceiling), nextStep, !!job) : null;
  const serverOptions = job
    ? state?.options?.length ? state.options : [{ id: 'review', label: 'Review request' }, { id: 'dismiss', label: 'Dismiss request' }]
    : [{ id: 'solve_anyway', label: 'Solve anyway' }];
  const options = ceilingQuestion
    ? [{ id: 'change_in_fusion', label: 'Change in Fusion' }, ...serverOptions.filter(option => option.id !== 'dismiss')]
    : serverOptions;
  const changingNow = changing === id && ceilingQuestion !== null;
  const choose = async (option: string) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(id);
    setError(null);
    try {
      if (option === 'change_in_fusion') {
        // The change is made in Fusion. A request from Fusion stays waiting,
        // so Fusion's panel keeps its Sources & Mesh… button, until the next
        // Solve from Fusion replaces it; this window's own choice is let go.
        if (!job) meshCeilingChoice.clear();
      } else if (!job) {
        // The browser path: prepare again accepting the ceiling, and solve,
        // for the model the question was asked about.
        const asked = ceiling!;
        meshCeilingChoice.clear();
        if (useCadReturnStore.getState().selectedBundle?.bundlePath === asked.bundlePath) {
          await cadWorkspace.getSnapshot().prepareAndSolve({ meshLimitOverride: true });
        }
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
      setChanging(null);
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
        <h2 id="held-request-title">{ceilingQuestion ? ceilingQuestion.title : 'Waiting for you'}</h2>
        {name && !ceilingQuestion && <p>{name}</p>}
      </div></header>
      <div className="update-dialog-body">
        <p>{changingNow ? ceilingQuestion!.changeInFusion : ceilingQuestion ? ceilingQuestion.body : nextStep}</p>
        {error?.id === id && <p role="alert">{error.message}</p>}
      </div>
      {changingNow ? <footer>
        <button disabled={busy !== null} onClick={() => setChanging(null)}>Back</button>
        <button className="primary" disabled={busy !== null} onClick={() => void choose('change_in_fusion')}>OK</button>
      </footer> : <footer>
        <button disabled={busy !== null} onClick={close}>Later</button>
        {options.map(option => <button key={option.id} disabled={busy !== null}
          className={option.id === 'solve_anyway' ? 'primary' : undefined}
          onClick={() => option.id === 'change_in_fusion' ? setChanging(id) : void choose(option.id)}>{option.label}</button>)}
      </footer>}
    </div>
  </div>;
}
