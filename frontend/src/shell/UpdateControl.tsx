import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getUpdateStatus, installApplicationUpdate, type UpdateStatus } from '../api/updates';
import { Icon } from './icons';
import { trapDialogFocus } from './SettingsDialog';

export const UPDATE_QUERY_KEY = ['application-update'] as const;
const UPDATE_CLIENT_STALE_MS = 60_000;
const UPDATE_PROGRESS_POLL_MS = 400;

type BundleInstallProgress = Pick<UpdateStatus, 'installState' | 'downloadedBytes' | 'totalBytes' | 'error'>;

export interface UpdateSnapshot {
  data: UpdateStatus | undefined;
  error: Error | null;
  isPending: boolean;
  refresh: () => Promise<UpdateStatus>;
}

export function useUpdateStatus(): UpdateSnapshot {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: UPDATE_QUERY_KEY,
    queryFn: () => getUpdateStatus(),
    retry: false,
    staleTime: UPDATE_CLIENT_STALE_MS,
    refetchOnWindowFocus: true,
  });
  const refresh = useCallback(async () => {
    const status = await getUpdateStatus(true);
    client.setQueryData(UPDATE_QUERY_KEY, status);
    return status;
  }, [client]);
  return {
    data: query.data,
    error: query.error instanceof Error ? query.error : null,
    isPending: query.isPending,
    refresh,
  };
}

export type UpdatePresentation = 'available' | 'current' | 'development' | 'unknown' | 'checking' | 'publishing' | 'reload';

export function updatePresentation(snapshot: Pick<UpdateSnapshot, 'data' | 'error' | 'isPending'>): {
  state: UpdatePresentation;
  wide: string;
  compact: string;
  announcement: string;
} {
  const version = __WG2_VERSION__;
  if (snapshot.data && snapshot.data.runningVersion !== version) {
    return { state: 'reload', wide: `${version} · reload`, compact: 'Reload', announcement: 'Waveguide Generator was updated. Reload this page.' };
  }
  if (snapshot.data?.availability === 'available') {
    const latest = snapshot.data.release?.version ?? 'a newer version';
    return { state: 'available', wide: `${version} · update available`, compact: 'Update', announcement: `Waveguide Generator ${latest} is available.` };
  }
  if (snapshot.data?.availability === 'incomplete') {
    return { state: 'publishing', wide: `${version} · update preparing`, compact: 'Update', announcement: 'A Waveguide Generator update is being published.' };
  }
  if (snapshot.data?.checkout.kind === 'development' || snapshot.data?.checkout.kind === 'detached') {
    return { state: 'development', wide: `${version} · development build`, compact: 'Dev', announcement: 'This is a development build of Waveguide Generator.' };
  }
  if (snapshot.data?.freshness === 'stale') {
    return { state: 'unknown', wide: `${version} · status unknown`, compact: version, announcement: 'Waveguide Generator update status is stale.' };
  }
  if (snapshot.data?.availability === 'current' || snapshot.data?.availability === 'ahead') {
    return { state: 'current', wide: `${version} · up to date`, compact: version, announcement: 'Waveguide Generator is up to date.' };
  }
  if (snapshot.isPending) {
    return { state: 'checking', wide: `${version} · checking…`, compact: version, announcement: 'Checking for Waveguide Generator updates.' };
  }
  if (snapshot.error || snapshot.data?.lastError || snapshot.data?.availability === 'unknown') {
    return { state: 'unknown', wide: `${version} · status unknown`, compact: version, announcement: 'Waveguide Generator update status is unknown.' };
  }
  return { state: 'checking', wide: `${version} · local`, compact: version, announcement: '' };
}

export function UpdateButton({ snapshot, open, onOpen, buttonRef }: {
  snapshot: Pick<UpdateSnapshot, 'data' | 'error' | 'isPending'>;
  open: boolean;
  onOpen: () => void;
  buttonRef?: RefObject<HTMLButtonElement | null>;
}) {
  const presentation = updatePresentation(snapshot);
  return <>
    <button
      ref={buttonRef}
      type="button"
      className={`update-indicator ${presentation.state}`}
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-label={`${presentation.wide}. Open application update details.`}
      onClick={onOpen}
    >
      <i className="update-dot" aria-hidden="true" />
      <span className="update-wide">{presentation.wide}</span>
      <span className="update-compact">{presentation.compact}</span>
    </button>
    <span className="sr-only" role="status" aria-atomic="true">{presentation.announcement}</span>
  </>;
}

function checkedLabel(value: string | null | undefined): string {
  if (!value) return 'Not checked successfully yet';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : `Last checked ${parsed.toLocaleString()}`;
}

function megabytes(value: number): string {
  return (value / 1_000_000).toFixed(1);
}

