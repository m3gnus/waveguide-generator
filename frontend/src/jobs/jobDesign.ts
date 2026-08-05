import { hydrateDesignDocument } from '../api/designIo';
import type { JobItem } from '../api/jobsSocket';
import { useDesignStore, type DesignDocument } from '../stores/design';

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
