import { useCallback, useEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getUpdateStatus, installApplicationUpdate, type UpdateStatus } from '../api/updates';
import { Icon } from './icons';
import { focusableSelector, useModalDialogFocus } from './dialogFocus';

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

export type UpdatePresentation =
  | 'available' | 'current' | 'development' | 'checking' | 'publishing' | 'reload' | 'failed';

export interface UpdatePresentationResult {
  state: UpdatePresentation;
  /** The verdict on its own, for the dialog's status row. */
  label: string;
  wide: string;
  compact: string;
  announcement: string;
  /** Why a check failed, or why the standing verdict is older than it looks. */
  detail: string | null;
  /** The verdict stands, but the most recent check did not succeed. */
  stale: boolean;
}

/**
 * What the top bar says about the update check, in one place both it and the
 * dialog read.
 *
 * The order below is the priority order, and its shape is deliberate: every
 * branch that can be reached with a verdict in hand reports that verdict, and
 * only the branches with nothing to report fall through to "check failed" or
 * "checking". There is no terminal "unknown" -- a permanent one is what a
 * packaged install showed for its entire life when the status payload was
 * refused client-side, and a label a user can never resolve is worse than the
 * failure it is hiding. A failed check says so, and carries its reason.
 */
export function updatePresentation(
  snapshot: Pick<UpdateSnapshot, 'data' | 'error' | 'isPending'>,
): UpdatePresentationResult {
  const version = __WG2_VERSION__;
  const data = snapshot.data;
  const reason = snapshot.error?.message ?? data?.lastError ?? null;
  // A stale verdict is still the truth of the last successful check; the failure
  // rides along as detail rather than replacing the answer.
  const stale = data?.freshness === 'stale';
  const carry = (result: Omit<UpdatePresentationResult, 'detail' | 'stale'>): UpdatePresentationResult => ({
    ...result,
    detail: stale ? reason : null,
    stale,
  });

  if (data && data.runningVersion !== version) {
    return carry({
      state: 'reload',
      label: 'Restart pending',
      wide: `${version} · reload`,
      compact: 'Reload',
      announcement: 'Waveguide Generator was updated. Reload this page.',
    });
  }
  if (data?.availability === 'available') {
    const latest = data.release?.version;
    return carry({
      state: 'available',
      label: 'Update available',
      wide: latest ? `${version} · update available (v${latest})` : `${version} · update available`,
      compact: 'Update',
      announcement: `Waveguide Generator ${latest ?? 'a newer version'} is available.`,
    });
  }
  if (data?.availability === 'incomplete') {
    return carry({
      state: 'publishing',
      label: 'Update preparing',
      wide: `${version} · update preparing`,
      compact: 'Update',
      announcement: 'A Waveguide Generator update is being published.',
    });
  }
  if (data?.checkout.kind === 'development' || data?.checkout.kind === 'detached') {
    return carry({
      state: 'development',
      label: 'Development build',
      wide: `${version} · development build`,
      compact: 'Dev',
      announcement: 'This is a development build of Waveguide Generator.',
    });
  }
  if (data?.availability === 'ahead') {
    // Running a beta after switching back to Stable. The visual state stays
    // 'current' on purpose -- being in front of your channel is not a problem
    // to flag -- but the label is not "up to date", because the running version
    // is not the one the channel offers.
    return carry({
      state: 'current',
      label: 'Ahead of stable',
      wide: `${version} · ahead of stable`,
      compact: version,
      announcement: `Waveguide Generator ${version} is newer than the latest stable release.`,
    });
  }
  if (data?.availability === 'current') {
    return carry({
      state: 'current',
      label: 'Up to date',
      wide: `${version} · up to date`,
      compact: version,
      announcement: 'Waveguide Generator is up to date.',
    });
  }
  // No verdict. Either the check failed, or it has not finished yet -- and those
  // are different things to say, so they are said differently.
  if (snapshot.isPending && !data) {
    return {
      state: 'checking',
      label: 'Checking…',
      wide: `${version} · checking…`,
      compact: version,
      announcement: 'Checking for Waveguide Generator updates.',
      detail: null,
      stale: false,
    };
  }
  const failure = reason ?? 'The last update check did not return a result.';
  return {
    state: 'failed',
    label: 'Check failed',
    wide: `${version} · check failed`,
    compact: version,
    announcement: `Waveguide Generator could not check for updates. ${failure}`,
    detail: failure,
    stale: false,
  };
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
      className={`update-indicator ${presentation.state}${presentation.stale ? ' stale' : ''}`}
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-label={`${presentation.wide}. Open application update details.`}
      // The reason on hover, so a failed check is never a dead end in the bar.
      title={presentation.detail ?? undefined}
      onClick={onOpen}
    >
      <i className="update-dot" aria-hidden="true" />
      <span className="update-wide">{presentation.wide}</span>
      <span className="update-compact">{presentation.compact}</span>
    </button>
    <span className="sr-only" role="status" aria-atomic="true">{presentation.announcement}</span>
  </>;
}

