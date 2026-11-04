import { describe, expect, it } from 'vitest';
import { isSafeJson } from './safeJson';

describe('shared safe JSON boundary', () => {
  it('allows finite JSON leaves at depth 40, but no container at depth 40', () => {
    const nest = (leaf: unknown) => Array.from({ length: 40 }).reduce<unknown>((value) => [value], leaf);
    expect(isSafeJson(nest('leaf'))).toBe(true);
    expect(isSafeJson(nest(null))).toBe(true);
    expect(isSafeJson(nest(1))).toBe(true);
    expect(isSafeJson(nest([]))).toBe(false);
    expect(isSafeJson(nest({}))).toBe(false);
  });

  it.each(['__proto__', 'constructor', 'prototype'])('rejects the own key %s at any nesting', (key) => {
    const ownKey = Object.fromEntries([[key, 'unsafe']]);
    expect(isSafeJson(ownKey)).toBe(false);
    expect(isSafeJson({ nested: [ownKey] })).toBe(false);
  });

  it('accepts nested JSON and rejects nonfinite and non-JSON leaves', () => {
    expect(isSafeJson({ nested: [null, 'text', true, false, -0, 1.5, {}] })).toBe(true);
    [NaN, Infinity, -Infinity, undefined, 1n, () => 1, Symbol('key')]
      .forEach((value) => expect(isSafeJson({ nested: [value] })).toBe(false));
  });
});
