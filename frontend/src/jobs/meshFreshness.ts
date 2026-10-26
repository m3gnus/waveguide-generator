import { create } from 'zustand';
import { deadlineFetch } from '../api/deadlineFetch';
import type { JobItem } from '../api/jobsSocket';
import type { components } from '../api/generated/openapi';

type Freshness = 'pending' | components['schemas']['MeshFreshnessResponse']['freshness'];

interface MeshFreshnessStore {
  jobs: Record<string, Freshness>;
  check(job: JobItem, force?: boolean, epoch?: number | null): void;
}

const requests = new Map<string, object>();
const checkedEpochs = new Map<string, number>();

export function hasMeshProvenance(job: Pick<JobItem, 'provenance'> | null | undefined): boolean {
  const version = job?.provenance?.mesher_version;
  const hash = job?.provenance?.geometry_hash;
  return typeof version === 'string' && version !== '' && version !== 'unknown'
    && typeof hash === 'string' && hash !== '';
}

/** A job's stored mesh identity is immutable; checking it is an on-demand read. */
export const useMeshFreshnessStore = create<MeshFreshnessStore>((set, get) => ({
  jobs: {},
  check(job, force = false, epoch = null) {
    // A backend restart may reuse its first epoch. Forget every prior check
    // when the connection drops, then recheck on the next hello.
    if (epoch === null) checkedEpochs.clear();
    if (!hasMeshProvenance(job)) {
      requests.delete(job.id);
      checkedEpochs.delete(job.id);
      if (get().jobs[job.id]) set(state => {
        const { [job.id]: _previous, ...jobs } = state.jobs;
        return { jobs };
      });
      return;
    }
    if (epoch !== null && checkedEpochs.get(job.id) !== epoch) force = true;
    if (!force && get().jobs[job.id]) return;
    if (epoch !== null) checkedEpochs.set(job.id, epoch);
    const ticket = {};
    requests.set(job.id, ticket);
    set(state => ({ jobs: { ...state.jobs, [job.id]: 'pending' } }));
    void deadlineFetch(fetch, `/api/jobs/${encodeURIComponent(job.id)}/mesh-freshness`)
      .then(async response => {
        if (!response.ok) return 'unknown';
        const payload = await response.json() as { freshness?: unknown };
        return payload.freshness === 'current' || payload.freshness === 'stale' ? payload.freshness : 'unknown';
      })
      .catch(() => 'unknown' as const)
      .then(freshness => {
        if (requests.get(job.id) !== ticket) return;
        requests.delete(job.id);
        set(state => ({ jobs: { ...state.jobs, [job.id]: freshness } }));
      });
  },
}));
