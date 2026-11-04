import { describe, expect, it } from 'vitest';
import { fixedDecimal } from './exportNumbers';

describe('ordinary decimal export numbers', () => {
  it.each([
    [1e21, 4, '1000000000000000000000.0000'],
    [-1e21, 4, '-1000000000000000000000.0000'],
    [1.23456789e25, 6, '12345680000000000000000000.000000'],
    [-1.23456789e25, 6, '-12345680000000000000000000.000000'],
    [1.23445, 4, '1.2345'],
    [1.23455, 4, '1.2346'],
    [-0, 4, '0.0000'],
    [-0.00001, 4, '-0.0000'],
  ] as const)('formats %s at precision %s', (value, precision, expected) => {
    expect(fixedDecimal(value, precision)).toBe(expected);
  });
});