export function UpdateDialog({ open, snapshot, onRefresh, onClose }: {
  open: boolean;
  snapshot: Pick<UpdateSnapshot, 'data' | 'error' | 'isPending'>;
  onRefresh: () => Promise<UpdateStatus>;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const dialog = useRef<HTMLDivElement>(null);
  const operationGeneration = useRef(0);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string>();
  const [bundleProgress, setBundleProgress] = useState<BundleInstallProgress>();
  const data = snapshot.data;
  const mismatch = Boolean(data && data.runningVersion !== __WG2_VERSION__);
  const bundleAction = data?.action?.kind === 'bundle_download' ? data.action : undefined;
  const installProgress = bundleProgress ?? (data ? {
    installState: data.installState,
    downloadedBytes: data.downloadedBytes,
    totalBytes: data.totalBytes,
    error: data.error,
  } : undefined);
  const installActive = installProgress?.installState === 'downloading' || installProgress?.installState === 'verifying';

  const close = useCallback(() => {
    operationGeneration.current += 1;
    setBusy(false);
    setFeedback(undefined);
    setBundleProgress(undefined);
    onClose();
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focus = requestAnimationFrame(() => dialog.current?.querySelector<HTMLElement>('button, [href], [tabindex="0"]')?.focus());
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
        return;
      }
      trapDialogFocus(dialog, event);
    };
    document.addEventListener('keydown', keydown);
    return () => {
      cancelAnimationFrame(focus);
      document.removeEventListener('keydown', keydown);
      previous?.focus();
    };
  }, [close, open]);

  useEffect(() => {
    if (open) return;
    operationGeneration.current += 1;
    setBusy(false);
    setFeedback(undefined);
    setBundleProgress(undefined);
  }, [open]);

  useEffect(() => {
    if (!open || !data || data.checkout.kind !== 'bundle') return;
    setBundleProgress({
      installState: data.installState,
      downloadedBytes: data.downloadedBytes,
      totalBytes: data.totalBytes,
      error: data.error,
    });
  }, [data, open]);

  useEffect(() => {
    if (!open || !installActive) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const schedule = () => {
      timer = window.setTimeout(() => void poll(), UPDATE_PROGRESS_POLL_MS);
    };
    const poll = async () => {
      try {
        const status = await getUpdateStatus(false, controller.signal);
        if (controller.signal.aborted) return;
        client.setQueryData(UPDATE_QUERY_KEY, status);
        setBundleProgress({
          installState: status.installState,
          downloadedBytes: status.downloadedBytes,
          totalBytes: status.totalBytes,
          error: status.error,
        });
        setFeedback((current) => current?.startsWith('Could not read update progress:') ? undefined : current);
        if (status.installState === 'downloading' || status.installState === 'verifying') schedule();
      } catch (reason) {
        if (controller.signal.aborted) return;
        setFeedback(`Could not read update progress: ${reason instanceof Error ? reason.message : String(reason)}`);
        schedule();
      }
    };
    schedule();
    return () => {
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [client, installActive, open]);

  if (!open) return null;

  const refresh = async () => {
    const operation = ++operationGeneration.current;
    setBusy(true);
    setFeedback(undefined);
    try {
      const result = await onRefresh();
      if (operation === operationGeneration.current) setFeedback(result.lastError ? `Could not refresh: ${result.lastError}` : 'Update status refreshed.');
    } catch (reason) {
      if (operation === operationGeneration.current) setFeedback(`Could not refresh: ${reason instanceof Error ? reason.message : String(reason)}`);
    } finally {
      if (operation === operationGeneration.current) setBusy(false);
    }
  };
  const copy = async () => {
    if (data?.action?.kind !== 'copy_command') return;
    const operation = ++operationGeneration.current;
    try {
      await navigator.clipboard.writeText(data.action.command);
      if (operation === operationGeneration.current) setFeedback('Update command copied. Close Waveguide Generator, then run it.');
    } catch {
      if (operation === operationGeneration.current) setFeedback('Clipboard access failed. Select and copy the command below.');
    }
  };
  const install = async () => {
    const operation = ++operationGeneration.current;
    setBusy(true);
    setFeedback(undefined);
    try {
      const result = await installApplicationUpdate();
      if (operation === operationGeneration.current) {
        if ('version' in result) {
          setBundleProgress({
            installState: result.installState,
            downloadedBytes: result.downloadedBytes,
            totalBytes: result.totalBytes,
            error: result.error,
          });
        } else {
          setFeedback(`Installing ${result.tag}. WG will close and restart.`);
        }
      }
    } catch (reason) {
      if (operation === operationGeneration.current) setFeedback(`Could not start the update: ${reason instanceof Error ? reason.message : String(reason)}`);
    } finally {
      if (operation === operationGeneration.current) setBusy(false);
    }
  };

  let title = `Waveguide Generator ${__WG2_VERSION__}`;
  let summary = checkedLabel(data?.checkedAt);
  if (mismatch) {
    title = 'Waveguide Generator was updated';
    summary = `This tab is ${__WG2_VERSION__}; the running application is ${data?.runningVersion}. Reload before continuing.`;
  } else if (data?.availability === 'available' && data.release) {
    title = `Waveguide Generator ${data.release.version} is available`;
    if (bundleAction) {
      summary = `You are running ${data.runningVersion}. Download size ${megabytes(bundleAction.downloadBytes)} MB.`;
    } else {
      summary = data.canInstall
        ? `You are running ${data.runningVersion}. Install the update now or copy the fallback command.`
        : `You are running ${data.runningVersion}. Close Waveguide Generator before running the updater.`;
    }
  } else if (data?.availability === 'incomplete') {
    title = 'An update is being published';
    summary = 'The release exists, but its verified interface files are not ready yet. WG will check again shortly.';
  } else if (snapshot.error || data?.lastError) {
    title = 'Update status unavailable';
    summary = snapshot.error?.message ?? data?.lastError ?? summary;
  } else if (data?.availability === 'current' || data?.availability === 'ahead') {
    summary = `Version ${data.runningVersion} is up to date. ${checkedLabel(data.checkedAt)}.`;
  }

  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
    <div ref={dialog} className="settings-dialog update-dialog" role="dialog" aria-modal="true" aria-labelledby="update-dialog-title" aria-busy={busy || installActive}>
      <header><div><h2 id="update-dialog-title">{title}</h2><p>{summary}</p></div><button className="dialog-close" aria-label="Close update details" onClick={close}><Icon name="close"/></button></header>
      <div className="update-dialog-body">
        {data?.checkout.reason && <p className={`update-checkout-note ${data.checkout.updateSupported ? '' : 'blocked'}`}><b>{data.checkout.kind === 'bundle' ? 'Standalone app' : data.checkout.kind === 'development' ? 'Development checkout' : 'Checkout status'}</b>{data.checkout.reason}</p>}
        {data?.freshness === 'stale' && <p className="update-stale-note">Showing the last successful result. {data.lastError}</p>}
        {data?.action?.kind === 'copy_command' && <section className="update-command" aria-labelledby="update-command-title">
          <div><h3 id="update-command-title">Install this update</h3><p>WG will close, run the verified installer, and restart. The {data.action.shell} command remains available as a fallback.</p></div>
          <pre tabIndex={0}>{data.action.command}</pre>
          {data.canInstall && <button className="primary" disabled={busy} onClick={() => void install()}>{busy ? 'Starting…' : 'Install update'}</button>}
          <button disabled={busy} onClick={() => void copy()}>Copy update command</button>
        </section>}
        {bundleAction && <section className="update-command bundle-update" aria-labelledby="update-command-title">
          <div><h3 id="update-command-title">Install this update</h3><p>WG stays open while it downloads and verifies the update, then closes and restarts to install it.</p></div>
          {installProgress?.installState === 'downloading' && <>
            <p className="bundle-update-progress" role="status">Downloading {megabytes(installProgress.downloadedBytes)} of {megabytes(installProgress.totalBytes || bundleAction.downloadBytes)} MB</p>
            <progress max={installProgress.totalBytes || bundleAction.downloadBytes} value={installProgress.downloadedBytes} />
          </>}
          {installProgress?.installState === 'verifying' && <p className="bundle-update-progress" role="status">Verifying downloaded update…</p>}
          {installProgress?.installState === 'ready' && <p className="bundle-update-progress ready" role="status">Update ready — WG will close and restart.</p>}
          {installProgress?.installState === 'failed' && <p className="update-blocked" role="alert">Update failed: {installProgress.error ?? 'Unknown error'}</p>}
          <button
            className="primary"
            disabled={data?.canInstall !== true || busy || installActive || installProgress?.installState === 'ready'}
            onClick={() => void install()}
          >
            {installProgress?.installState === 'downloading'
              ? 'Downloading…'
              : installProgress?.installState === 'verifying'
                ? 'Verifying…'
                : installProgress?.installState === 'ready'
                  ? 'Update ready'
                  : installProgress?.installState === 'failed'
                    ? 'Try again'
                    : 'Install update'}
          </button>
        </section>}
        {data?.availability === 'available' && !data.action && <p className="update-blocked">This release is available, but WG will not suggest an update command until the checkout issue above is resolved.</p>}
        {data?.release && <a className="update-release-link" href={data.release.url} target="_blank" rel="noreferrer">View release details</a>}
        <div className="update-dialog-actions">
          {mismatch
            ? <button className="primary" onClick={() => window.location.reload()}>Reload WG</button>
            : <button disabled={busy || installActive} onClick={() => void refresh()}>{busy ? 'Checking…' : 'Check again'}</button>}
          <button onClick={close}>Close</button>
        </div>
        {feedback && <p className="update-feedback" role="status" aria-atomic="true">{feedback}</p>}
      </div>
    </div>
  </div>;
}
