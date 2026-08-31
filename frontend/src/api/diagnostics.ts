/**
 * The problem-report surface.
 *
 * Everything here is a local call to WG's own backend. Nothing in this module
 * transmits anything anywhere: the report is a file the user saves, and the
 * GitHub link is a link they choose to follow.
 */

export const ISSUES_URL = 'https://github.com/m3gnus/waveguide-generator/issues';
export const NEW_ISSUE_URL = `${ISSUES_URL}/new`;

/**
 * GitHub rejects a prefilled issue URL over 8191 bytes with a bare 414, so the
 * budget is set below it and the body is what gets cut. The report carries the
 * detail; the URL only has to carry enough to start the conversation.
 */
export const MAX_ISSUE_URL_BYTES = 6_000;

export interface DiagnosticsEngine {
  name: string;
  available: boolean;
  reason: string | null;
  version: string | null;
}

export interface DiagnosticsSummary {
  schema: number;
  createdAt: string;
  version: string;
  build: { label?: string; commit?: string | null; commit_short?: string | null; source?: string; dirty?: boolean };
  system: { platform: string | null; machine: string | null; python: string | null; cpuCount: number | null };
  dataDir: string;
  engines: DiagnosticsEngine[] | { status: string; reason: string };
  dependencies: { drift: string[]; pinned: Record<string, string>; installed: Record<string, string | null> } | null;
  storage: unknown;
  recentJobs: { id: string; run: number | null; status: string; engine: string | null; error: string | null }[];
  frontendErrors: number;
}

export interface DiagnosticsReport {
  summary: DiagnosticsSummary;
  /** The same facts as plain text, for a clipboard or a forum post. */
  text: string;
}

async function errorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
  } catch { /* HTTP status remains useful. */ }
  return `${fallback} (${response.status})`;
}

export async function getDiagnosticsReport(fetcher: typeof fetch = fetch): Promise<DiagnosticsReport> {
  const response = await fetcher('/api/diagnostics/summary');
  if (!response.ok) throw new Error(await errorMessage(response, 'Problem report details are unavailable'));
  return response.json() as Promise<DiagnosticsReport>;
}

export async function openLogsFolder(fetcher: typeof fetch = fetch): Promise<void> {
  const response = await fetcher('/api/diagnostics/open-logs', { method: 'POST' });
  if (!response.ok) throw new Error(await errorMessage(response, 'The logs folder could not be opened'));
}

/** A plain URL, so the download can be an anchor rather than a fetch. */
export function bundleUrl({ job, design }: { job?: string; design?: boolean } = {}): string {
  const query = new URLSearchParams();
  if (job) query.set('job', job);
  if (design) query.set('design', 'true');
  const suffix = query.toString();
  return `/api/diagnostics/bundle${suffix ? `?${suffix}` : ''}`;
}

/** The dropdown value in `.github/ISSUE_TEMPLATE/bug.yml`, matched exactly. */
export function platformOption(summary: DiagnosticsSummary | undefined): string | undefined {
  const platform = summary?.system.platform ?? '';
  if (platform.startsWith('Windows')) return 'Windows';
  if (platform.startsWith('Linux')) return 'Linux';
  if (!platform.startsWith('macOS') && !platform.startsWith('Darwin')) return undefined;
  const machine = (summary?.system.machine ?? '').toLowerCase();
  return machine === 'arm64' || machine === 'aarch64' ? 'macOS (Apple silicon)' : 'macOS (Intel)';
}

/**
 * A prefilled bug-report form, trimmed to fit.
 *
 * The build label and platform are prefilled because they are the two facts a
 * reporter cannot be expected to know and the two a maintainer always needs.
 * What happened is left empty on purpose: it is the one field only the user
 * can fill, and a placeholder in it reads as already answered.
 */
export function issueUrl(summary: DiagnosticsSummary | undefined, details = ''): string {
  const query = new URLSearchParams({ template: 'bug.yml' });
  const label = summary?.build.label ?? summary?.build.commit_short;
  if (label) query.set('build', label);
  const platform = platformOption(summary);
  if (platform) query.set('platform', platform);
  if (details.trim()) query.set('what-happened', details.trim());

  const url = `${NEW_ISSUE_URL}?${query}`;
  if (url.length <= MAX_ISSUE_URL_BYTES) return url;

  // Over budget: trim what happened rather than dropping the build label. The
  // label is a few dozen bytes and is the fact a maintainer always needs; the
  // full text is in the report the user is about to attach either way.
  //
  // Trimmed in a loop because one subtraction cannot get there: percent
  // encoding turns one newline into three characters, so the overflow measured
  // in the URL is not the number of characters to remove from the text.
  const withDetails = (value: string): string => {
    const trimmed = new URLSearchParams(query);
    trimmed.set('what-happened', `${value}${TRIM_NOTE}`);
    return `${NEW_ISSUE_URL}?${trimmed}`;
  };
  let text = details.trim();
  let candidate = withDetails(text);
  while (text.length > 0 && candidate.length > MAX_ISSUE_URL_BYTES) {
    const excess = candidate.length - MAX_ISSUE_URL_BYTES;
    // At worst every removed character was encoded as three, so dividing by
    // three cannot overshoot; the `1` guarantees the loop always makes progress.
    text = text.slice(0, Math.max(0, text.length - Math.max(1, Math.ceil(excess / 3))));
    candidate = withDetails(text);
  }
  query.delete('what-happened');
  return text ? candidate : `${NEW_ISSUE_URL}?${query}`;
}

const TRIM_NOTE = '\n\n[trimmed — the rest is in the attached report]';

export interface ClientErrorReport {
  message: string;
  stack?: string;
  source?: string;
  at?: string;
}

/** Report one interface error so the next problem report carries it. */
export async function reportClientError(
  entry: ClientErrorReport,
  fetcher: typeof fetch = fetch,
): Promise<boolean> {
  try {
    const response = await fetcher('/api/diagnostics/client-log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(entry),
    });
    return response.ok;
  } catch {
    // The interface is already failing. A failed attempt to say so is not a
    // second failure worth surfacing.
    return false;
  }
}
