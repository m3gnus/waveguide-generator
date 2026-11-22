import { useCallback, useEffect, useState } from 'react';

/**
 * Hold a value back until it has stopped changing for `delay` milliseconds.
 * `flush` adopts the current value at once, for an action that must not act
 * on a value the user has already typed past.
 */
export function useFlushableSettled<T>(value: T, delay: number): [T, () => void] {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  const flush = useCallback(() => setSettled(value), [value]);
  return [settled, flush];
}

/** Hold a value back until it has stopped changing for `delay` milliseconds. */
export function useSettled<T>(value: T, delay: number): T {
  return useFlushableSettled(value, delay)[0];
}
