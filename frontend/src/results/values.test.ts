import { describe, expect, it } from 'vitest';
import { complexToDb, polarValueDb } from './values';
import { complexToDb as legacyComplexToDb } from './mappers';

describe('shared polar values', () => {
  it('keeps the existing complex conversion import and reference behavior', () => {
    expect(legacyComplexToDb).toBe(complexToDb);
    expect(complexToDb(3, 4)).toBe(13.979400086720377);
    expect(complexToDb(3, 4, 5)).toBe(0);
    expect(complexToDb(0, 0)).toBeNull();
    expect(complexToDb(Infinity, 0)).toBeNull();
    expect(complexToDb(1, 0, 0)).toBeNull();
  });

  it('only coerces components of complex pairs', () => {
    expect(polarValueDb(['3', '4'])).toBe(13.979400086720377);
    expect(polarValueDb(-0)).toBe(-0);
    [null, undefined, '12', '', ' ', true, false, NaN, Infinity, [0, 0], [1, NaN]]
      .forEach((value) => expect(polarValueDb(value)).toBeNull());
  });
});
