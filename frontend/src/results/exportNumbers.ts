/** Locale-independent ordinary decimals, including Number#toFixed's exponential range. */
export function fixedDecimal(value: number, precision: number): string {
  const formatted = value.toFixed(precision);
  if (!/[eE]/.test(formatted)) return formatted;

  // Number#toFixed delegates to exponential notation at |value| >= 1e21.
  // Export readers expect ordinary decimals, so expand that rare case explicitly.
  const [coefficient, exponentText] = value.toExponential(precision).split('e');
  const sign = coefficient.startsWith('-') ? '-' : '';
  const digits = coefficient.replace('-', '').replace('.', '');
  const exponent = Number(exponentText);
  return `${sign}${digits}${'0'.repeat(Math.max(0, exponent - precision))}.${'0'.repeat(precision)}`;
}
