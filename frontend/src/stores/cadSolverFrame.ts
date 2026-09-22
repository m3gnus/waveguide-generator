import { create } from 'zustand';
import {
  confirmSolverFrame,
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
 * project's confirmed frame, a v1 confirmation carried forward, or the
 * automatic suggestion -- until the user picks another. Solve confirms exactly
 * that visible axis (`confirmDisplayedFrame`); nothing here confirms on its own,
 * and a suggestion that disagrees with the project's frame never switches it.
 */
export interface CadFrameView {
  ingestId: string;
  status: 'loading' | 'ready' | 'error';
  /** The server's answer; null while loading, after an error, and for a linked model. */
  frame: SolverFrameState | null;
  linked: boolean;
  /** The axis Solve confirms, or null while the user still has to choose one. */
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
  load: (ingestId: string, fetcher?: typeof fetch) => Promise<void>;
  /** The user's pick among the allowed axes, shown and confirmed by Solve. */
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

/** Why Solve cannot confirm the frame shown for this ingestion, or null. */
export function frameSolveBlocker(ingestId: string | null | undefined): string | null {
  if (!ingestId) return null;
  const view = useCadSolverFrameStore.getState().frames[ingestId];
  if (!view || view.linked) return null;
  if (view.status === 'loading' && !view.frame) return 'Reading which way this model radiates…';
  if (view.status === 'ready' && view.frame && view.axis === null) {
    return 'Choose which way this model radiates, in the CAD Link panel, then press Solve.';
  }
  return null;
}

/**
 * Confirm the axis the Solve card shows for this ingestion, as part of Solve,
 * and answer it: the preparation is then held to exactly that axis
 * (`frameAxis` on prepare), so a project confirmation changed elsewhere after
 * this card last read it stops the solve at the frame gate instead of
 * changing the axis solved.
 *
 * Only a frame the card has on screen is confirmed: with nothing read (the
 * card never showed one) or a read that failed, nothing is sent, and the
 * backend's own frame gate still stops the solve and asks. An axis already
 * confirmed for the project is not sent again. Whether the confirmation agrees
 * with the automatic suggestion is the server's to record.
 */
export async function confirmDisplayedFrame(
  ingestId: string,
  fetcher: typeof fetch = fetch,
): Promise<SolverFrameAxis | null> {
  const view = useCadSolverFrameStore.getState().frames[ingestId];
  if (!view || view.linked || !view.frame || view.status !== 'ready') return null;
  const blocker = frameSolveBlocker(ingestId);
  if (blocker) throw new Error(blocker);
  const axis = view.axis!;
  if (view.frame.confirmed?.axis === axis) return axis;
  const answer = await confirmSolverFrame({ ingestId }, axis, fetcher);
  useCadSolverFrameStore.getState().apply(ingestId, answer);
  return axis;
}

/** Tests only. */
export function resetCadSolverFrameStore(): void {
  loads.clear();
  useCadSolverFrameStore.setState({ frames: {} });
}
