/** Shared raw pressure and DSP convention boundary for result reconstruction. */
import { channelWeight, complexMultiply, type Complex } from './crossoverFilters';
import type { CrossoverSpec } from './crossoverSpec';
import type { ResultPayload } from './types';
const TWO_PI = 2 * Math.PI;

export function onAxisComplex(response: ResultPayload['spl_on_axis'] | undefined, count: number): Complex[] | null {
  const spl = response?.spl;
  const phase = response?.phase_degrees;
  if (!spl || !phase || spl.length !== count || phase.length !== count) return null;
  const values: Complex[] = [];
  for (let index = 0; index < count; index += 1) {
    const level = spl[index];
    const degrees = phase[index];
    if (typeof level !== 'number' || !Number.isFinite(level)
      || typeof degrees !== 'number' || !Number.isFinite(degrees)) return null;
    const amplitude = 10 ** (level / 20);
    const radians = (degrees * Math.PI) / 180;
    values.push([amplitude * Math.cos(radians), amplitude * Math.sin(radians)]);
  }
  return values;
}

/**
 * The factor the solver applied to one member's raw `exp(+ikr)` field.
 *
 * The filters, the gain and the delay are defined in the engineering
 * `e^{+jωt}` convention (`server/solver/combine.raw_channel_weights`), and the
 * on-axis phase this payload carries is the raw solver phase, so the
 * engineering weight is conjugated exactly once here — the same single
 * convention boundary the solver has.
 */
export function rawWeight(
  spec: CrossoverSpec,
  member: string,
  gainDb: number,
  delayMs: number,
  inverted: boolean,
  frequencyHz: number,
): Complex {
  const channel = spec.channels[member];
  const band = channelWeight(channel.hp, channel.lp, frequencyHz);
  const gain = 10 ** (gainDb / 20);
  const angle = TWO_PI * frequencyHz * (delayMs / 1_000);
  const sign = inverted ? -1 : 1;
  // conj(band) * gain * exp(+j 2 pi f tau) * sign
  return complexMultiply(
    [band[0] * gain * sign, -band[1] * gain * sign],
    [Math.cos(angle), Math.sin(angle)],
  );
}

