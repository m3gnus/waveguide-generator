import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getUpdateChannel, getUpdateStatus, installApplicationUpdate, setUpdateChannel,
  type UpdateChannel, type UpdateInstallAccepted, type UpdateStatus } from '../api/updates';
import { Icon } from './icons';
import { focusableSelector, useModalDialogFocus } from './dialogFocus';

export const UPDATE_QUERY_KEY = ['application-update'] as const;
const active = (state: string | undefined) => state === 'downloading' || state === 'verifying';
const megabytes = (value: number) => `${(value / 1_000_000).toFixed(1)} MB`;
export interface UpdateSnapshot {
  data: UpdateStatus | undefined; error: Error | null; isPending: boolean;
  refresh: () => Promise<UpdateStatus>;
}
export function useUpdateStatus(): UpdateSnapshot {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: UPDATE_QUERY_KEY, queryFn: () => getUpdateStatus(), retry: false, staleTime: 60_000,
    refetchInterval: (value) => value.state.data?.checking || active(value.state.data?.installState) ? 400 : 60_000,
  });
  const refresh = useCallback(async () => {
    const result = await getUpdateStatus(true);
    client.setQueryData(UPDATE_QUERY_KEY, result);
    return result;
  }, [client]);
  return { data: query.data, error: query.error instanceof Error ? query.error : null, isPending: query.isPending, refresh };
}
export interface UpdatePresentationResult {
  state: 'available' | 'current' | 'development' | 'checking' | 'publishing' | 'reload' | 'failed';
  label: string; wide: string; compact: string; announcement: string; detail: string | null; stale: boolean;
}
export function updatePresentation(snapshot: Pick<UpdateSnapshot, 'data' | 'error' | 'isPending'>): UpdatePresentationResult {
  const data = snapshot.data;
  const version = __WG2_VERSION__;
  const stale = data?.freshness === 'stale';
  const reason = snapshot.error?.message ?? data?.lastError ?? null;
  let state: UpdatePresentationResult['state'] = 'current';
  let label = 'Up to date';
  if (data && data.runningVersion !== version) { state = 'reload'; label = 'Reload WG'; }
  else if (data?.availability === 'available') {
    state = 'available';
    const size = data.action?.size ?? data.release?.installer?.size;
    label = `v${data.release?.version} available${size === undefined ? '' : ` (${megabytes(size)})`}`;
  } else if (data?.availability === 'incomplete') { state = 'publishing'; label = 'Update preparing'; }
  else if (data?.availability === 'ahead') label = `Ahead of ${data.channel}`;
  else if (data?.availability === 'current') label = 'Up to date';
  else if (data?.checking || snapshot.isPending) { state = 'checking'; label = 'Checking…'; }
  else if (data?.checkout.kind === 'source') { state = 'development'; label = 'Development build'; }
  else { state = 'failed'; label = 'Check failed'; }
  const detail = state === 'failed' ? reason ?? 'WG has not completed an update check.' : stale ? reason : null;
  const wide = `${version} · ${label}`;
  return { state, label, wide, compact: state === 'available' ? 'Update' : version,
    announcement: detail ? `${wide}. ${detail}` : wide, detail, stale };
}
export function UpdateButton({ snapshot, open, onOpen, buttonRef }: {
  snapshot: Pick<UpdateSnapshot, 'data' | 'error' | 'isPending'>; open: boolean; onOpen: () => void;
  buttonRef?: RefObject<HTMLButtonElement | null>;
}) {
  const presentation = updatePresentation(snapshot);
  return <>
    <button ref={buttonRef} type="button" className={`update-indicator ${presentation.state}${presentation.stale ? ' stale' : ''}`}
      aria-haspopup="dialog" aria-expanded={open} aria-label={`${presentation.wide}. Open application update details.`}
      title={presentation.detail ?? undefined} onClick={onOpen}>
      <i className="update-dot" aria-hidden="true"/><span className="update-wide">{presentation.wide}</span>
      <span className="update-compact">{presentation.compact}</span>
    </button>
    <span className="sr-only" role="status" aria-atomic="true">{presentation.announcement}</span>
  </>;
}

