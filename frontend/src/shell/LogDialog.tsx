import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from './icons';
import { trapDialogFocus } from './SettingsDialog';

export function LogDialog({ jobId, onClose }: { jobId: string; onClose: () => void }) {
  const dialog = useRef<HTMLDivElement>(null);
  const requestGeneration = useRef(0);
  const [contents, setContents] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [copyStatus, setCopyStatus] = useState<string>();

  const refresh = useCallback(async () => {
    const request = ++requestGeneration.current;
    setLoading(true);
    setError(undefined);
    setCopyStatus(undefined);
    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/log`);
      if (!response.ok) throw new Error(`Log request failed (${response.status})`);
      const text = await response.text();
      if (request === requestGeneration.current) setContents(text);
    } catch (reason) {
      if (request === requestGeneration.current) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (request === requestGeneration.current) setLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    void refresh();
    return () => { requestGeneration.current += 1; };
  }, [refresh]);

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focus = requestAnimationFrame(() => {
      dialog.current?.querySelector<HTMLElement>('button:not([disabled])')?.focus();
    });
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
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
  }, [onClose]);

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
  return createPortal(<div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div ref={dialog} className="settings-dialog log-dialog" role="dialog" aria-modal="true" aria-labelledby="job-log-title">
      <header><div><h2 id="job-log-title">Job log</h2><p>Complete output for this run.</p></div><button className="dialog-close" aria-label="Close job log" onClick={onClose}><Icon name="close"/></button></header>
      <div className="settings-scroll log-dialog-body">
        <div className="log-dialog-actions">
          <button disabled={loading} onClick={() => void refresh()}>Refresh</button>
          <button disabled={loading || !contents} onClick={() => void copy()}>Copy</button>
          {copyStatus && <span role="status">{copyStatus}</span>}
        </div>
        {error && <p className="workspace-settings-error" role="alert">{error}</p>}
        <pre className="job-log-content" aria-busy={loading}>{loading && !contents ? 'Loading log…' : contents}</pre>
      </div>
    </div>
  </div>, document.body);
}
