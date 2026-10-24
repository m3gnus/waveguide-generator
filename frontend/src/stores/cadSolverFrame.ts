import { create } from 'zustand';
import {
  getSolverFrame,
  type SolverFramePreview,
  type SolverFrameState,
} from '../api/solverFrame';
import type { SolverFrameAxis } from '../viewport/solverFrame';

/**
 * The solver frame of the CAD model on screen, as the Solve card shows it
 * (PLAN.md M1b/M1e, docs/architecture/CAD-OPERATIONS.md "Unlinked solver frame").
 *
 * One answer per ingestion: what the server says about the frame, and the axis
 * the card shows as chosen. That axis is the server's preselection -- the
 * project's confirmed frame, a v1 confirmation carried forward, or WG's own
 * decision (confident, or the documented fallback) -- until the user picks
 * another (Change). WG never asks which way a model radiates: Solve names an
 * axis only when a person chose it (`chosenFrameAxis`), and otherwise the
 * backend decides through the same rule the preselection shows. Nothing here
 * confirms on its own, and a suggestion that disagrees with the project's
 * frame never switches it.
 */
export interface CadFrameView {
  ingestId: string;
  status: 'loading' | 'ready' | 'error';
  /** The server's answer; null while loading, after an error, and for a linked model. */
  frame: SolverFrameState | null;
  linked: boolean;
  /** The axis shown as chosen; null only when the server preselects none (a
   * record from before the frame contract, or an older server): WG decides at Solve. */
  axis: SolverFrameAxis | null;
  /** The user picked `axis` here rather than taking the preselection. */
  picked: boolean;
  /** The axis this card showed before a fresh read found the project's frame
   * changed elsewhere; null when nothing changed under the user. */
  changedFrom: SolverFrameAxis | null;
  error: string | null;
}

interface CadSolverFrameStore {
  frames: Record<string, CadFrameView>;
  /** Bumped when something else on the card (the run line's "Change") asks the
   * frame card to open its axis chooser; the card reads it, never the reverse. */
  changeRequests: Record<string, number>;
  requestChange: (ingestId: string) => void;
  /** The card took the request (opened its chooser); it is not asked again on a remount. */
  consumeChange: (ingestId: string) => void;
  load: (ingestId: string, fetcher?: typeof fetch) => Promise<void>;
  /** The user's pick among the allowed axes (Change), confirmed by Solve. */
  pick: (ingestId: string, axis: SolverFrameAxis) => void;
  /** A fresh server answer (after a confirmation), taken as the new preselection. */
  apply: (ingestId: string, preview: SolverFramePreview) => void;
}

function allows(frame: SolverFrameState, axis: SolverFrameAxis | null | undefined): axis is SolverFrameAxis {
  return Boolean(axis) && frame.axes.some((item) => item.axis === axis && item.allowed);
}

/** The axis a server answer preselects, if the snapshot allows it. */
export function preselectedAxis(frame: SolverFrameState): SolverFrameAxis | null {
  const preselected = frame.preselected?.axis;
  if (allows(frame, preselected)) return preselected;
  // A server before the automatic frame answers no preselection: its
  // confirmed axis, when the snapshot still allows it, is the one on screen.
  if (frame.preselected === undefined && allows(frame, frame.confirmed?.axis)) return frame.confirmed!.axis;
  return null;
}

function viewOf(ingestId: string, preview: SolverFramePreview, held?: CadFrameView): CadFrameView {
  if (preview.linked) {
    return { ingestId, status: 'ready', frame: null, linked: true, axis: null, picked: false, changedFrom: null, error: null };
  }
  // A pick the user made stays theirs while the snapshot allows it.
  const keep = held?.picked && allows(preview, held.axis) ? held.axis : null;
  const axis = keep ?? preselectedAxis(preview);
  // The axis shown moved without the user choosing: say so, never silently.
  const changedFrom = keep === null && held?.axis && held.axis !== axis ? held.axis : held?.changedFrom ?? null;
  return {
    ingestId,
    status: 'ready',
    frame: preview,
    linked: false,
    axis,
    picked: keep !== null,
    changedFrom: changedFrom === axis ? null : changedFrom,
    error: null,
  };
}

