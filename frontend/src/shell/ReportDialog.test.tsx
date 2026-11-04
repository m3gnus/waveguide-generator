import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { bundleUrl, issueUrl, platformOption, MAX_ISSUE_URL_BYTES, type DiagnosticsSummary } from '../api/diagnostics';
import { JOB_STATUSES } from '../api/jobsSocket';
import { ReportDialog, defaultJob, type ReportJob } from './ReportDialog';
import { activityStore } from './activity';

const SUMMARY: DiagnosticsSummary = {
  schema: 1,
  createdAt: '2026-08-31T00:00:00Z',
  version: '0.3.0',
  build: { label: '0.3.0+gabcd1234', commit_short: 'abcd1234', source: 'git' },
  system: { platform: 'Windows-11-10.0.26100-SP0', machine: 'AMD64', python: '3.13.3', cpuCount: 8 },
  dataDir: '~/AppData/Roaming/WaveguideGenerator',
  engines: [{ name: 'bempp', available: true, reason: '', version: '0.1.0' }],
  dependencies: { drift: [], pinned: {}, installed: {} },
  storage: {},
  recentJobs: [],
  frontendErrors: 0,
};

const JOBS: ReportJob[] = [
  { id: 'run-d', status: 'held', run_number: 4 },
  { id: 'run-c', status: 'complete', run_number: 3 },
  { id: 'run-b', status: 'error', run_number: 2 },
  { id: 'run-a', status: 'error', run_number: 1 },
];

describe('defaultJob', () => {
  it('picks the newest failure, because that is why the dialog is open', () => {
    expect(defaultJob(JOBS)).toBe('run-b');
  });

  it('falls back to the newest run when nothing failed', () => {
    expect(defaultJob([{ id: 'run-c', status: 'complete', run_number: 3 }])).toBe('run-c');
  });

  it('never takes a held request for a failure: it waits for the user', () => {
    expect(defaultJob([{ id: 'held', status: 'held' }, { id: 'failed', status: 'error' }])).toBe('failed');
  });

  it('never takes a request saved as an error before holds had a status for a failure', () => {
    const legacy: ReportJob = { id: 'legacy-hold', status: 'error', cad_state: { held: true } as ReportJob['cad_state'] };
    expect(defaultJob([legacy, { id: 'failed', status: 'error' }])).toBe('failed');
  });

  it('reads only statuses the jobs API sends', () => {
    expect(JOBS.every((job) => JOB_STATUSES.includes(job.status))).toBe(true);
  });

  it('has no answer when there are no runs at all', () => {
    expect(defaultJob([])).toBeUndefined();
  });
});

describe('bundleUrl', () => {
  it('asks for nothing extra by default', () => {
    expect(bundleUrl()).toBe('/api/diagnostics/bundle');
  });

  it('carries the run and the design opt-in', () => {
    expect(bundleUrl({ job: 'run-b', design: true })).toBe('/api/diagnostics/bundle?job=run-b&design=true');
  });

  it('never asks for the design implicitly', () => {
    expect(bundleUrl({ job: 'run-b' })).not.toContain('design');
  });
});

describe('issueUrl', () => {
  it('prefills the two facts a reporter cannot be expected to know', () => {
    const url = new URL(issueUrl(SUMMARY));
    expect(url.searchParams.get('template')).toBe('bug.yml');
    expect(url.searchParams.get('build')).toBe('0.3.0+gabcd1234');
    expect(url.searchParams.get('platform')).toBe('Windows');
  });

  it('leaves what happened to the user when they have not written anything', () => {
    expect(new URL(issueUrl(SUMMARY)).searchParams.has('what-happened')).toBe(false);
  });

  it('stays under GitHub’s URL ceiling, which answers 414 rather than truncating', () => {
    const url = issueUrl(SUMMARY, 'x'.repeat(20_000));
    expect(url.length).toBeLessThanOrEqual(MAX_ISSUE_URL_BYTES);
    expect(new URL(url).searchParams.get('build')).toBe('0.3.0+gabcd1234');
  });

  it('never carries the summary text or an incident’s words into the public issue', () => {
    const withIncident: DiagnosticsSummary = {
      ...SUMMARY,
      recentIncidents: [{
        id: 'domain-0123456789ab', kind: 'domain', phase: 'cad-preparation', count: 1,
        firstSeen: '2026-10-24T00:00:00Z', lastSeen: '2026-10-24T00:00:00Z', ids: { job_id: 'job-1' },
        message: 'Kestrel horn cannot be rebuilt',
      }],
    };
    const url = decodeURIComponent(issueUrl(withIncident, 'It crashed'));
    expect(url).not.toContain('Kestrel');
    expect(url).not.toContain('domain-0123456789ab');
    expect(new URL(issueUrl(withIncident, 'It crashed')).searchParams.get('what-happened')).toBe('It crashed');
  });

  it('still produces a usable link with no summary yet', () => {
    expect(issueUrl(undefined)).toContain('template=bug.yml');
  });
});

