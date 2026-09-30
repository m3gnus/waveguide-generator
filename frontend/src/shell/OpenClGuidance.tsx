import sharedGuidance from '../../../shared/opencl-driver-guidance.v1.json';

export type OpenClGuidancePlatform = 'windows' | 'linux';
export type OpenClGuidanceReason = 'no_device' | 'smoke_test_failed' | 'smoke_test_timeout' | 'inventory_timeout' | 'pocl_windows';
export interface OpenClGuidanceProps {
  platform: OpenClGuidancePlatform;
  reason?: OpenClGuidanceReason;
}

interface GuidanceData {
  version: number;
  title: string;
  labels: { heading: string; ariaLabel: string };
  reasons?: Partial<Record<OpenClGuidanceReason, string>>;
  platforms: Partial<Record<OpenClGuidancePlatform, {
    summary: string;
    steps?: { vendor: string; label: string; url: string; note: string }[];
  }>>;
  warnings?: { id: string; platforms: OpenClGuidancePlatform[]; text: string }[];
  gpu_alternatives?: { id: string; platforms: OpenClGuidancePlatform[]; text: string }[];
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const isText = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0;
const isPlatform = (value: unknown): value is OpenClGuidancePlatform => value === 'windows' || value === 'linux';

/** Missing optional content is safe; malformed types suppress the guidance. */
export function isOpenClGuidanceData(value: unknown): value is GuidanceData {
  if (!isRecord(value) || value.version !== 1 || !isText(value.title)
    || !isRecord(value.labels) || !isText(value.labels.heading) || !isText(value.labels.ariaLabel)
    || !isRecord(value.platforms)) return false;
  if (value.reasons !== undefined
    && (!isRecord(value.reasons) || !Object.values(value.reasons).every(isText))) return false;
  if (!Object.entries(value.platforms).every(([platform, entry]) => isPlatform(platform)
    && isRecord(entry) && isText(entry.summary)
    && (entry.steps === undefined || (Array.isArray(entry.steps) && entry.steps.every((step: unknown) =>
      isRecord(step) && ['vendor', 'label', 'url', 'note'].every((key) => isText(step[key]))))))) return false;
  return [value.warnings, value.gpu_alternatives].every((entries) => entries === undefined
    || (Array.isArray(entries) && entries.every((entry: unknown) =>
      isRecord(entry) && isText(entry.id) && isText(entry.text)
      && Array.isArray(entry.platforms) && entry.platforms.every(isPlatform))));
}

// Validate once, before rendering. Tests assert that the shipped JSON passes.
const guidance: GuidanceData | undefined = isOpenClGuidanceData(sharedGuidance) ? sharedGuidance : undefined;

/** Shared CPU runtime guidance for solver settings and the numba fallback notice. */
export function OpenClGuidance({ platform, reason }: OpenClGuidanceProps) {
  const entry = guidance?.platforms[platform];
  if (!guidance || !entry) return null;
  const { summary, steps = [] } = entry;
  const reasonLine = reason && guidance.reasons?.[reason];
  const warnings = (guidance.warnings ?? []).filter((warning) => warning.platforms.includes(platform));
  const alternatives = (guidance.gpu_alternatives ?? []).filter((entry) => entry.platforms.includes(platform));
  return <section className="opencl-guidance" aria-label={guidance.labels.ariaLabel}>
    <h3>{guidance.labels.heading}</h3>
    {reasonLine && <p>{reasonLine}</p>}
    <p>{summary}</p>
    {alternatives.map(({ id, text }) => <p key={id}>{text}</p>)}
    {steps.length > 0 && <ul>
      {steps.map(({ vendor, label, url, note }) => <li key={vendor}>
        <a href={url} target="_blank" rel="noopener noreferrer">{label}</a>
        <p>{note}</p>
      </li>)}
    </ul>}
    {warnings.map(({ id, text }) => <p key={id}>{text}</p>)}
  </section>;
}