const loads = new Map<string, Promise<void>>();

export const useCadSolverFrameStore = create<CadSolverFrameStore>((set, get) => ({
  frames: {},
  changeRequests: {},
  consumeChange: (ingestId) => {
    if (!get().changeRequests[ingestId]) return;
    set({ changeRequests: { ...get().changeRequests, [ingestId]: 0 } });
  },
  requestChange: (ingestId) => set({
    changeRequests: { ...get().changeRequests, [ingestId]: (get().changeRequests[ingestId] ?? 0) + 1 },
  }),
  load: (ingestId, fetcher = fetch) => {
    const running = loads.get(ingestId);
    if (running) return running;
    const held = get().frames[ingestId];
    set({ frames: {
      ...get().frames,
      [ingestId]: held
        ? { ...held, status: held.status === 'error' ? 'loading' : held.status }
        : { ingestId, status: 'loading', frame: null, linked: false, axis: null, picked: false, changedFrom: null, error: null },
    } });
    const request = getSolverFrame({ ingestId }, fetcher)
      .then((preview) => {
        if (!preview.linked && !Array.isArray((preview as Partial<SolverFrameState>).axes)) {
          throw new Error('the server answered no solver frame for this model');
        }
        set({ frames: { ...get().frames, [ingestId]: viewOf(ingestId, preview, get().frames[ingestId]) } });
      })
      .catch((reason: unknown) => {
        const message = reason instanceof Error ? reason.message : String(reason);
        const current = get().frames[ingestId];
        set({ frames: { ...get().frames, [ingestId]: {
          ingestId, status: 'error', frame: current?.frame ?? null, linked: false,
          axis: current?.axis ?? null, picked: current?.picked ?? false, changedFrom: current?.changedFrom ?? null,
          error: message,
        } } });
      })
      .finally(() => { loads.delete(ingestId); });
    loads.set(ingestId, request);
    return request;
  },
  pick: (ingestId, axis) => {
    const held = get().frames[ingestId];
    if (!held?.frame || !allows(held.frame, axis)) return;
    set({ frames: { ...get().frames, [ingestId]: { ...held, axis, picked: true, changedFrom: null } } });
  },
  apply: (ingestId, preview) => {
    if (!preview.linked && !Array.isArray((preview as Partial<SolverFrameState>).axes)) return;
    set({ frames: { ...get().frames, [ingestId]: viewOf(ingestId, preview) } });
  },
}));

/** The read of this ingestion's frame under way, if any: a Solve given right
 * after a preparation (Bring in & solve) waits for it, and judges the frame
 * the card is about to show rather than a read still in flight. */
export function frameReadInFlight(ingestId: string | null | undefined): Promise<void> | null {
  return (ingestId ? loads.get(ingestId) : undefined) ?? null;
}

/** Sources of a preselection that are a person's own choice. */
const CHOSEN_SOURCES: ReadonlySet<string> = new Set(['confirmed', 'carried']);

/**
 * The axis a Solve names for this ingestion's frame: the user's pick (Change),
 * or the project's own confirmed (or carried v1) frame, which the press then
 * holds the solve to. Null for WG's own decision, a linked model, a frame not
 * read, or none shown: the backend then decides (`frame_infer.choose_axis`)
 * and never asks.
 */
export function chosenFrameAxis(view: CadFrameView | undefined): SolverFrameAxis | null {
  if (!view || view.linked || !view.frame || view.status !== 'ready' || !view.axis) return null;
  if (view.picked) return view.axis;
  const source = view.frame.preselected?.source
    // A server before the automatic frame preselects nothing: its confirmed axis is the project's.
    ?? (view.frame.preselected === undefined && view.frame.confirmed?.axis === view.axis ? 'confirmed' : null);
  return source && CHOSEN_SOURCES.has(source) ? view.axis : null;
}

/** Tests only. */
export function resetCadSolverFrameStore(): void {
  loads.clear();
  useCadSolverFrameStore.setState({ frames: {}, changeRequests: {} });
}
