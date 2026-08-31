import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { bundleUrl, issueUrl, platformOption, MAX_ISSUE_URL_BYTES, type DiagnosticsSummary } from '../api/diagnostics';
import { ReportDialog, defaultJob, type ReportJob } from './ReportDialog';

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
  { id: 'run-c', status: 'succeeded', run_number: 3 },
  { id: 'run-b', status: 'failed', run_number: 2 },
  { id: 'run-a', status: 'failed', run_number: 1 },
];

describe('defaultJob', () => {
  it('picks the newest failure, because that is why the dialog is open', () => {
    expect(defaultJob(JOBS)).toBe('run-b');
  });

  it('falls back to the newest run when nothing failed', () => {
    expect(defaultJob([{ id: 'run-c', status: 'succeeded', run_number: 3 }])).toBe('run-c');
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
    vi.unstubAllGlobals();
  });

  async function render(open = true) {
    await act(async () => {
      root.render(<ReportDialog open={open} jobs={JOBS} onClose={() => undefined}/>);
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

  it('points the download at the selected run without the design', async () => {
    await render();
    const link = document.querySelector<HTMLAnchorElement>('.report-dialog-actions a.primary')!;
    expect(link.getAttribute('href')).toBe('/api/diagnostics/bundle?job=run-b');
    expect(link.hasAttribute('download')).toBe(true);
  });

  it('reports a backend that cannot answer instead of showing an empty box', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ detail: 'Problem report details are unavailable' }),
      { status: 500, headers: { 'Content-Type': 'application/json' } },
    )));
    await render();
    expect(document.querySelector('.report-dialog-body')?.textContent)
      .toContain('Problem report details are unavailable');
    // The report itself is still reachable: the summary is a convenience.
    expect(document.querySelector<HTMLAnchorElement>('.report-dialog-actions a.primary')).not.toBeNull();
  });
});
