/**
 * Would replacing the design on screen lose it?
 *
 * WG has no Save and no saved-file baseline, so New, Open, a CAD-linked open
 * and a Fusion project switch ask this instead of "is it unsaved?". The design
 * on screen is kept -- it exists somewhere it can be had back from -- when its
 * content key equals one of:
 *
 * - `openedContentKey`: the design as WG last opened it, or as WG itself last
 *   wrote it somewhere it can be opened from again (Export a copy, or the
 *   CAD-link registry a Send to CAD commits to);
 * - the key of a stored run's design, which can be loaded back from the run;
 * - the built-in default design's key, because New recreates that design.
 *
 * Anything else, a restored autosave draft included, is the only copy.
 *
 * The name is held constant on both sides of every comparison, so a rename
 * alone never counts as a loss. Settings are part of the key, except against
 * the built-in design: it carries none of its own, and New keeps the settings
 * on screen.
 *
 * Evaluated at transition time only, never in a render selector: keying a
 * design serializes it, and a half-typed directivity setting (a design that
 * cannot be keyed) must only ever mean "unkept", never a render failure.
 */
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { hydrateJobDesign, jobDesignAvailability } from '../jobs/jobDesign';
import { seedDesign, useDesignStore, type DesignDocument } from '../stores/design';
import { designContentKey, type DesignFileSettings } from '../stores/designContentKey';
import { wgSolveSettingsFromSolveOptions, wgSolveSettingsFromStore } from '../stores/designWire';
import { useDocumentStore } from '../stores/document';
import { useSolveOptionsStore } from '../stores/solveOptions';

/** The one name every kept comparison keys both sides under. */
const KEPT_NAME = '';

type StoredRun = Pick<JobItem, 'id' | 'script_snapshot'>
  & Partial<Pick<JobItem, 'solve_options' | 'design_availability' | 'config_summary'>>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function liveFileSettings(): DesignFileSettings {
  const state = useSolveOptionsStore.getState();
  return { polar: { ui: state.polar }, solve: wgSolveSettingsFromStore(state) };
}

/**
 * The key the replacement check compares for `design` under the settings on
 * screen, or null when it cannot be keyed. A caller about to write `design`
 * takes this before its first await, so it describes exactly what is written.
 */
export function keptContentKeyOf(design: DesignDocument): string | null {
  return designContentKey(design, KEPT_NAME, liveFileSettings());
}

/** `keptContentKeyOf` the design on screen; also what an open records. */
export function keptContentKeyNow(): string | null {
  return keptContentKeyOf(useDesignStore.getState().design);
}

/**
 * WG just wrote the content `key` stands for somewhere it can be opened from
 * again. It becomes the one remembered copy: a send overwrites the registry
 * copy the design may have been opened from, so the key from that open would
 * otherwise claim a copy that no longer exists.
 */
export function rememberWrittenCopy(key: string | null): void {
  useDocumentStore.getState().setOpenedContentKey(key);
}

/**
 * A send committed `sentKey`'s content to the CAD-link registry. When the
 * document on screen is the one that was sent, that content is its remembered
 * copy. When it is not but is linked to the same registry design, whatever it
 * was opened from has just been overwritten, so it keeps no copy at all.
 */
export function rememberSentCopy(
  sentKey: string | null,
  sentDesignId: string | null | undefined,
  documentWasSent: boolean,
): void {
  if (documentWasSent) {
    rememberWrittenCopy(sentKey);
    return;
  }
  if (sentDesignId && useDocumentStore.getState().identity?.designId === sentDesignId) {
    rememberWrittenCopy(null);
  }
}

const runKeys = new Map<string, { snapshot: unknown; options: unknown; key: string | null }>();

/**
 * A run's design under the same key, with the settings the run recorded.
 *
 * Only a design that can be put back on screen keeps anything: an imported
 * run's anchor, or a snapshot this build cannot read, is not a copy the user
 * could have back. Cached per stored snapshot, which a run never changes.
 */
function runContentKey(job: StoredRun): string | null {
  const cached = runKeys.get(job.id);
  if (cached && cached.snapshot === job.script_snapshot && cached.options === job.solve_options) {
    return cached.key;
  }
  const loadable = job.config_summary?.geometry_type !== 'imported'
    && jobDesignAvailability(job).reopenable;
  const design = loadable ? hydrateJobDesign(job) : null;
  const key = design
    ? designContentKey(design, KEPT_NAME, {
      polar: { config: job.solve_options?.polar_config },
      solve: wgSolveSettingsFromSolveOptions(job.solve_options),
    })
    : null;
  runKeys.set(job.id, { snapshot: job.script_snapshot, options: job.solve_options, key });
  return key;
}

