import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  bundleUrl,
  getDiagnosticsReport,
  issueUrl,
  openLogsFolder,
  type DiagnosticsReport,
} from '../api/diagnostics';
import { Icon } from './icons';
import { useModalDialogFocus } from './dialogFocus';

export interface ReportJob {
  id: string;
  status: string;
  run_number?: number | null;
  label?: string | null;
}

/**
 * The run a report should describe, chosen for the user.
 *
 * Somebody opening this dialog has almost always just watched a solve fail, so
 * the newest failure is the answer nearly every time. Falling back to the
 * newest run of any status keeps the selector useful for "it finished but the
 * result is wrong", which is the other half of the reports that arrive.
 */
export function defaultJob(jobs: readonly ReportJob[]): string | undefined {
  return (jobs.find((job) => job.status === 'failed') ?? jobs[0])?.id;
}

export function jobLabel(job: ReportJob): string {
  const run = job.run_number ? `Run ${job.run_number}` : job.id.slice(0, 8);
  return `${run} · ${job.status}`;
}

/**
 * Everything a bug report needs, in one file, without leaving the application.
 *
 * The three buttons are three destinations for the same artefact, because the
 * audience splits three ways: people who will open a GitHub issue, people who
 * will paste into a forum thread, and people whose application is broken
 * enough that all they can do is find the folder. Nothing here transmits
 * anything; every action is local until the user chooses otherwise.
 */
export function ReportDialog({ open, jobs, onClose }: {
  open: boolean;
  jobs: readonly ReportJob[];
  onClose: () => void;
}) {
  const dialog = useModalDialogFocus<HTMLDivElement>({ open, onClose });
  const [report, setReport] = useState<DiagnosticsReport>();
  const [error, setError] = useState<string>();
  const [feedback, setFeedback] = useState<string>();
  const [details, setDetails] = useState('');
  const [includeDesign, setIncludeDesign] = useState(false);
  const [job, setJob] = useState<string>();

  // Read through a ref so the effect below can seed itself from the current
  // run list without re-running whenever that list changes: a solve finishing
  // behind the dialog must not move the selection out from under whoever is
  // reading it, nor re-tick a box they just unticked.
  const latestJobs = useRef(jobs);
  latestJobs.current = jobs;

  // Off every time the dialog opens. A checkbox that remembered "yes" would
  // eventually ship a design the user had stopped thinking about.
  useEffect(() => {
    if (!open) return;
    setIncludeDesign(false);
    setFeedback(undefined);
    setJob(defaultJob(latestJobs.current));
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let current = true;
    setError(undefined);
    getDiagnosticsReport()
      .then((value) => { if (current) setReport(value); })
      .catch((reason: unknown) => {
        if (current) setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => { current = false; };
  }, [open]);

  const downloadUrl = useMemo(
    () => bundleUrl({ job, design: includeDesign }),
    [includeDesign, job],
  );

  const copySummary = useCallback(async () => {
    const text = [report?.text, details.trim()].filter(Boolean).join('\n\n');
    try {
      await navigator.clipboard.writeText(text);
      setFeedback('Summary copied.');
    } catch {
      setFeedback('Copying is unavailable here — select the text above instead.');
    }
  }, [details, report?.text]);

  const openFolder = useCallback(async () => {
    try {
      await openLogsFolder();
      setFeedback('Opened the logs folder.');
    } catch (reason) {
      setFeedback(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);

  if (!open) return null;
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div ref={dialog} className="settings-dialog report-dialog" role="dialog" aria-modal="true" aria-labelledby="report-dialog-title">
      <header>
        <div>
          <h2 id="report-dialog-title">Report a problem</h2>
          <p>Saves one file with the logs and what this machine can solve. Nothing is sent anywhere until you send it.</p>
        </div>
        <button className="dialog-close" aria-label="Close problem report" onClick={onClose}><Icon name="close"/></button>
      </header>
      <div className="settings-scroll report-dialog-body">
        <label className="report-field">
          <span>What happened</span>
          <textarea
            rows={4}
            value={details}
            placeholder="What you did, what you expected, and what WG did instead."
            onChange={(event) => setDetails(event.target.value)}
          />
        </label>

        <label className="report-field">
          <span>Include the log of</span>
          <select value={job ?? ''} onChange={(event) => setJob(event.target.value || undefined)}>
            <option value="">No run — application log only</option>
            {jobs.map((entry) => <option key={entry.id} value={entry.id}>{jobLabel(entry)}</option>)}
          </select>
        </label>

        <label className="report-design-toggle">
          <input type="checkbox" checked={includeDesign} onChange={(event) => setIncludeDesign(event.target.checked)}/>
          <span>
            <b>Include my current design</b>
            <small>Off by default. Tick this only if the problem is about a particular geometry — without it the report carries no design, no driver library and no CAD project.</small>
          </span>
        </label>

        <section className="report-summary" aria-labelledby="report-summary-title">
          <h3 id="report-summary-title">What this report says about your machine</h3>
          {error && <p className="workspace-settings-error" role="status">{error}</p>}
          <pre tabIndex={0}>{report?.text ?? (error ? 'Unavailable.' : 'Collecting…')}</pre>
          <p className="cad-settings-note">The saved file adds the application log, the selected run&rsquo;s log, and a <code>manifest.json</code> listing everything in it. Your user name is rewritten out of every path.</p>
        </section>

        <div className="report-dialog-actions">
          <a className="primary" href={downloadUrl} download>Save report</a>
          <button onClick={() => void copySummary()}>Copy summary</button>
          <button onClick={() => void openFolder()}>Open logs folder</button>
          <a href={issueUrl(report?.summary, details)} target="_blank" rel="noreferrer">Open a GitHub issue</a>
        </div>
        <p className="cad-settings-note">Save the report first, then drag it into the issue. Suggestions go in the same place — an issue that says what you would like WG to do.</p>
        {feedback && <p className="update-feedback" role="status" aria-atomic="true">{feedback}</p>}
      </div>
    </div>
  </div>;
}
