import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { executeRunMove, previewRunMove, recoverRunMoves, runMoveStatus, undoRunMove, type MoveTarget, type RunMove } from '../api/runMoves';
import { useModalDialogFocus } from './dialogFocus';

function size(bytes: number) {
  return bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
}
const message = (error: unknown) => error instanceof Error ? error.message : String(error);

/** Each entry point uses this one dialog and the same server-owned preview. */
export function MoveRunsButton({ target, children = 'Move earlier runs…', role }: {
  target: MoveTarget; children?: React.ReactNode; role?: 'menuitem';
}) {
  const [open, setOpen] = useState(false);
  return <><button type="button" role={role} onClick={() => setOpen(true)}>{children}</button>
    {open && <MoveRunsDialog target={target} onClose={() => setOpen(false)}/>}</>;
}

export function MoveRunsDialog({ target, onClose }: { target: MoveTarget; onClose: () => void }) {
  const [plan, setPlan] = useState<RunMove | null>(null);
  const [report, setReport] = useState<RunMove | null>(null);
  const [history, setHistory] = useState<RunMove[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [gather, setGather] = useState(true);
  const [rename, setRename] = useState(false);
  const generation = useRef(0);
  const acting = useRef(false);
  const close = useCallback(() => { if (!acting.current) onClose(); }, [onClose]);
  const dialog = useModalDialogFocus<HTMLDivElement>({ open: true, onClose: close });
  const refresh = async (options: { selected?: string[]; gather: boolean; rename: boolean }) => {
    const request = ++generation.current;
    setBusy(true); setError(null); setPlan(null);
    try {
      const next = await previewRunMove(target, options);
      if (generation.current !== request) return;
      setPlan(next); setSelected(next.selected);
    } catch (reason) {
      if (generation.current === request) setError(message(reason));
    } finally { if (generation.current === request) setBusy(false); }
  };
  useEffect(() => {
    void refresh({ gather: true, rename: false });
    let current = true;
    void runMoveStatus().then(value => { if (current) setHistory((value.reports ?? []).filter(item => item.key === target.key)); }, () => undefined);
    return () => { current = false; generation.current += 1; };
    // The mounted dialog belongs to this target for its entire lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target.key, target.name]);

  const act = async (operation: () => Promise<RunMove>) => {
    if (acting.current) return;
    acting.current = true; setBusy(true); setError(null);
    try { setReport(await operation()); }
    catch (reason) { setError(message(reason)); }
    finally { acting.current = false; setBusy(false); }
  };
  const latest = report ?? history.at(-1);
  const undoable = latest?.phase === 'done' && !latest.operations.some(op => op.method === 'copy_verify_remove');
  const operations = plan?.operations ?? [];
  return createPortal(<div className="modal-backdrop">
    <div ref={dialog} className="update-dialog export-dialog move-runs-dialog" role="dialog" aria-modal="true" aria-labelledby="move-runs-title" aria-busy={busy}>
      <header><h2 id="move-runs-title">Move earlier runs…</h2><button aria-label="Close move tool" disabled={acting.current} onClick={close}>×</button></header>
      <div className="update-dialog-body">
        <p>Bring earlier runs into {target.name}. Check the folders below, then confirm the move.</p>
        <p>On another drive, WG copies and verifies every file in the whole move before removing the originals. A failed verification keeps the originals and reports the problem.</p>
        {busy && <p role="status">{acting.current ? 'Moving files — keep WG open…' : 'Checking folders…'}</p>}
        {error && <p role="alert">{error}</p>}
        {!report && <>
          <label><input type="checkbox" checked={gather} disabled={busy} onChange={event => {
            setGather(event.target.checked); void refresh({ selected, gather: event.target.checked, rename });
          }}/>Gather Fusion files into cad/</label>
          <label><input type="checkbox" checked={rename} disabled={busy} onChange={event => {
            setRename(event.target.checked); void refresh({ selected, gather, rename: event.target.checked });
          }}/>Rename folder to match {target.name}</label>
          {plan && <>
            <p>Destination: <strong>{plan.target}</strong></p>
            <p>{plan.summary.runs} runs · {plan.summary.files} files · {size(plan.summary.bytes)}</p>
            {plan.folder !== plan.target && <p>Rename folder: {plan.folder} → {plan.target} · {plan.summary.rename_runs} runs · {plan.summary.rename_files} files · {size(plan.summary.rename_bytes)}</p>}
            {plan.candidates.length === 0 && <p>No earlier runs found in the Runs folders WG knows.</p>}
            <ul>{plan.candidates.map(candidate => <li key={candidate.job_id}>
              <label><input type="checkbox" checked={selected.includes(candidate.job_id)} disabled={busy} onChange={event => {
                const next = event.target.checked ? [...selected, candidate.job_id] : selected.filter(id => id !== candidate.job_id);
                setSelected(next); void refresh({ selected: next, gather, rename });
              }}/>{candidate.source}</label>
              <p>→ {candidate.destination}<br/>1 run · {candidate.files} files · {size(candidate.bytes)}<br/>{candidate.reason}<br/>{candidate.method === 'rename'
                ? 'Moved on this drive (instant)' : `Copied and verified, then originals removed from ${candidate.source}`}</p>
            </li>)}</ul>
            {gather && <p>{plan.summary.fusion_files} verified Fusion copies gathered · {plan.summary.duplicates} byte-identical duplicates removed · {size(plan.summary.saved_bytes)} saved. Versions used by runs remain in cad/.</p>}
            {operations.filter(op => op.kind === 'cad').map(op => <p key={op.src}>{op.src} → {op.dst}</p>)}
            <p>Old folders removed if left empty: {plan.old_folders?.join(', ') || 'None'}.</p>
          </>}
        </>}
        {latest && <section aria-label="Move report">
          <h3>{latest.phase === 'done' ? 'Move complete' : latest.phase === 'undone' ? 'Move undone' : 'Move needs attention'}</h3>
          {latest.error && <p role="alert">{latest.error}</p>}
          <ul>{latest.operations.map((op, index) => <li key={index}>{op.src} → {op.dst} · {latest.phase === 'undone' ? 'Restored' : op.state === 'removed' ? (op.kind === 'run' || op.kind === 'rename' ? 'Moved' : 'Gathered') : op.state === 'published' ? 'At destination; finishing move' : 'Not moved'}</li>)}</ul>
          {latest.phase !== 'done' && latest.phase !== 'undone' && <button disabled={busy} onClick={() => void act(async () => {
            const recovered = await recoverRunMoves();
            const item = recovered.reports.find(item => item.id === latest.id);
            if (!item) throw new Error('No pending move found. Reopen the tool to refresh its status.');
            return item;
          })}>Retry recovery</button>}
          {undoable && <button disabled={busy} onClick={() => void act(() => undoRunMove(latest.id))}>Undo this move</button>}
          {latest.operations.some(op => op.method === 'copy_verify_remove') && <p>{latest.phase === 'done' ? 'Moves from another drive cannot be undone here. Originals were removed after the whole move verified.' : 'No original is removed until every file in the whole move verifies. Close open files or reconnect the drive, then retry recovery.'}</p>}
        </section>}
      </div>
      <footer><button disabled={acting.current} onClick={close}>{report ? 'Close' : 'Cancel'}</button>
        {!report && <button className="primary" disabled={busy || !plan || operations.length === 0} onClick={() => {
          if (plan && !busy) void act(() => executeRunMove(plan.id));
        }}>{(plan?.summary.runs ?? 0) > 0 ? `Move ${plan!.summary.runs} ${plan!.summary.runs === 1 ? 'run' : 'runs'}` : 'Apply folder changes'}</button>}
      </footer>
    </div>
  </div>, document.body);
}

/** Startup recovery is visible even before somebody opens the tool. */
export function RunMoveRecoveryNotice() {
  const [recovered, setRecovered] = useState<RunMove[]>([]);
  const [review, setReview] = useState<RunMove | null>(null);
  useEffect(() => {
    let current = true;
    void runMoveStatus().then(value => { if (current) setRecovered(value.recovered ?? []); }, () => undefined);
    return () => { current = false; };
  }, []);
  if (!recovered.length) return null;
  return <><div role="status">{recovered.some(item => item.error)
    ? 'An earlier-runs move needs attention.' : `Recovered ${recovered.length} earlier-runs move(s).`}
    {recovered.map(item => <button key={item.id} onClick={() => setReview(item)}>Review move into {item.target}</button>)}
    <button aria-label="Dismiss move recovery notice" onClick={() => setRecovered([])}>×</button>
  </div>{review && <MoveRunsDialog target={{ key: review.key, name: review.name }} onClose={() => setReview(null)}/>}</>;
}