/** The socket's list is filled by its first snapshot message; until then its cursor is null. */
function jobsListLoaded(): boolean {
  return jobsSocket.getSnapshot().cursor !== null;
}

/** Runs read over HTTP because the jobs socket had not delivered its list. */
let fetchedRuns: StoredRun[] | null = null;
let lastRunsReadAt: number | null = null;

function knownRuns(): readonly StoredRun[] {
  if (jobsListLoaded() || fetchedRuns === null) return jobsSocket.getSnapshot().jobs;
  return fetchedRuns;
}

/**
 * Read the run list over HTTP. A list that cannot be read keeps nothing: the
 * check then knows fewer runs, and at worst asks when it need not.
 */
let runsReadSequence = 0;

async function fetchRuns(fetcher: typeof fetch): Promise<void> {
  lastRunsReadAt = Date.now();
  // Only the newest read may land: an older one finishing last could bring
  // back a run deleted in between.
  const read = ++runsReadSequence;
  const land = (runs: StoredRun[] | null) => { if (read === runsReadSequence) fetchedRuns = runs; };
  try {
    const runs: StoredRun[] = [];
    const pageSize = 200;
    let offset = 0;
    for (let page = 0; page < 100; page += 1) {
      const response = await fetcher(`/api/jobs?limit=${pageSize}&offset=${offset}`);
      if (!response.ok) throw new Error(`jobs list answered ${response.status}`);
      const body = await response.json() as unknown;
      if (!isRecord(body) || !Array.isArray(body.items)) throw new Error('Invalid jobs list response');
      runs.push(...body.items.filter((item): item is StoredRun => isRecord(item) && typeof item.id === 'string'));
      offset += body.items.length;
      if (!body.items.length || typeof body.total !== 'number' || offset >= body.total) break;
    }
    land(runs);
  } catch {
    land(null);
  }
}

function keptWithoutRuns(key: string): boolean {
  if (key === useDocumentStore.getState().openedContentKey) return true;
  return key === designContentKey(seedDesign, KEPT_NAME, liveFileSettings());
}

/**
 * The verdict from what is already known, without fetching anything: for a
 * synchronous guard at the last instant before a replacement is applied.
 */
export function replacingWouldLoseNow(): boolean {
  const key = keptContentKeyNow();
  if (key === null) return true;
  if (keptWithoutRuns(key)) return false;
  return !knownRuns().some((job) => runContentKey(job) === key);
}

/**
 * The verdict at a transition. Reads the run list first when the jobs socket
 * has not delivered it; the verdict itself is taken after that read, so an
 * edit made while it was in flight is part of the answer.
 *
 * A replacement always reads afresh. Only a caller that merely wants to know
 * whether a wait has cleared -- and that re-asks before replacing anything --
 * may pass `reuseRunsReadWithinMs` to read at most that often.
 */
export async function replacingWouldLose(
  fetcher: typeof fetch = fetch,
  options: { reuseRunsReadWithinMs?: number } = {},
): Promise<boolean> {
  const key = keptContentKeyNow();
  if (key === null) return true;
  if (keptWithoutRuns(key)) return false;
  // A clock that stepped backwards makes the age negative, and that is not
  // "recent": it would otherwise stop the list being read until it caught up.
  const age = lastRunsReadAt === null ? null : Date.now() - lastRunsReadAt;
  const readRecently = age !== null && age >= 0 && age < (options.reuseRunsReadWithinMs ?? 0);
  if (!jobsListLoaded() && !readRecently) await fetchRuns(fetcher);
  return replacingWouldLoseNow();
}

/**
 * What a replacement asks before discarding the design on screen. There is no
 * Save to point at: a design is kept by solving it (its run keeps it) or by
 * exporting a copy, and either stops this question coming back.
 */
export function discardConfirmation(replacement: string): string {
  if (keptContentKeyNow() === null) {
    return `Discard the design on screen and ${replacement}? Its directivity settings are incomplete, `
      + 'so WG cannot tell whether a run or a copy holds it. To keep it, cancel and finish them, '
      + 'then solve it or export a copy.';
  }
  // One remembered copy, not every file ever opened: "may", because an older
  // file the design matches can still be on disk.
  return `Discard the design on screen and ${replacement}? No run matches it, and neither does what WG `
    + 'last opened, exported or sent, so this may lose it. To keep it, cancel, then solve it or export a copy.';
}
