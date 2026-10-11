import { create } from 'zustand';
import type { FusionCadLink, FusionCadStatus } from '../api/cadlink';
import { agedFusionStatus, statusExpiresAt } from '../shell/cadWorkflowView';

/** Additive schema-1 display metadata; it never participates in link identity. */
export type CadConnectionLink = FusionCadLink & { linkName?: string | null };
export type CadConnectionStatus = Omit<FusionCadStatus, 'link' | 'matchingLinks'> & {
  link: CadConnectionLink | null;
  matchingLinks?: CadConnectionLink[];
};

interface CadConnectionState {
  fusionStatus: CadConnectionStatus | null;
  receivedAt: number | null;
  expiresAt: number | null;
  linkName: string | null;
  recoveryError: string | null;
}

const VALID_STATES = new Set(['closed', 'addin_offline', 'addin_outdated', 'no_document',
  'not_linked', 'instance_selection_required', 'current', 'stale']);

/** One observation owner, with no network or coordinator dependency. A caller must
 * use the returned read, never a previously held status, to guard a command. */
export function createCadConnectionStore(now: () => number = Date.now) {
  const store = create<CadConnectionState>(() => ({
    fusionStatus: null, receivedAt: null, expiresAt: null, linkName: null, recoveryError: null,
  }));
  let generation = 0;
  let expiryTimer: ReturnType<typeof setTimeout> | null = null;
  const clearExpiry = () => {
    if (expiryTimer !== null) clearTimeout(expiryTimer);
    expiryTimer = null;
  };
  const publish = (status: CadConnectionStatus) => {
    clearExpiry();
    const expiresAt = statusExpiresAt(status);
    const held = expiresAt !== null && expiresAt <= now() ? agedFusionStatus(status) : status;
    store.setState({
      fusionStatus: held, receivedAt: now(), expiresAt,
      linkName: (held.link as CadConnectionLink | null)?.linkName ?? null,
    });
    if (expiresAt !== null && expiresAt > now()) {
      expiryTimer = setTimeout(() => {
        expiryTimer = null;
        if (store.getState().fusionStatus === held) {
          store.setState({ fusionStatus: agedFusionStatus(held) });
        }
      }, expiresAt - now());
    }
    return held;
  };
  return Object.assign(store, {
    async read(reader: () => Promise<CadConnectionStatus>): Promise<CadConnectionStatus | null> {
      const started = ++generation;
      try {
        const status = await reader();
        // Refuse the answer as well as its publication: an older read must not
        // drive an action while a newer read is pending, even if it later fails.
        if (started !== generation || !VALID_STATES.has(status.state)) return null;
        return publish(status);
      } catch {
        // Presence is advisory. Keep the last report and let its own TTL age it.
        return null;
      }
    },
    invalidate() {
      generation += 1;
      clearExpiry();
      store.setState({ fusionStatus: null, receivedAt: null, expiresAt: null, linkName: null });
    },
    setRecoveryError(recoveryError: string | null) { store.setState({ recoveryError }); },
  });
}

export const useCadConnectionStore = createCadConnectionStore();
export type CadConnectionStore = ReturnType<typeof createCadConnectionStore>;