describe('platformOption', () => {
  it('matches the dropdown values in the issue form exactly', () => {
    expect(platformOption(SUMMARY)).toBe('Windows');
    expect(platformOption({ ...SUMMARY, system: { ...SUMMARY.system, platform: 'macOS-15.0-arm64', machine: 'arm64' } })).toBe('macOS (Apple silicon)');
    expect(platformOption({ ...SUMMARY, system: { ...SUMMARY.system, platform: 'macOS-13.0-x86_64', machine: 'x86_64' } })).toBe('macOS (Intel)');
    expect(platformOption({ ...SUMMARY, system: { ...SUMMARY.system, platform: 'Linux-6.8.0', machine: 'x86_64' } })).toBe('Linux');
    expect(platformOption(undefined)).toBeUndefined();
  });
});

describe('ReportDialog', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.stubGlobal('URL', class extends URL {
      static createObjectURL = vi.fn(() => 'blob:report');
      static revokeObjectURL = vi.fn();
    });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ summary: SUMMARY, text: 'Waveguide Generator 0.3.0 (0.3.0+gabcd1234)' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )));
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    activityStore.reset();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  async function render(open = true) {
    await act(async () => {
      root.render(<ReportDialog open={open} jobs={JOBS} onClose={() => undefined}/>);
    });
  }

  function button(label: string): HTMLButtonElement {
    return [...host.querySelectorAll('button')].find((entry) => entry.textContent === label)!;
  }

  async function describeProblem(text: string) {
    await act(async () => {
      const textarea = host.querySelector('textarea')!;
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, text);
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  it('renders nothing while closed, and asks the backend for nothing', async () => {
    await render(false);
    expect(document.querySelector('.report-dialog')).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('shows the summary and defaults the run selector to the newest failure', async () => {
    await render();
    expect(document.querySelector('.report-summary pre')?.textContent)
      .toContain('Waveguide Generator 0.3.0');
    expect(document.querySelector<HTMLSelectElement>('.report-field select')?.value).toBe('run-b');
  });

  it('shows what may be shared before anything is: the copied text, and that the file names parts', async () => {
    await render();
    expect(document.querySelector('.report-summary-scope')?.textContent)
      .toContain('exactly what Copy summary puts on the clipboard');
    expect(document.querySelector('.report-file-scope')?.textContent)
      .toContain('Model and part names may appear');
  });

  it('opens with the design box clear every time', async () => {
    await render();
    const checkbox = document.querySelector<HTMLInputElement>('.report-design-toggle input')!;
    expect(checkbox.checked).toBe(false);

    await act(async () => {
      checkbox.checked = true;
      checkbox.dispatchEvent(new Event('click', { bubbles: true }));
    });
    // Close and reopen: the opt-in must not survive into a later report.
    await render(false);
    await render();
    expect(document.querySelector<HTMLInputElement>('.report-design-toggle input')!.checked).toBe(false);
  });

  it('keeps saving available without requiring a diagnostic summary', async () => {
    await render();
    expect(button('Save report').disabled).toBe(false);
  });

  it('saves the current complete description, run and design choice through the bundle owner', async () => {
    await render();
    const description = 'Reproduction step\n'.repeat(1500) + 'UNIQUE FINAL MARKER';
    await describeProblem('Earlier draft');
    await describeProblem(description);
    await act(async () => {
      host.querySelector<HTMLInputElement>('.report-design-toggle input')!.click();
      const select = host.querySelector('select')!;
      select.value = 'run-a';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    const zip = new Uint8Array([80, 75, 3, 4, 0, 255]);
    const fetcher = vi.mocked(fetch).mockResolvedValue(new Response(zip, {
      headers: { 'Content-Type': 'application/zip', 'Content-Disposition': 'attachment; filename="wg-report-test.zip"' },
    }));
    const issue = new URL(host.querySelector<HTMLAnchorElement>('a[target="_blank"]')!.href);
    expect(issue.href.length).toBeLessThanOrEqual(MAX_ISSUE_URL_BYTES);
    expect(issue.searchParams.get('what-happened')).not.toContain('UNIQUE FINAL MARKER');
    await act(async () => {
      button('Save report').click();
      await vi.waitFor(() => expect(URL.createObjectURL).toHaveBeenCalled());
    });
    expect(fetcher).toHaveBeenLastCalledWith('/api/diagnostics/bundle?job=run-a&design=true', expect.objectContaining({
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ description }),
    }));
    const saved = vi.mocked(URL.createObjectURL).mock.calls[0][0] as Blob;
    expect(new Uint8Array(await saved.arrayBuffer())).toEqual(zip);
    const anchor = vi.mocked(HTMLAnchorElement.prototype.click).mock.instances[0] as HTMLAnchorElement;
    expect(anchor.download).toBe('wg-report-test.zip');
  });

  it('registers saving in the status bar and offers an actionable failure without starting a download', async () => {
    await render();
    let resolve!: (response: Response) => void;
    vi.mocked(fetch).mockImplementation(() => new Promise((done) => { resolve = done; }));
    await act(async () => { button('Save report').click(); });
    expect(button('Saving report…').disabled).toBe(true);
    expect(activityStore.getSnapshot()).toEqual([expect.objectContaining({ label: 'Saving problem report' })]);
    await act(async () => { resolve(new Response(JSON.stringify({ detail: 'This run no longer exists. Choose another run for the report.' }), { status: 404 })); });
    expect(host.textContent).toContain('This run no longer exists. Choose another run for the report.');
    expect(button('Save report').disabled).toBe(false);
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(activityStore.getSnapshot()).toEqual([]);
  });

  it('explains how to recover when the request body exceeds the report limit', async () => {
    await render();
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ detail: 'Request body exceeds the 1 MB limit.' }), { status: 413 }));
    await act(async () => { button('Save report').click(); });
    expect(host.textContent).toContain('Keep What happened to at most 64,000 characters, then save again.');
    expect(button('Save report').disabled).toBe(false);
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it('reports a backend that cannot answer instead of showing an empty box', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ detail: 'WG could not gather the report details. Try again.' }),
      { status: 500, headers: { 'Content-Type': 'application/json' } },
    )));
    await render();
    expect(document.querySelector('.report-dialog-body')?.textContent)
      .toContain('WG could not gather the report details. Try again.');
    // The report itself is still reachable: the summary is a convenience.
    expect(button('Save report').disabled).toBe(false);
  });

  it('cannot copy until the current diagnostics and their response body arrive', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    let resolve!: (value: { summary: DiagnosticsSummary; text: string }) => void;
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: () => new Promise((done) => { resolve = done; }) } as Response);
    await render();
    expect(button('Copy summary').disabled).toBe(true);
    expect(host.querySelector('pre')?.textContent).toBe('Collecting…');
    expect(activityStore.getSnapshot()).toEqual([expect.objectContaining({ label: 'Collecting problem report details' })]);
    await act(async () => { button('Copy summary').click(); });
    expect(writeText).not.toHaveBeenCalled();
    expect(host.textContent).not.toContain('Summary copied.');

    await act(async () => { resolve({ summary: SUMMARY, text: 'Current summary' }); });
    expect(button('Copy summary').disabled).toBe(false);
    expect(activityStore.getSnapshot()).toEqual([]);
    await describeProblem('New reproduction');
    await act(async () => { button('Copy summary').click(); });
    expect(writeText).toHaveBeenCalledWith('Current summary\n\nNew reproduction');
    expect(host.textContent).toContain('Summary copied.');
  });

  it('invalidates a previous summary on reopening, keeps Copy disabled after failure and retries', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    await render();
    await render(false);
    let reject!: (reason: Error) => void;
    vi.mocked(fetch).mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; }));
    await render();
    expect(host.querySelector('pre')?.textContent).toBe('Collecting…');
    expect(button('Copy summary').disabled).toBe(true);
    await act(async () => { reject(new Error('WG could not gather the report details. Try again.')); });
    expect(host.querySelector('pre')?.textContent).toBe('Unavailable.');
    expect(button('Copy summary').disabled).toBe(true);
    await act(async () => { button('Copy summary').click(); });
    expect(writeText).not.toHaveBeenCalled();
    expect(activityStore.getSnapshot()).toEqual([]);

    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({ summary: SUMMARY, text: 'Retried summary' })));
    await act(async () => { button('Retry').click(); });
    expect(host.querySelector('pre')?.textContent).toBe('Retried summary');
    expect(host.querySelector('.notice-error')).toBeNull();
    expect(button('Copy summary').disabled).toBe(false);
    await act(async () => { button('Copy summary').click(); });
    expect(writeText).toHaveBeenCalledWith('Retried summary');
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it('ignores an older response that arrives after reopening has loaded a fresh summary', async () => {
    let resolve!: (response: Response) => void;
    vi.mocked(fetch).mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    await render();
    await render(false);
    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({ summary: SUMMARY, text: 'Fresh summary' })));
    await render();
    await act(async () => { resolve(new Response(JSON.stringify({ summary: SUMMARY, text: 'Stale summary' }))); });
    expect(host.querySelector('pre')?.textContent).toBe('Fresh summary');
  });
});
