import { jobDesignAvailability, jobRerunState, type JobDesignFields } from './jobDesign';

/**
 * The two places a job's design verdict has to show up.
 *
 * Almost every imported v1 job is translated by the server and behaves like a
 * natively solved one. The rest are the reason these exist: a Rerun that does
 * nothing, or worse silently runs whatever is currently on screen under an old
 * job's name, is not an acceptable way to say "this design was never stored".
 *
 * Styling is inline against the existing tokens rather than a new stylesheet
 * rule, so this drops into a job card without a matching CSS change.
 */

const NOTICE_STYLE = {
  margin: '7px 0 0',
  padding: '5px 6px',
  border: '1px solid var(--hair)',
  borderRadius: 3,
  background: 'var(--ov1)',
  color: 'var(--fg2)',
  font: '9.5px/1.45 var(--ui, inherit)',
  overflow: 'visible',
  textOverflow: 'clip',
  whiteSpace: 'normal',
} as const;

/** Why this job cannot be reopened, or what its recovered design is missing. */
export function DesignAvailabilityNotice({ job }: { job: JobDesignFields }) {
  // A reason outranks a note: they never both apply, but if that ever changes
  // the blocking sentence is the one the user needs first.
  const message = jobRerunState(job).reason ?? jobDesignAvailability(job).note;
  if (!message) return null;
  return <p role="note" className="job-design-notice" style={NOTICE_STYLE}>{message}</p>;
}

export function RerunButton({ job, onRerun, label = 'Rerun', className = 'primary' }: {
  job: JobDesignFields;
  onRerun: () => void;
  label?: string;
  className?: string;
}) {
  const { enabled, reason } = jobRerunState(job);
  return <button
    className={className}
    disabled={!enabled}
    // The tooltip carries the same sentence as the notice, so the disabled
    // control explains itself on its own terms too.
    title={reason ?? 'Run this design again'}
    onClick={enabled ? onRerun : undefined}
  >{label}</button>;
}
