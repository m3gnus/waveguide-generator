import { reportSolveCommandOutcome } from '../api/cadlink';
import { useCadReturnStore } from './cadReturn';

/**
 * A CAD-authored "solve this in WG" request WG has taken responsibility for.
 *
 * The marker on disk is retired only by a terminal ledger entry, so a request
 * that stops at a readiness gate has to be held here rather than dropped:
 * dropping it is what let a gated command replay on the next page load and
 * duplicate a solve the user had already run by hand.
 */
export interface ParkedSolveCommand {
  commandId: string;
  bundlePath: string;
  /** Empty while the automatic attempt is still in flight. A non-empty list is
   * the parked state the CAD Link panel renders. */
  blockers: string[];
  parkedAt: string;
}

export interface ParkedSolveCommandState { command: ParkedSolveCommand | null }

type Listener = () => void;

/** Outside React because both solve entry points and the coordinator's poll
 * touch it, and none of them may depend on the panel being mounted. */
class ParkedSolveCommandStore {
  private value: ParkedSolveCommandState = { command: null };
  private listeners = new Set<Listener>();

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): ParkedSolveCommandState => this.value;

  park(command: ParkedSolveCommand): void {
    this.publish({ command: { ...command, blockers: [...command.blockers] } });
  }

  /** Record why the parked command cannot run yet. Ignored once the command it
   * describes has been consumed, so a late refusal cannot resurrect a banner. */
  setBlockers(commandId: string, blockers: string[]): void {
    const current = this.value.command;
    if (!current || current.commandId !== commandId) return;
    this.publish({ command: { ...current, blockers: [...blockers] } });
  }

  clear(): void {
    if (this.value.command === null) return;
    this.publish({ command: null });
  }

  private publish(value: ParkedSolveCommandState): void {
    this.value = value;
    this.listeners.forEach((listener) => listener());
  }
}

export const parkedSolveCommandStore = new ParkedSolveCommandStore();

/**
 * Retire a parked command against a job that was just submitted for it.
 *
 * Every solve of an imported return passes through here, so pressing Solve by
 * hand after acknowledging the findings consumes the same request Fusion
 * asked for instead of leaving it to replay. The job id travels with the
 * ledger entry so a later replay can name the run that already exists.
 */
export async function consumeParkedSolveCommand(
  jobId: string | null,
  fetcher: typeof fetch = fetch,
): Promise<void> {
  const parked = parkedSolveCommandStore.getSnapshot().command;
  if (!parked) return;
  // Only the return the command named: solving some other bundle is not the
  // work Fusion asked for and must not spend its request.
  if (useCadReturnStore.getState().selectedBundle?.bundlePath !== parked.bundlePath) return;
  parkedSolveCommandStore.clear();
  await reportSolveCommandOutcome(
    { commandId: parked.commandId, state: 'accepted', jobId, reason: null },
    fetcher,
  ).catch(() => undefined);
}

/** Refuse a parked command for good: the marker is deleted and the ledger says
 * why, so nothing re-offers it. Used by the panel's Dismiss and by outcomes
 * that can never succeed on this machine. */
export async function refuseParkedSolveCommand(
  reason: string,
  fetcher: typeof fetch = fetch,
): Promise<void> {
  const parked = parkedSolveCommandStore.getSnapshot().command;
  if (!parked) return;
  parkedSolveCommandStore.clear();
  await reportSolveCommandOutcome(
    { commandId: parked.commandId, state: 'refused', jobId: null, reason },
    fetcher,
  ).catch(() => undefined);
}
