import { hydrateDesignDocument } from '../api/designIo';
import type { JobItem } from '../api/jobsSocket';
import { useDesignStore, type DesignDocument } from '../stores/design';

/** The server's verdict on a job's stored design. See server/jobs/legacy_design.py. */
export interface DesignAvailability {
  reopenable: boolean;
  source: 'v2-snapshot' | 'v1-design-state' | 'v1-mesher-payload' | 'none';
  reason_code: 'ok' | 'recovered' | 'freeform_legacy_design' | 'no_stored_design' | 'unreadable_design';
  /** Why this job cannot be reopened. Present exactly when `reopenable` is false. */
  reason: string | null;
  /** A fidelity caveat about a design that *was* recovered. */
  note: string | null;
}

/**
 * Declared here rather than on `JobItem` so this stays readable against a
 * server that has not sent the field yet — an older build, or a replayed
 * fixture — instead of trusting a type that would be lying.
 */
export type JobDesignFields = Pick<JobItem, 'script_snapshot'> & {
  design_availability?: DesignAvailability | null;
};

const UNAVAILABLE_FALLBACK =
  'This job has no design that v2 can read back, so it cannot be reopened or rerun.';

export function jobDesignWire(job: Pick<JobItem, 'script_snapshot'>): Record<string, unknown> | null {
  const snapshot = job.script_snapshot;
  if (!snapshot) return null;
  if (snapshot.version === 1 && snapshot.design && typeof snapshot.design === 'object') {
    return snapshot.design as Record<string, unknown>;
  }
  // Read legacy, pre-versioned rows without ever casting them into store state.
  return typeof snapshot.formula === 'string' ? snapshot : null;
}

export function hydrateJobDesign(job: Pick<JobItem, 'script_snapshot'>): DesignDocument | null {
  const wire = jobDesignWire(job);
  if (!wire) return null;
  try { return hydrateDesignDocument(wire); } catch { return null; }
}

export function replaceWithJobDesign(
  job: Pick<JobItem, 'script_snapshot'>,
  options?: { keepHistory?: boolean },
): boolean {
  const design = hydrateJobDesign(job);
  if (!design) return false;
  useDesignStore.getState().replaceDesign(design, options);
  return true;
}

/**
 * What this job's design permits, and what to say about it.
 *
 * The server already translates a recoverable v1 job into v2's own snapshot
 * shape, so `reopenable` is true for almost everything and the interesting
 * case is the remainder: a job that cannot be reopened has to say *why*, in a
 * sentence a user can act on. Falling back to hydration keeps the answer
 * honest when the field is absent, and never reports a reason it does not have.
 */
export function jobDesignAvailability(job: JobDesignFields): DesignAvailability {
  const stated = job.design_availability;
  if (stated && typeof stated.reopenable === 'boolean') {
    return {
      ...stated,
      reason: stated.reopenable ? null : stated.reason || UNAVAILABLE_FALLBACK,
    };
  }
  const hydrated = hydrateJobDesign(job) !== null;
  return {
    reopenable: hydrated,
    source: hydrated ? 'v2-snapshot' : 'none',
    reason_code: hydrated ? 'ok' : 'no_stored_design',
    reason: hydrated ? null : UNAVAILABLE_FALLBACK,
    note: null,
  };
}

const CLIENT_UNREADABLE =
  'This job’s stored design could not be read by this version of the interface, so it cannot be reopened or rerun.';

/**
 * Whether this job's design can actually be put back on screen, and why not.
 *
 * The server's verdict is not the last word: it says the design converted,
 * this says the browser could also hydrate it. Both have to hold before a
 * control that reruns a design is allowed to be live, because the failure the
 * user would otherwise see is a run of the *current* design under an old job's
 * name -- which looks like it worked.
 */
export function jobRerunState(job: JobDesignFields): { enabled: boolean; reason: string | null } {
  const availability = jobDesignAvailability(job);
  if (!availability.reopenable) return { enabled: false, reason: availability.reason };
  if (hydrateJobDesign(job) === null) return { enabled: false, reason: CLIENT_UNREADABLE };
  return { enabled: true, reason: null };
}

/** True when this job's design can be put back on screen. */
export function canLoadJobDesign(job: JobDesignFields): boolean {
  return jobRerunState(job).enabled;
}
