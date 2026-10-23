import type { JobItem } from '../api/jobsSocket';
import { hydrateJobDesign } from '../jobs/jobDesign';
import { serializeDesign, type DesignDocument } from '../stores/design';

/**
 * A content identity for a design: equal fingerprints mean the same model.
 *
 * Taken from the serialized wire form rather than from the store document,
 * because that is the form a run was solved from and the form its snapshot is
 * read back as -- a fingerprint is only worth anything if a design reopened
 * from a file, undone back to what was solved, or browsed to in the run list
 * hashes equal to the run it came from. Everything `serializeDesign` drops is
 * therefore excluded by construction: `simulation.solver_mode` (a machine
 * preference that never leaves this computer), `enclosure.baffle_margin` and
 * the `quadrants` mirror, and `guiding_curve` off OSSE. `_expressions` is
 * stripped on top of that, being ATH spelling for a value already present as a
 * scalar; `_absent` is kept, because it decides whether the wire states a
 * value or leaves it to the server.
 */
const designFingerprints = new WeakMap<DesignDocument, string>();

export function designFingerprint(design: DesignDocument): string {
  // Keyed by document identity: the store hands out a new object per edit, so
  // this is one hash per edit however many surfaces ask for it per render.
  const cached = designFingerprints.get(design);
  if (cached !== undefined) return cached;
  const { _expressions, ...rest } = design;
  const fingerprint = fnv1a(canonicalJson(serializeDesign(rest as DesignDocument)));
  designFingerprints.set(design, fingerprint);
  return fingerprint;
}

/** Key order is an artifact of how a document was built, never a difference. */
function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value !== null && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(',')}}`;
  }
  return JSON.stringify(value) ?? 'null';
}

/** FNV-1a, widened by the canonical length so the compare keys stay short. */
function fnv1a(text: string): string {
  let value = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    value ^= text.charCodeAt(index);
    value = Math.imul(value, 0x01000193);
  }
  return `${(value >>> 0).toString(36)}.${text.length.toString(36)}`;
}

/**
 * The fingerprint of the design a run was solved from, or null when the run
 * stored none this build can read.
 *
 * Cached against the snapshot the job arrived with: a run's stored design is
 * immutable, so the hash survives every jobs message that re-sends the row.
 */
const jobDesignFingerprints = new Map<string, { snapshot: JobItem['script_snapshot']; fingerprint: string | null }>();

export function jobDesignFingerprint(job: Pick<JobItem, 'id' | 'script_snapshot'>): string | null {
  const cached = jobDesignFingerprints.get(job.id);
  if (cached && cached.snapshot === job.script_snapshot) return cached.fingerprint;
  const design = hydrateJobDesign(job);
  const fingerprint = design ? designFingerprint(design) : null;
  jobDesignFingerprints.set(job.id, { snapshot: job.script_snapshot, fingerprint });
  return fingerprint;
}
