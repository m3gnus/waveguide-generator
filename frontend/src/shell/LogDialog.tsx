import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from './icons';
import { trapDialogFocus } from './SettingsDialog';

export const LOG_PREVIEW_BYTES = 1_000_000;

async function readLogPreview(response: Response): Promise<{ contents: string; truncated: boolean }> {
  const reader = response.body?.getReader();
  if (!reader) throw new Error('Streaming log previews are unavailable in this browser');
  const decoder = new TextDecoder();
  const fragments: string[] = [];
  let bytes = 0;
  let truncated = false;
  const declaredBytes = Number(response.headers.get('Content-Length'));
  const declaredOversize = Number.isFinite(declaredBytes) && declaredBytes > LOG_PREVIEW_BYTES;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const remaining = LOG_PREVIEW_BYTES - bytes;
    if (value.byteLength > remaining) {
      fragments.push(decoder.decode(value.subarray(0, remaining), { stream: true }));
      truncated = true;
      await reader.cancel();
      break;
    }
    fragments.push(decoder.decode(value, { stream: true }));
    bytes += value.byteLength;
    if (bytes === LOG_PREVIEW_BYTES && declaredOversize) {
      truncated = true;
      await reader.cancel();
      break;
    }
  }
  fragments.push(decoder.decode());
  return { contents: fragments.join(''), truncated };
}

export function LogDialog({ jobId, onClose }: { jobId: string; onClose: () => void }) {
  const dialog = useRef<HTMLDivElement>(null);
  const requestGeneration = useRef(0);
  const requestController = useRef<AbortController | undefined>(undefined);
  const [contents, setContents] = useState('');
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [copyStatus, setCopyStatus] = useState<string>();
  const logUrl = `/api/jobs/${encodeURIComponent(jobId)}/log`;

  const refresh = useCallback(async () => {
    const request = ++requestGeneration.current;
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    setLoading(true);
    setError(undefined);
    setCopyStatus(undefined);
    try {
      const response = await fetch(logUrl, { signal: controller.signal });
      if (!response.ok) throw new Error(`Log request failed (${response.status})`);
      const preview = await readLogPreview(response);
      if (request === requestGeneration.current) {
        setContents(preview.contents);
        setTruncated(preview.truncated);
      }
    } catch (reason) {
      if (request === requestGeneration.current && !controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (request === requestGeneration.current) setLoading(false);
    }
  }, [logUrl]);

  useEffect(() => {
    void refresh();
    return () => {
      requestGeneration.current += 1;
      requestController.current?.abort();
    };
  }, [refresh]);

  const close = useCallback(() => {
    requestController.current?.abort();
    onClose();
  }, [onClose]);

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focus = requestAnimationFrame(() => {
      dialog.current?.querySelector<HTMLElement>('button:not([disabled])')?.focus();
    });
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
  }, [close]);

  const copy = async () => {
    setCopyStatus(undefined);
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard access is unavailable');
      await navigator.clipboard.writeText(contents);
      setCopyStatus('Copied');
    } catch (reason) {
      setCopyStatus(reason instanceof Error ? reason.message : String(reason));
    }
  };

  // Portalled out of the job card that opens it: `.job-card p` would otherwise
  // restyle the dialog's text (including the red error line) as a one-line
  // ellipsised job metric, and a fixed backdrop must not inherit any containing
  // block a panel ancestor establishes.
  const visibleContents = loading && !contents
    ? 'Loading log…'
    : !contents
      ? error ? 'Log preview unavailable.' : 'This log is empty.'
      : contents;

  return createPortal(<div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
    <div ref={dialog} className="settings-dialog log-dialog" role="dialog" aria-modal="true" aria-labelledby="job-log-title">
      <header><div><h2 id="job-log-title">Job log</h2><p>Preview of this run's output, up to 1.0 MB.</p></div><button className="dialog-close" aria-label="Close job log" onClick={close}><Icon name="close"/></button></header>
      <div className="settings-scroll log-dialog-body">
        <div className="log-dialog-actions">
          <button disabled={loading} onClick={() => void refresh()}>Refresh</button>
          <button disabled={loading || !contents} onClick={() => void copy()}>Copy preview</button>
          <a href={logUrl} download>Download complete log</a>
          {copyStatus && <span role="status">{copyStatus}</span>}
        </div>
        <div className="log-dialog-messages">
          {error && <p className="workspace-settings-error" role="alert">{error}</p>}
          {truncated && <p className="log-dialog-limit" role="status">Showing the first 1.0 MB only. Download the complete log to view the rest.</p>}
        </div>
        <pre className="job-log-content" role="region" aria-label="Job log preview" aria-busy={loading} tabIndex={0}>{visibleContents}</pre>
      </div>
    </div>
  </div>, document.body);
}
