export type DecimalInput =
  | { kind: 'number'; value: number }
  | { kind: 'expression'; raw: string }
  | { kind: 'error'; message: string };

interface DecimalInputOptions {
  allowExpression?: boolean;
  /** Lists and comma/whitespace-delimited rows reserve commas for separators. */
  allowCommaDecimal?: boolean;
  /** The ATH SuperFormula control also accepts a complete six-term tuple. */
  tupleSize?: number;
}

const DOT_NUMBER = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;
const COMMA_NUMBER = /^[+-]?(?:\d+,\d*|,\d+)(?:[eE][+-]?\d+)?$/;
const GROUPING_MESSAGE = 'Do not use thousands separators. Write 1234.5.';

/** User entry only: saved designs, cfg files and exports keep their existing syntax. */
export function parseDecimalInput(text: string, options: DecimalInputOptions = {}): DecimalInput {
  const raw = text.trim();
  const number = (normalized: string): DecimalInput => {
    const value = Number(normalized);
    return Number.isFinite(value) ? { kind: 'number', value } : { kind: 'error', message: 'Enter a finite number.' };
  };
  if (DOT_NUMBER.test(raw)) return number(raw);
  if (options.allowCommaDecimal !== false && COMMA_NUMBER.test(raw)) {
    if (/,[0-9]{3}$/.test(raw)) {
      const decimal = String(Number(raw.replace(',', '.')));
      return { kind: 'error', message: `Ambiguous: write ${raw.replace(',', '')} or ${decimal}${decimal.includes('.') ? '' : '.0'}.` };
    }
    return number(raw.replace(',', '.'));
  }
  if (options.allowExpression && options.tupleSize) {
    const parts = raw.split(',');
    if (parts.length === options.tupleSize && parts.every((part) => DOT_NUMBER.test(part.trim()))) {
      return { kind: 'expression', raw };
    }
  }
  // Catch numeric-looking grouping before expression detection. Operators and
  // function calls, including their argument commas, retain their own grammar.
  if (/^[+-]?[\d.,\s]+(?:[eE][+-]?\d+)?$/.test(raw) && /[,\s]/.test(raw)) {
    return { kind: 'error', message: GROUPING_MESSAGE };
  }
  if (raw && options.allowExpression) return { kind: 'expression', raw };
  return { kind: 'error', message: 'Enter a valid number.' };
}

/** Semicolons/tabs make comma decimals unambiguous within each cell. */
export function numericRowCells(line: string): { cells: string[]; allowCommaDecimal: boolean } {
  const delimiter = line.includes(';') ? ';' : line.includes('\t') ? '\t' : line.includes(',') ? ',' : /\s+/;
  return { cells: line.split(delimiter), allowCommaDecimal: delimiter === ';' || delimiter === '\t' };
}

export function parseNumericRow(line: string): DecimalInput[] {
  const { cells, allowCommaDecimal } = numericRowCells(line);
  return cells.map((cell) => parseDecimalInput(cell, { allowCommaDecimal }));
}
