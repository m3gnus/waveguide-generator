import type { JobsSnapshot } from '../api/jobsSocket';

/** The jobs socket resyncs its CAD listeners on every hello, the first included.
 * An owner that mounts while the socket is still starting reads at mount; that read,
 * while it runs or once it has succeeded, already answers the socket's first hello.
 * Every later hello is a reconnect and reads again, and so does the first one when
 * the mount read failed or the socket was already connected (or reconnecting) at mount.
 */
export interface CadStartupHello {
  /** Record the mount read. */
  mountRead(read: Promise<unknown>): void;
  /** Called on each resync; true when this hello must read again. */
  resync(): boolean;
}

export function cadStartupHello(manager: { getSnapshot?(): Pick<JobsSnapshot, 'connection'> }): CadStartupHello {
  const connection = manager.getSnapshot?.().connection ?? 'idle';
  let awaitingFirstHello = connection === 'idle' || connection === 'connecting';
  let covered = false;
  return {
    mountRead(read) {
      covered = true;
      read.then(undefined, () => { covered = false; });
    },
    resync() {
      const answered = awaitingFirstHello && covered;
      awaitingFirstHello = false;
      return !answered;
    },
  };
}
