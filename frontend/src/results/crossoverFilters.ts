import { familyOrders, type FilterFamily, type FilterSection } from './crossoverSpec';

/**
 * The crossover filter families, evaluated in the browser.
 *
 * A port of `server/solver/filters.py`, in the same engineering `e^{+jωt}`
 * convention with `s = j f / fc`. It exists for one reason: the reverse-null
 * overlay has to weight the member responses exactly as the solver did, and
 * the result payload states the filters rather than the weights. Every family
 * here is checked against the server's own output in `crossoverFilters.test.ts`
 * — if the two ever drift, the overlay would draw a null that no solve
 * produced, which is worse than drawing nothing.
 */

export type Complex = readonly [number, number];

export function complexMultiply(left: Complex, right: Complex): Complex {
  return [
    left[0] * right[0] - left[1] * right[1],
    left[0] * right[1] + left[1] * right[0],
  ];
}

export function complexDivide(left: Complex, right: Complex): Complex {
  const denominator = right[0] * right[0] + right[1] * right[1];
  if (denominator === 0) return [0, 0];
  return [
    (left[0] * right[0] + left[1] * right[1]) / denominator,
    (left[1] * right[0] - left[0] * right[1]) / denominator,
  ];
}

export function complexAbs(value: Complex): number {
  return Math.hypot(value[0], value[1]);
}

/** Polynomial with the given complex roots, highest power first, real part
 * only — every root set here is closed under conjugation. */
function polyFromRoots(roots: Complex[]): number[] {
  let coefficients: Complex[] = [[1, 0]];
  for (const root of roots) {
    const next: Complex[] = [...coefficients, [0, 0]];
    for (let index = 0; index < coefficients.length; index += 1) {
      const shifted = complexMultiply(coefficients[index], root);
      next[index + 1] = [next[index + 1][0] - shifted[0], next[index + 1][1] - shifted[1]];
    }
    coefficients = next;
  }
  return coefficients.map(([real]) => real);
}

function convolve(left: number[], right: number[]): number[] {
  const out = new Array<number>(left.length + right.length - 1).fill(0);
  left.forEach((a, i) => right.forEach((b, j) => { out[i + j] += a * b; }));
  return out;
}

/** Butterworth denominator: poles `exp(jπ(2k+n−1)/(2n))`, k = 1..n. */
function butterworthDenominator(order: number): number[] {
  const roots: Complex[] = [];
  for (let k = 1; k <= order; k += 1) {
    const angle = (Math.PI * (2 * k + order - 1)) / (2 * order);
    roots.push([Math.cos(angle), Math.sin(angle)]);
  }
  return polyFromRoots(roots);
}

/** Reverse Bessel polynomial coefficients, ascending power. */
function besselAscending(order: number): number[] {
  const factorial = (value: number): number => (value <= 1 ? 1 : value * factorial(value - 1));
  const coefficients: number[] = [];
  for (let k = 0; k <= order; k += 1) {
    coefficients.push(
      factorial(2 * order - k) / (2 ** (order - k) * factorial(k) * factorial(order - k)),
    );
  }
  return coefficients;
}

function evaluateAscending(coefficients: number[], s: Complex): Complex {
  let power: Complex = [1, 0];
  let total: Complex = [0, 0];
  for (const coefficient of coefficients) {
    total = [total[0] + coefficient * power[0], total[1] + coefficient * power[1]];
    power = complexMultiply(power, s);
  }
  return total;
}

/**
 * The frequency scale that puts the Bessel low-pass at −3 dB at fc.
 *
 * `scipy.signal.bessel(..., norm="mag")` on the server; here the same
 * condition is solved directly, which is exact to bisection precision and
 * avoids shipping a table of coefficients that could go stale.
 */
function besselMagnitudeScale(order: number): number {
  const ascending = besselAscending(order);
  const magnitude = (omega: number): number => complexAbs(
    complexDivide([ascending[0], 0], evaluateAscending(ascending, [0, omega])),
  );
  let low = 1e-6;
  let high = 100;
  const target = Math.SQRT1_2;
  for (let step = 0; step < 200; step += 1) {
    const mid = (low + high) / 2;
    if (magnitude(mid) > target) low = mid; else high = mid;
  }
  return (low + high) / 2;
}

const besselCache = new Map<number, number[]>();

/** Bessel denominator, ascending power, already magnitude-normalised. */
function besselDenominatorAscending(order: number): number[] {
  const cached = besselCache.get(order);
  if (cached) return cached;
  const ascending = besselAscending(order);
  const scale = besselMagnitudeScale(order);
  const scaled = ascending.map((coefficient, power) => coefficient * scale ** power);
  besselCache.set(order, scaled);
  return scaled;
}

/** The prototype low-pass response at the normalised variable `s`. */
function prototype(family: FilterFamily, order: number, s: Complex): Complex {
  if (family === 'bessel') {
    const ascending = besselDenominatorAscending(order);
    return complexDivide([ascending[0], 0], evaluateAscending(ascending, s));
  }
  // Linkwitz-Riley of order n is the Butterworth of n/2 squared; a linear
  // phase section is its magnitude with the phase discarded, which the caller
  // applies after evaluating.
  const half = family === 'butterworth' ? order : order / 2;
  const denominator = butterworthDenominator(half);
  const squared = family === 'butterworth' ? denominator : convolve(denominator, denominator);
  const ascending = [...squared].reverse();
  return complexDivide([1, 0], evaluateAscending(ascending, s));
}

export function isSupportedSection(section: { family: FilterFamily; order: number }): boolean {
  return familyOrders(section.family).includes(section.order);
}

/** One section's engineering response at `frequencyHz`. */
export function sectionResponse(section: FilterSection, kind: 'hp' | 'lp', frequencyHz: number): Complex {
  const normalized = frequencyHz / section.fcHz;
  if (kind === 'hp' && normalized === 0) return [0, 0];
  // A high-pass is the family's low-pass at 1/s.
  const s: Complex = kind === 'lp' ? [0, normalized] : [0, -1 / normalized];
  const response = prototype(section.family, section.order, s);
  return section.family === 'linear_phase' ? [complexAbs(response), 0] : response;
}

/** A channel's band-limiting weight: its high-pass times its low-pass. */
export function channelWeight(
  hp: FilterSection | null,
  lp: FilterSection | null,
  frequencyHz: number,
): Complex {
  let weight: Complex = [1, 0];
  if (hp) weight = complexMultiply(weight, sectionResponse(hp, 'hp', frequencyHz));
  if (lp) weight = complexMultiply(weight, sectionResponse(lp, 'lp', frequencyHz));
  return weight;
}
