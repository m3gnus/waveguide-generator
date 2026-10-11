/**
 * Compatibility observation policy learned from the CAD returns listing.
 * WG always reports `off`: idle file transport needs no routine status reads.
 * The `on` value remains understood for older servers. Until a listing answers,
 * `unknown` behaves as `off`; moving CAD work still enables observations.
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
