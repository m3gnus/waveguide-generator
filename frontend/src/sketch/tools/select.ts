export const select = (selection: string[], id: string, extend = false): string[] => extend
  ? selection.includes(id) ? selection.filter(v => v !== id) : [...selection, id] : [id];

/** One pending target per animation frame. A flush on release preserves the final target. */
export function coalesceTargets<T>(schedule: (callback: () => void) => number, cancel: (id: number) => void, consume: (target: T) => void) {
  let frame: number | undefined, latest: T | undefined;
  const flush = () => {
    if (frame !== undefined) cancel(frame);
    frame = undefined;
    if (latest !== undefined) { const target = latest; latest = undefined; consume(target); }
  };
  return {
    push(target: T) { latest = target; frame ??= schedule(flush); },
    flush,
    cancel() { if (frame !== undefined) cancel(frame); frame = undefined; latest = undefined; },
  };
}
