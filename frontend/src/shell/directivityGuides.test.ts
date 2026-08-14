import { describe, expect, it } from 'vitest';
import type { ChartTokens } from '../results/EChart';
import type { ResultPayload } from '../results/types';
import { directivityAngleGuides, heatmapOption } from './ResultsPanel';

const tokens: ChartTokens = {
  foreground: '#d8dde8', muted: '#7f8796', grid: '#1a212d', gridMinor: '#141a24', accent: '#4aa3df',
  series: ['#4aa3df', '#df8b4a'], colormap: ['#0b1d33', '#1a4673', '#2f7ab8', '#59a7d8', '#8fc9e8', '#bfe0f2', '#e2f1fa', '#ffffff'],
};

function payload(): ResultPayload {
  const frequencies = [500, 1_000];
  const angles = Array.from({ length: 37 }, (_, index) => index * 5);
  return {
    frequencies,
    directivity: {
      horizontal: frequencies.map(() => angles.map((angle) => [angle, -(angle / 6)] as [number, number])),
    },
  } as ResultPayload;
}

describe('directivity angular guides', () => {
  it('anchors a user-defined interval to physical zero degrees', () => {
    expect(directivityAngleGuides([-91, 91], 15)).toEqual([-90, -75, -60, -45, -30, -15, 0, 15, 30, 45, 60, 75, 90]);
    expect(directivityAngleGuides([0, 180], 10)).toEqual(Array.from({ length: 17 }, (_, index) => (index + 1) * 10));
  });

  it('adds the selected graticule to the interactive heatmap', () => {
    const option = heatmapOption(payload(), tokens, 'horizontal', -6, 'regular', false, 15) as {
      series: Array<{ name?: string; data?: number[] }>;
    };
    const guides = option.series.find(({ name }) => name === '15° angular guides');
    expect(guides?.data).toEqual([15, 30, 45, 60, 75, 90, 105, 120, 135, 150, 165]);
  });
});
