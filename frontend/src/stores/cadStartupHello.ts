import type { JobsSnapshot } from '../api/jobsSocket';

/** The jobs socket resyncs its CAD listeners on every hello, the first included.
 * An owner that mounts while the socket is still starting (idle or connecting) reads
 * at mount; that read, while it runs or once it has succeeded, answers the socket's
 * first hello instead of a second read.
 *
 * Accepted window: the server subscribes its event queue just before it says hello,
 * so a CAD notification published between the server answering the mount read and
 * that subscription is not pushed and is not re-read here. It lasts no longer than
 * the first WebSocket handshake; the owners' own pushes, polls and focus wakes pick
 * such a change up later.
 *
 * Every other hello reads again: one after the socket dropped (reconnecting or
 * disconnected) before its first hello, every later hello, and the first one when
 * the mount read failed. A mount read that fails after it answered a hello calls
 * `retry` once. A manager that cannot report its state reads on every hello.
 */
export interface CadStartupHello {
  /** Record the mount read; `retry` runs if it fails after answering a hello. */
  mountRead(read: Promise<unknown>, retry: () => void): void;
  /** Called on each resync; true when this hello must read again. */
  resync(): boolean;
  /** Stop watching the socket state. */
  dispose(): void;
}

type StartupManager = {
  getSnapshot?(): Pick<JobsSnapshot, 'connection'>;
  subscribe?(listener: () => void): () => void;
};

export function cadStartupHello(manager: StartupManager): CadStartupHello {
  const starting = () => {
    const connection = manager.getSnapshot?.().connection;
    return connection === 'idle' || connection === 'connecting';
  };
  let awaitingFirstHello = starting();
  let covered = false;
  let answered = false;
  let owedRetry: (() => void) | null = null;
  // A socket that drops before its first hello reconnects after an outage that
  // the mount read cannot cover.
  let unsubscribe = awaitingFirstHello && manager.subscribe
    ? manager.subscribe(() => { if (!starting()) stopWatching(); })
    : null;
  function stopWatching() {
    if (manager.getSnapshot?.().connection !== 'connected') awaitingFirstHello = false;
    unsubscribe?.(); unsubscribe = null;
  }
  return {
    mountRead(read, retry) {
      covered = true;
      owedRetry = retry;
      read.then(undefined, () => {
        covered = false;
        if (answered) { answered = false; owedRetry?.(); }
        owedRetry = null;
      });
    },
    resync() {
      const answers = awaitingFirstHello && covered;
      awaitingFirstHello = false;
      unsubscribe?.(); unsubscribe = null;
      if (answers) answered = true;
      return !answers;
    },
    dispose() { unsubscribe?.(); unsubscribe = null; owedRetry = null; },
  };
}
