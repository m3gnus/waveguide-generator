/**
 * WG's CAD coordination gate (server/cadlink/coordination.py).
 *
 * The server reads it once at start-up; the page learns it from the CAD
 * returns listing it reads at mount, so no request exists only to ask.
 * `on` is today's behaviour: the CAD returns listing and the Fusion-status
 * read run on their adaptive clocks. `off` stops both unless CAD work is in
 * flight; operation changes still arrive as `cadOperation` pushes on the jobs
 * socket. Until the server answers the state is `unknown`, which behaves as
 * `on`, and so does a server too old to answer: an unanswered question never
 * silences a poll the user may depend on.
 */
export type CadCoordination = 'unknown' | 'on' | 'off';

let state: CadCoordination = 'unknown';
const listeners = new Set<() => void>();

export const cadCoordinationStore = {
  getSnapshot: (): CadCoordination => state,
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  set(next: CadCoordination): void {
    if (next === state) return;
    state = next;
    listeners.forEach((listener) => listener());
  },
};

export function cadCoordinationOff(): boolean {
  return state === 'off';
}

/** Tests only. */
export function resetCadCoordinationForTests(value: CadCoordination = 'unknown'): void {
  state = value;
  listeners.forEach((listener) => listener());
}
