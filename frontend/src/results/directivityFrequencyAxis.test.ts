import { describe, expect, it } from 'vitest';
import type { ChartTokens } from './EChart';
import type { ResultPayload } from './types';
import { directivityFrequencyTickLabels, logFrequencyTicks, preferredDirectivityFrequencyTicks } from './directivityFrequencyAxis';
import { heatmapOption, interpolateDirectivityGrid, lineOption } from '../shell/ResultsPanel';

const tokens: ChartTokens = {
  foreground: '#eee', muted: '#aaa', grid: '#444', gridMinor: '#333', accent: '#e60',
  series: ['#e60', '#59d'], colormap: ['#000', '#fff'],
};

function payload(): ResultPayload {
  const frequencies = Array.from({ length: 40 }, (_, index) => 400 * (40 ** (index / 39)));
  const angles = Array.from({ length: 37 }, (_, index) => index * 5);
  return {
    frequencies,
    directivity: { horizontal: frequencies.map(() => angles.map((angle) => [angle, -angle / 6])) },
  } as ResultPayload;
}

describe('directivity frequency axis', () => {
  it('restores WG v1 clean logarithmic frequency ticks', () => {
    expect(preferredDirectivityFrequencyTicks(100, 10_000)).toEqual([
      100, 200, 300, 400, 500, 600, 700, 800, 900, 1_000,
      2_000, 3_000, 4_000, 5_000, 6_000, 7_000, 8_000, 9_000, 10_000,
    ]);
    expect(preferredDirectivityFrequencyTicks(350, 4_500)).toEqual([
      400, 500, 600, 700, 800, 900, 1_000, 2_000, 3_000, 4_000,
    ]);

    const dense = interpolateDirectivityGrid(payload(), 'horizontal');
    expect([...directivityFrequencyTickLabels(dense.frequencies).values()]).toEqual([
      '400', '500', '600', '700', '800', '900', '1k', '2k', '3k', '4k', '5k', '6k', '7k', '8k', '9k', '10k',
    ]);
  });

  it('uses only clean WG v1 labels and guides on the interactive heatmap', () => {
    const option = heatmapOption(payload(), tokens, 'horizontal', -6) as {
      xAxis: {
        data: string[];
        axisLabel: { interval: (index: number) => boolean; formatter: (value: string, index: number) => string };
        splitLine: { show: boolean; interval: (index: number) => boolean };
      };
    };
    const visible = option.xAxis.data.flatMap((value, index) => option.xAxis.axisLabel.interval(index)
      ? [option.xAxis.axisLabel.formatter(value, index)]
      : []);
    // A regular card labels 1-2-5 per decade: 1k and 10k are always there.
    expect(visible).toEqual(['500', '1k', '2k', '5k', '10k']);
    // Every WG v1 graticule tick keeps its guide line.
    expect(option.xAxis.splitLine.show).toBe(true);
    const guides = option.xAxis.data.flatMap((value, index) => option.xAxis.splitLine.interval(index) ? [option.xAxis.axisLabel.formatter(value, index)] : []);
    expect(guides).toEqual(['400', '500', '600', '700', '800', '900', '1k', '2k', '3k', '4k', '5k', '6k', '7k', '8k', '9k', '10k']);
  });

  it('labels 300 and 3k as well in the detail view', () => {
    const option = heatmapOption(payload(), tokens, 'horizontal', -6, 'full') as unknown as {
      xAxis: { data: string[]; axisLabel: { interval: (index: number) => boolean; formatter: (value: string, index: number) => string } };
    };
    const visible = option.xAxis.data.flatMap((value, index) => option.xAxis.axisLabel.interval(index) ? [option.xAxis.axisLabel.formatter(value, index)] : []);
    expect(visible).toEqual(['500', '1k', '2k', '3k', '5k', '10k']);
  });

  it('labels line charts at round frequencies, in the same format as the map', () => {
    expect(logFrequencyTicks(20, 20_000, 'compact')).toEqual([20, 50, 100, 200, 500, 1_000, 2_000, 5_000, 10_000, 20_000]);
    expect(logFrequencyTicks(100, 20_000, 'full')).toEqual([100, 200, 300, 500, 1_000, 2_000, 3_000, 5_000, 10_000, 20_000]);
    // A sweep bound a rounding error inside 20 kHz still gets its 20k.
    expect(logFrequencyTicks(100, 19_999.99999999, 'regular').at(-1)).toBe(20_000);
    const option = lineOption([{ type: 'line', data: [[100, 1], [20_000, 2]] }], tokens, 'dB', 'regular') as unknown as {
      xAxis: { axisLabel: { customValues: number[]; formatter: (value: number) => string }; axisTick: { customValues: number[] } };
    };
    expect(option.xAxis.axisLabel.customValues).toEqual([100, 200, 500, 1_000, 2_000, 5_000, 10_000, 20_000]);
    expect(option.xAxis.axisTick.customValues).toEqual(option.xAxis.axisLabel.customValues);
    expect(option.xAxis.axisLabel.customValues.map(option.xAxis.axisLabel.formatter)).toEqual(['100', '200', '500', '1k', '2k', '5k', '10k', '20k']);
  });
});
