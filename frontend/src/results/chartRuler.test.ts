import { describe, expect, it } from 'vitest';
import type { EChartsOption } from 'echarts';
import { axisUnit, chartGutter, inAxisGutter, rulerDecimals, rulerLabel } from './chartRuler';

const LINE: EChartsOption = {
  grid: { left: 40, right: 10, top: 20, bottom: 22 },
  yAxis: { type: 'value', name: 'Directivity index [dB]' },
};

describe('axis hover ruler geometry', () => {
  it('reads the plot edges from the option\'s pixel insets', () => {
    expect(chartGutter(LINE, 400, 200)).toEqual({ left: 40, right: 390, top: 20, bottom: 178 });
  });

  it('declines charts with no cartesian grid, so the polar response is untouched', () => {
    expect(chartGutter({ polar: { radius: '72%' } } as EChartsOption, 400, 200)).toBeNull();
  });

  it('declines percentage insets rather than treating them as pixels', () => {
    expect(chartGutter({ grid: { left: '12%', right: 10, top: 20, bottom: 22 } } as EChartsOption, 400, 200)).toBeNull();
  });

  it('declines a card too small to hold its own insets', () => {
    expect(chartGutter(LINE, 400, 30)).toBeNull();
    expect(chartGutter(LINE, 40, 200)).toBeNull();
  });

  it('arms only over the left gutter, never over the plot or outside it', () => {
    const gutter = chartGutter(LINE, 400, 200)!;
    expect(inAxisGutter(gutter, 12, 100)).toBe(true);
    expect(inAxisGutter(gutter, 40, 100)).toBe(true);
    expect(inAxisGutter(gutter, 41, 100)).toBe(false);
    expect(inAxisGutter(gutter, 12, 10)).toBe(false);
    expect(inAxisGutter(gutter, 12, 190)).toBe(false);
  });
});

describe('axis hover ruler readout', () => {
  const gutter = chartGutter(LINE, 400, 200)!;
  /** A linear axis from `top` dB at the plot top to `bottom` dB at its floor. */
  const linear = (top: number, bottom: number) => (pixel: number) =>
    top + (bottom - top) * (pixel - gutter.top) / (gutter.bottom - gutter.top);

  it('states the level under the pointer with the axis unit', () => {
    expect(rulerLabel(LINE, gutter, 99, linear(20, -20))).toBe('0.0 dB');
  });

  it('resolves finely enough that a one-pixel move changes the number', () => {
    const convert = linear(20, -20);
    expect(rulerLabel(LINE, gutter, 99, convert)).not.toBe(rulerLabel(LINE, gutter, 100, convert));
  });

  it('drops decimals a coarse axis cannot support', () => {
    expect(rulerDecimals(40, 158)).toBe(1);
    expect(rulerDecimals(5, 158)).toBe(2);
    expect(rulerDecimals(360, 158)).toBe(0);
    expect(rulerDecimals(4000, 158)).toBe(0);
    // A degenerate extent must not ask toFixed for a negative or absurd count.
    expect(rulerDecimals(0, 158)).toBe(1);
    expect(rulerDecimals(1e-9, 158)).toBe(3);
  });

  it('reads a category axis out of its data, since the pixel maps to an ordinal', () => {
    const map: EChartsOption = {
      grid: { left: 34, right: 32, top: 20, bottom: 22 },
      yAxis: { type: 'category', name: 'Angle [°]', data: ['-90', '-45', '0', '45', '90'] },
    };
    const mapGutter = chartGutter(map, 400, 200)!;
    expect(rulerLabel(map, mapGutter, 100, () => 2.4)).toBe('0 °');
    expect(rulerLabel(map, mapGutter, 100, () => 9)).toBeNull();
  });

  it('states the bare number when the density dropped the axis name', () => {
    const bare: EChartsOption = { grid: { left: 30, right: 8, top: 16, bottom: 17 }, yAxis: { type: 'value' } };
    expect(rulerLabel(bare, chartGutter(bare, 400, 200)!, 100, () => 6)).toBe('6.0');
  });

  it('says nothing when the pixel is off the axis', () => {
    expect(rulerLabel(LINE, gutter, 100, () => null)).toBeNull();
    expect(rulerLabel(LINE, gutter, 100, () => Number.NaN)).toBeNull();
  });

  it('takes the unit from a bracketed axis name only', () => {
    expect(axisUnit('Group delay [ms]')).toBe('ms');
    expect(axisUnit('Frequency')).toBe('');
    expect(axisUnit(undefined)).toBe('');
  });
});