function checkedAt(value: string | null | undefined): { short: string; full: string } {
  if (!value) return { short: 'Never checked', full: 'WG has not completed an update check yet.' };
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return { short: value, full: value };
  return {
    short: `Checked ${parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`,
    full: `Last checked ${parsed.toLocaleString()}`,
  };
}

function megabytes(value: number): string {
  return (value / 1_000_000).toFixed(1);
}

function Fact({ label, value }: { label: string; value: ReactNode }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

export function UpdateDialog({ open, snapshot, onRefresh, onClose }: {
  open: boolean;
  snapshot: Pick<UpdateSnapshot, 'data' | 'error' | 'isPending'>;
  onRefresh: () => Promise<UpdateStatus>;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const operationGeneration = useRef(0);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string>();
  const [bundleProgress, setBundleProgress] = useState<BundleInstallProgress>();
  const data = snapshot.data;
  const presentation = updatePresentation(snapshot);
  const mismatch = presentation.state === 'reload';
  const bundleAction = data?.action?.kind === 'bundle_download' ? data.action : undefined;
  const commandAction = data?.action?.kind === 'copy_command' ? data.action : undefined;
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

  // The action a user came here for, not whichever control happens to be first
  // in the DOM.
  const initialFocus = useCallback((node: HTMLDivElement) => (
    node.querySelector<HTMLElement>('[data-autofocus]') ?? node.querySelector<HTMLElement>(focusableSelector)
  ), []);
  const dialog = useModalDialogFocus<HTMLDivElement>({ open, onClose: close, initialFocus });

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
    if (!commandAction) return;
    const operation = ++operationGeneration.current;
    try {
      await navigator.clipboard.writeText(commandAction.command);
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

  const latest = data?.release?.version;
  const checked = checkedAt(data?.checkedAt);
  const channelName = data?.channel === 'beta' ? 'Beta' : 'Stable';
  const installable = !mismatch && data?.availability === 'available' && data.canInstall === true;
  const totalBytes = installProgress?.totalBytes || bundleAction?.downloadBytes || 0;
  const downloadedBytes = installProgress?.downloadedBytes ?? 0;
  const percent = totalBytes > 0
    ? Math.max(0, Math.min(100, Math.round((downloadedBytes / totalBytes) * 100)))
    : 0;

  let title = `Waveguide Generator ${__WG2_VERSION__}`;
  let summary = 'WG checks GitHub for a newer release and can install it for you.';
  if (mismatch) {
    title = 'Waveguide Generator was updated';
    summary = `This tab is ${__WG2_VERSION__}; the running application is ${data?.runningVersion}. Reload before continuing.`;
  } else if (presentation.state === 'available' && latest) {
    title = `Waveguide Generator ${latest} is available`;
    summary = bundleAction
      ? `WG downloads ${megabytes(bundleAction.downloadBytes)} MB, verifies it, then restarts to finish.`
      : data?.canInstall
        ? 'WG can run the verified installer for you, or you can run the command yourself.'
        : 'Close Waveguide Generator before running the updater.';
  } else if (presentation.state === 'publishing') {
    title = 'An update is being published';
    summary = 'The release exists, but its verified interface files are not ready yet. WG will check again shortly.';
  } else if (presentation.state === 'failed') {
    // The reason belongs to the alert below, once. Repeating it here left the
    // headline saying nothing about what the failure means for the user.
    title = 'Update check failed';
    summary = `WG cannot tell whether a newer release exists. Version ${__WG2_VERSION__} keeps running normally.`;
  } else if (presentation.state === 'development') {
    summary = 'This is a development checkout, so WG will not install a release over it.';
  } else if (presentation.state === 'checking') {
    summary = 'Checking GitHub for a newer release…';
  } else if (data?.availability === 'ahead') {
    // Reached by switching back to Stable while running a beta, which is the
    // whole reason no new availability state was added for that: WG is not out
    // of date, it is in front of the channel it now follows.
    summary = `This build is newer than the latest ${data.channel === 'beta' ? 'release' : 'stable release'} on the ${channelName.toLowerCase()} channel.`;
  } else if (data?.availability === 'current') {
    summary = `You are running the latest ${data.channel === 'beta' ? 'release offered on the beta channel' : 'stable release'}.`;
  }

  const primary = mismatch
    ? { label: 'Reload WG', onClick: () => window.location.reload(), disabled: false }
    : installable
      ? {
        label: installProgress?.installState === 'downloading'
          ? 'Downloading…'
          : installProgress?.installState === 'verifying'
            ? 'Verifying…'
            : installProgress?.installState === 'ready'
              ? 'Update ready'
              : installProgress?.installState === 'failed'
                ? 'Try again'
                : busy ? 'Starting…' : 'Install update',
        onClick: () => void install(),
        disabled: busy || installActive || installProgress?.installState === 'ready',
      }
      : {
        label: busy ? 'Checking…' : 'Check again',
        onClick: () => void refresh(),
        disabled: busy || installActive,
      };

  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
    <div ref={dialog} className="update-dialog" role="dialog" aria-modal="true" aria-labelledby="update-dialog-title" aria-busy={busy || installActive}>
      <header>
        <div>
          <h2 id="update-dialog-title">{title}</h2>
          <p>{summary}</p>
        </div>
        <button className="dialog-close" aria-label="Close update details" onClick={close}><Icon name="close"/></button>
      </header>

      <div className="update-dialog-body">
        <p className={`update-state ${presentation.state}`}>
          <i className="update-dot" aria-hidden="true"/>
          <b>{presentation.label}</b>
          <em title={checked.full}>{checked.short}</em>
        </p>

        <dl className="update-facts">
          <Fact label="Installed" value={data?.runningVersion ?? __WG2_VERSION__}/>
          <Fact label="Latest" value={latest ?? (presentation.state === 'failed' ? 'Unknown' : '—')}/>
          <Fact label="Channel" value={channelName}/>
          {bundleAction && <Fact label="Download" value={`${megabytes(bundleAction.downloadBytes)} MB`}/>}
        </dl>

        {presentation.state === 'failed' && <p className="update-note error" role="alert">
          <b>WG could not complete the check</b>
          {presentation.detail}
        </p>}

        {presentation.stale && presentation.detail && <p className="update-note warn">
          <b>Showing the last successful result</b>
          {presentation.detail}
        </p>}

        {data?.checkout.reason && <p className={`update-note ${data.checkout.updateSupported ? '' : 'error'}`}>
          <b>{data.checkout.kind === 'bundle' ? 'Standalone app' : data.checkout.kind === 'development' ? 'Development checkout' : 'Checkout status'}</b>
          {data.checkout.reason}
        </p>}

        {data?.channel === 'beta' && <p className="update-note">
          <b>Beta channel</b>
          WG is offered pre-releases as well as finished ones. Change this in Settings.
        </p>}

        {commandAction && <section className="update-install" aria-labelledby="update-install-title">
          <h3 id="update-install-title">Install this update</h3>
          <p>WG will close, run the verified installer, and restart. The {commandAction.shell} command remains available as a fallback.</p>
          <pre tabIndex={0}>{commandAction.command}</pre>
          <div className="update-install-actions">
            <button disabled={busy} onClick={() => void copy()}><Icon name="copy"/>Copy update command</button>
          </div>
        </section>}

        {bundleAction && <section className="update-install" aria-labelledby="update-install-title">
          <h3 id="update-install-title">Install this update</h3>
          <p>WG stays open while it downloads and verifies the update, then closes and restarts to install it. Unsaved work in this window is not carried across the restart.</p>
          {installProgress?.installState === 'downloading' && <div className="update-progress">
            <div className="update-progress-line">
              <span>Downloading {megabytes(installProgress.downloadedBytes)} of {megabytes(totalBytes)} MB</span>
              <b>{percent}%</b>
            </div>
            <div
              className="progress"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={percent}
              aria-valuetext={`${percent}% downloaded`}
            ><i style={{ width: `${percent}%` }}/></div>
          </div>}
          {installProgress?.installState === 'verifying' && <div className="update-progress">
            <div className="update-progress-line"><span>Verifying downloaded update…</span></div>
            <div className="progress indeterminate" role="progressbar" aria-valuetext="Verifying"><i/></div>
          </div>}
          {installProgress?.installState === 'ready' && <p className="update-progress-note ready" role="status">Update ready — WG will close and restart.</p>}
          {installProgress?.installState === 'failed' && <p className="update-note error" role="alert">
            <b>Update failed</b>
            {installProgress.error ?? 'Unknown error'}
          </p>}
        </section>}

        {data?.availability === 'available' && !data.action && <p className="update-note error">
          <b>No update command</b>
          This release is available, but WG will not suggest an update command until the checkout issue above is resolved.
        </p>}

        {feedback && <p className="update-feedback" role="status" aria-atomic="true">{feedback}</p>}
      </div>

      <footer>
        {data?.release && <a className="update-release-link" href={data.release.url} target="_blank" rel="noreferrer">
          Release notes for {data.release.tag}
        </a>}
        <span className="spacer"/>
        {installable && <button disabled={busy || installActive} onClick={() => void refresh()}>Check again</button>}
        <button className="primary" data-autofocus disabled={primary.disabled} onClick={primary.onClick}>{primary.label}</button>
      </footer>
    </div>
  </div>;
}
