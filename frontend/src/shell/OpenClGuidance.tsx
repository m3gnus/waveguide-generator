import guidance from '../../../shared/opencl-driver-guidance.v1.json';

export type OpenClGuidancePlatform = 'windows' | 'linux';
export type OpenClGuidanceReason = 'no_device' | 'smoke_test_failed' | 'smoke_test_timeout' | 'pocl_windows';
export interface OpenClGuidanceProps {
  platform: OpenClGuidancePlatform;
  reason?: OpenClGuidanceReason;
}

/** Shared CPU runtime guidance for solver settings and the numba fallback notice. */
export function OpenClGuidance({ platform, reason }: OpenClGuidanceProps) {
  const { summary, steps } = guidance.platforms[platform];
  const warnings = guidance.warnings.filter((warning) => warning.platforms.includes(platform));
  return <section className="opencl-guidance" aria-label="CPU OpenCL runtime">
    <h3>CPU OpenCL runtime</h3>
    {reason && <p>{guidance.reasons[reason]}</p>}
    <p>{summary}</p>
    <ul>
      {steps.map(({ vendor, label, url, note }) => <li key={vendor}>
        <a href={url} target="_blank" rel="noopener noreferrer">{label}</a>
        <p>{note}</p>
      </li>)}
    </ul>
    {warnings.map(({ id, text }) => <p key={id}>{text}</p>)}
  </section>;
}
