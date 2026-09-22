/**
 * WG's CAD coordination gate (server/cadlink/coordination.py).
 *
 * The server reads it once at start-up; the page learns it from the CAD
 * returns listing it reads at mount, so no request exists only to ask.
 * Explicit `on` runs the CAD returns listing and the Fusion-status read on
 * their adaptive clocks. `off` stops both unless CAD work is in flight;
 * operation changes still arrive as `cadOperation` pushes on the jobs socket.
 * Until the server answers, `unknown` behaves as `off`, as does a response
 * without a recognized coordination value.
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
  return state !== 'on';
}

/** Tests only. */
export function resetCadCoordinationForTests(value: CadCoordination = 'unknown'): void {
  state = value;
  listeners.forEach((listener) => listener());
}
