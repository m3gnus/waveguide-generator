import { describe, expect, it } from 'vitest';
import { seriesColorsByLabel } from './seriesColors';

/** The shipped palette width; collision behaviour depends on it. */
const PALETTE = ['#E0673F', '#5D9BD9', '#AD8400', '#00A6AD', '#CA90F3', '#60B374'];

describe('label-keyed series colours', () => {
  it('keeps a run on its colour when another run leaves the chart', () => {
    const both = seriesColorsByLabel(['260308tritonia-q_v02', '260308tritonia-q_v03'], PALETTE, '#000');
    const alone = seriesColorsByLabel(['260308tritonia-q_v03'], PALETTE, '#000');
    expect(alone.get('260308tritonia-q_v03')).toBe(both.get('260308tritonia-q_v03'));
  });

  it('keeps a run on its colour when another run joins ahead of it', () => {
    // Jobs arrive newest-first, so a finished solve is prepended: under
    // positional colours this recoloured every run already on the chart.
    const before = seriesColorsByLabel(['run_v01'], PALETTE, '#000');
    const after = seriesColorsByLabel(['run_v02', 'run_v01'], PALETTE, '#000');
    expect(after.get('run_v01')).toBe(before.get('run_v01'));
  });

  it('does not depend on the order labels with free slots are listed in', () => {
    const forward = seriesColorsByLabel(['run_v01', 'run_v02'], PALETTE, '#000');
    const reversed = seriesColorsByLabel(['run_v02', 'run_v01'], PALETTE, '#000');
    expect(forward.get('run_v01')).toBe(reversed.get('run_v01'));
    expect(forward.get('run_v02')).toBe(reversed.get('run_v02'));
  });

  it('yields a contested slot to whichever label claimed it first, and hands it back', () => {
    // 'alpha' and 'beta' prefer the same slot. Distinctness wins over
    // stability there, because two runs sharing a colour cannot be read as a
    // comparison at all, so the later label probes forward -- and reclaims its
    // preference once the other leaves. This is the one case where a colour
    // still moves, and it is narrower than the positional scheme it replaced,
    // where any change at all moved everything behind it.
    const contested = seriesColorsByLabel(['alpha', 'beta'], PALETTE, '#000');
    expect(contested.get('beta')).not.toBe(contested.get('alpha'));
    expect(seriesColorsByLabel(['beta'], PALETTE, '#000').get('beta')).toBe(contested.get('alpha'));
  });

  it('gives a full palette of labels distinct colours', () => {
    const labels = ['alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta'];
    const colors = seriesColorsByLabel(labels, PALETTE, '#000');
    expect(new Set(colors.values()).size).toBe(PALETTE.length);
    expect([...colors.values()].every((color) => PALETTE.includes(color))).toBe(true);
  });

  it('collapses a repeated label onto one colour', () => {
    const colors = seriesColorsByLabel(['Run A', 'Run A', 'Run B'], PALETTE, '#000');
    expect(colors.size).toBe(2);
    expect(colors.get('Run A')).not.toBe(colors.get('Run B'));
  });

  it('keeps reusing the palette deterministically past its width', () => {
    const labels = Array.from({ length: 9 }, (_, index) => `run_v0${index}`);
    const colors = seriesColorsByLabel(labels, PALETTE, '#000');
    expect(colors.size).toBe(9);
    expect(colors).toEqual(seriesColorsByLabel(labels, PALETTE, '#000'));
  });

  it('falls back rather than emitting an empty colour for an empty palette', () => {
    expect(seriesColorsByLabel(['Run A'], [], '#abc').get('Run A')).toBe('#abc');
  });
});
