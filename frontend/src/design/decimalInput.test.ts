import { expect, it } from 'vitest';
import { parseDecimalInput } from './decimalInput';

it.each([
  ['12,5', 12.5], ['-0,75', -.75], [',5', .5], ['+12,5', 12.5],
  ['12.5', 12.5], ['-.75', -.75], ['.5', .5], ['20', 20],
  [' 12,5 ', 12.5], ['1,2345', 1.2345], ['1.234', 1.234],
  ['1e3', 1000], ['1,25e-2', .0125], ['1,234e2', 123.4],
])('accepts %s as %s', (text, value) => {
  expect(parseDecimalInput(text as string)).toEqual({ kind: 'number', value });
});

it.each(['1,000', '20,000', '-3,500', '0,125', ',500'])('refuses ambiguous %s', (text) => {
  expect(parseDecimalInput(text, { allowExpression: true })).toMatchObject({ kind: 'error', message: expect.stringContaining('Ambiguous: write') });
});

it.each(['1,234.5', '1.234,5', '1 234', '1\u00a0234', '1,234,567'])('refuses grouping %s', (text) => {
  expect(parseDecimalInput(text, { allowExpression: true })).toEqual({ kind: 'error', message: 'Do not use thousands separators. Write 1234.5.' });
});

it.each(['sf(4,12,1,1,1,1)', '12.5 + 2*p', '2+3', '48.5 - 7*cos(2*p)^5', '(1,2)'])('preserves expression %s', (raw) => {
  expect(parseDecimalInput(raw, { allowExpression: true })).toEqual({ kind: 'expression', raw });
});

it.each(['', ' ', 'Infinity', 'NaN', '0x10', '1e999'])('refuses invalid plain number %s', (text) => {
  expect(parseDecimalInput(text).kind).toBe('error');
});

it('reserves commas for lists and permits only the explicit SuperFormula tuple exception', () => {
  expect(parseDecimalInput('12,5', { allowCommaDecimal: false }).kind).toBe('error');
  const raw = '1,1,4,2,3,4';
  expect(parseDecimalInput(raw, { allowExpression: true }).kind).toBe('error');
  expect(parseDecimalInput(raw, { allowExpression: true, tupleSize: 6 })).toEqual({ kind: 'expression', raw });
  expect(parseDecimalInput('12,5', { allowExpression: true, tupleSize: 6 })).toEqual({ kind: 'number', value: 12.5 });
});