export function UpdateDialog({ open, snapshot, onRefresh, onClose, activeJobs = 0 }: {
  open: boolean; snapshot: Pick<UpdateSnapshot, 'data' | 'error' | 'isPending'>;
  onRefresh: () => Promise<UpdateStatus>; onClose: () => void; activeJobs?: number;
}) {
  const client = useQueryClient();
  const data = snapshot.data;
  const presentation = updatePresentation(snapshot);
  const generation = useRef(0);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string>();
  const [choice, setChoice] = useState<UpdateChannel>();
  const [confirmRestart, setConfirmRestart] = useState(false);
  const [progress, setProgress] = useState<UpdateInstallAccepted>();
  const state = progress?.installState ?? data?.installState;
  const installing = active(state) || state === 'ready';
  const close = useCallback(() => {
    generation.current += 1; setBusy(false); setFeedback(undefined); setConfirmRestart(false); setProgress(undefined); onClose();
  }, [onClose]);
  const initialFocus = useCallback((node: HTMLDivElement) => node.querySelector<HTMLElement>('[data-autofocus]')
    ?? node.querySelector<HTMLElement>(focusableSelector), []);
  const dialog = useModalDialogFocus<HTMLDivElement>({ open, onClose: close, initialFocus });
  useEffect(() => {
    if (open) return;
    generation.current += 1; setBusy(false); setFeedback(undefined); setConfirmRestart(false); setProgress(undefined); setChoice(undefined);
  }, [open]);
  useEffect(() => {
    if (!open || choice !== undefined) return;
    if (data?.channel) { setChoice(data.channel); return; }
    const controller = new AbortController();
    void getUpdateChannel(controller.signal).then((next) => { if (!controller.signal.aborted) setChoice(next); }).catch((error: unknown) => {
      if (!controller.signal.aborted) setFeedback(error instanceof Error ? error.message : String(error));
    });
    return () => controller.abort();
  }, [open, data?.channel, choice]);
  useEffect(() => {
    if (!open || !active(state)) return;
    const controller = new AbortController();
    let timer: number;
    const poll = async () => {
      try {
        const result = await getUpdateStatus(false, controller.signal);
        if (controller.signal.aborted) return;
        client.setQueryData(UPDATE_QUERY_KEY, result);
        setProgress({ accepted: true, version: result.activeVersion ?? '', activeVersion: result.activeVersion ?? '',
          installState: result.installState, downloadedBytes: result.downloadedBytes, totalBytes: result.totalBytes, error: result.error });
        if (active(result.installState)) timer = window.setTimeout(() => void poll(), 400);
      } catch (error) {
        if (controller.signal.aborted) return;
        setFeedback(`Could not read update progress: ${error instanceof Error ? error.message : String(error)}`);
        timer = window.setTimeout(() => void poll(), 400);
      }
    };
    timer = window.setTimeout(() => void poll(), 400);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [client, open, state]);
  if (!open) return null;

  const perform = async (action: () => Promise<void>) => {
    const operation = ++generation.current;
    setBusy(true); setFeedback(undefined);
    try { await action(); }
    catch (error) { if (operation === generation.current) setFeedback(error instanceof Error ? error.message : String(error)); }
    finally { if (operation === generation.current) setBusy(false); }
  };
  const install = () => {
    if (activeJobs > 0 && !confirmRestart) { setConfirmRestart(true); return; }
    const operation = generation.current + 1;
    void perform(async () => {
      const result = await installApplicationUpdate();
      if (operation === generation.current) { setProgress(result); setConfirmRestart(false); }
    });
  };
  const choose = (next: UpdateChannel) => {
    const operation = generation.current + 1;
    void perform(async () => {
      const saved = await setUpdateChannel(next);
      if (operation !== generation.current) return;
      setChoice(saved); setConfirmRestart(false); setProgress(undefined);
      await client.invalidateQueries({ queryKey: UPDATE_QUERY_KEY });
    });
  };
  const total = progress?.totalBytes || data?.totalBytes || data?.action?.size || 0;
  const downloaded = progress?.downloadedBytes ?? data?.downloadedBytes ?? 0;
  const error = progress?.error ?? data?.error;
  const mismatch = presentation.state === 'reload';
  const outcome = data?.lastOutcome;
  const installable = !mismatch && data?.canInstall === true && data.action?.kind === 'full_installer';
  const title = mismatch ? 'Waveguide Generator was updated' : data?.availability === 'available'
    ? `Waveguide Generator ${data.release?.version} is available` : `Waveguide Generator ${__WG2_VERSION__}`;
  const actionLabel = state === 'downloading' ? 'Downloading…' : state === 'verifying' ? 'Verifying…'
    : state === 'ready' ? 'Restarting…' : confirmRestart ? 'Stop solves, install and restart' : 'Install and restart';

  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
    <div ref={dialog} className="update-dialog" role="dialog" aria-modal="true" aria-labelledby="update-dialog-title" aria-busy={busy || installing}>
      <header><div><h2 id="update-dialog-title">{title}</h2><p>{mismatch
        ? 'Reload this page before continuing.' : 'WG checks for updates automatically. Downloads start only when you choose to install.'}</p></div>
        <button className="dialog-close" aria-label="Close update details" onClick={close}><Icon name="close"/></button></header>
      <div className="update-dialog-body">
        <p className={`update-state ${presentation.state}`} role="status">{presentation.label}</p>
        <dl className="update-facts"><div><dt>Installed</dt><dd>{data?.runningVersion ?? __WG2_VERSION__}</dd></div>
          <div><dt>Latest</dt><dd>{data?.release?.version ?? '—'}</dd></div>
          {data?.action && <div><dt>Download</dt><dd>{megabytes(data.action.size)}</dd></div>}</dl>
        <section className="update-channel" aria-label="Update channel"><h3>Update channel</h3>
          <div className="settings-theme-options" role="group" aria-label="Update channel">
            {(['stable', 'beta'] as const).map((next) => <button key={next} aria-pressed={choice === next}
              className={choice === next ? 'on' : ''} disabled={choice === undefined || busy || installing || mismatch}
              onClick={() => choose(next)}>{next === 'stable' ? 'Stable' : 'Beta'}</button>)}</div>
          <p>{choice === 'beta' ? 'Includes pre-release builds. Returning to Stable waits for a newer stable version.' : 'Finished releases only.'}</p></section>
        {presentation.detail && <p role={presentation.stale ? 'status' : 'alert'} className="update-note error">{presentation.detail}</p>}
        {data?.checkout.reason && <p className="update-note">{data.checkout.reason}</p>}
        {outcome && <p role={outcome.result === 'installed' ? 'status' : 'alert'} className="update-note">
          {outcome.result === 'installed' ? `Updated to v${outcome.to}.` : outcome.result === 'rollback_incomplete'
            ? 'The update failed and recovery is incomplete. Review the installation log before changing the installation.'
            : outcome.previousKept === true ? 'Update failed. The previous version was kept.' : 'Update failed. Review the installation log.'}
          {outcome.backupPath && <> Recovery location: <code>{outcome.backupPath}</code>.</>}
          {outcome.result !== 'installed' && <> Log: <code>{outcome.log}</code>.</>}</p>}
        {confirmRestart && <p role="alert" className="update-note warn">{activeJobs} active solve{activeJobs === 1 ? '' : 's'} will be stopped when WG restarts.</p>}
        {installable && <p className="update-note">WG verifies the download, closes, installs the update, and restarts. Your saved data stays in place.</p>}
        {(active(state) || state === 'ready') && <div role="status" className="update-install">
          <p>{state === 'verifying' ? 'Verifying the download…' : state === 'ready' ? 'Restarting WG to install the update…'
            : `${megabytes(downloaded)} of ${megabytes(total)}`}</p>
          {state === 'downloading' && <progress aria-label="Update download" value={downloaded} max={total || 1}/>}</div>}
        {error && <p role="alert" className="update-note error">{error}</p>}
        {feedback && <p role="status" className="update-note">{feedback}</p>}
        {data?.release?.notes && <section className="update-release-notes"><h3>Release notes</h3><p style={{ whiteSpace: 'pre-wrap' }}>{data.release.notes}</p></section>}
        {data?.release && <a href={data.release.url} target="_blank" rel="noreferrer">View release</a>}
      </div>
      <footer>{mismatch ? <button data-autofocus onClick={() => window.location.reload()}>Reload WG</button>
        : installable ? <button data-autofocus disabled={busy || installing} onClick={install}>{busy ? 'Starting…' : actionLabel}</button>
          : <button data-autofocus disabled={busy || installing || data?.checking} onClick={() => void perform(async () => { await onRefresh(); })}>
            {busy || data?.checking ? 'Checking…' : 'Check again'}</button>}
        <button onClick={close}>Close</button></footer>
    </div>
  </div>;
}
