import { describe, expect, it } from 'vitest';
import { runOfLabel, seriesColorsByLabel, stableColorKey } from './seriesColors';

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

describe('colours that survive a re-solve', () => {
  it('keeps a design on its colour when it is solved again unchanged', () => {
    // The reported glitch: run numbers are consecutive per created job, so
    // pressing Solve twice with nothing changed used to rehash the label and
    // hand the same curve a different colour.
    const before = seriesColorsByLabel(['#41 · tritonia-q'], PALETTE, '#000');
    const after = seriesColorsByLabel(['#42 · tritonia-q'], PALETTE, '#000');
    expect(after.get('#42 · tritonia-q')).toBe(before.get('#41 · tritonia-q'));
  });

  it('still separates two runs of the same design being compared', () => {
    const colors = seriesColorsByLabel(['#41 · tritonia-q', '#42 · tritonia-q'], PALETTE, '#000');
    expect(colors.get('#42 · tritonia-q')).not.toBe(colors.get('#41 · tritonia-q'));
  });

  it('keeps the angles of one run distinct from each other', () => {
    const labels = ['#41 · tritonia-q · On-axis', '#41 · tritonia-q · 30°', '#41 · tritonia-q · 60°'];
    const colors = seriesColorsByLabel(labels, PALETTE, '#000');
    expect(new Set(colors.values()).size).toBe(labels.length);
  });

  it('moves an angled trace with its run, not with the run number', () => {
    const before = seriesColorsByLabel(['#41 · tritonia-q · 30°'], PALETTE, '#000');
    const after = seriesColorsByLabel(['#42 · tritonia-q · 30°'], PALETTE, '#000');
    expect(after.get('#42 · tritonia-q · 30°')).toBe(before.get('#41 · tritonia-q · 30°'));
  });

  it('leaves a label that carries no run number alone', () => {
    expect(stableColorKey('Throat')).toBe('Throat');
    expect(runOfLabel('Throat')).toBe('Throat');
    expect(stableColorKey('#7 · design_v02 · Woofer')).toBe('design_v02 · Woofer');
    expect(runOfLabel('#7 · design_v02 · Woofer')).toBe('design_v02');
  });
});

describe('the primary run', () => {
  it('takes the accent slot whatever its name hashes to', () => {
    const colors = seriesColorsByLabel(
      ['#41 · tritonia-q', '#40 · asro68'],
      PALETTE,
      '#000',
      'tritonia-q',
    );
    expect(colors.get('#41 · tritonia-q')).toBe(PALETTE[0]);
    expect(colors.get('#40 · asro68')).not.toBe(PALETTE[0]);
  });

  it('pins its first trace only, so its other angles stay distinct', () => {
    const labels = ['#41 · tritonia-q · On-axis', '#41 · tritonia-q · 30°'];
    const colors = seriesColorsByLabel(labels, PALETTE, '#000', 'tritonia-q');
    expect(colors.get(labels[0])).toBe(PALETTE[0]);
    expect(colors.get(labels[1])).not.toBe(PALETTE[0]);
  });

  it('pins nothing when the chart filtered the primary run out', () => {
    // The impedance chart drops runs with no impedance block. Promoting the
    // first survivor into the accent slot would recolour the chart around a
    // run the user did not select, which is the positional failure this
    // module exists to prevent.
    const survivors = ['#40 · asro68', '#39 · bigmeh'];
    const filtered = seriesColorsByLabel(survivors, PALETTE, '#000', 'tritonia-q');
    expect(filtered).toEqual(seriesColorsByLabel(survivors, PALETTE, '#000'));
  });

  it('does not depend on where the primary run sits in the list', () => {
    const forward = seriesColorsByLabel(['#41 · tritonia-q', '#40 · asro68'], PALETTE, '#000', 'tritonia-q');
    const reversed = seriesColorsByLabel(['#40 · asro68', '#41 · tritonia-q'], PALETTE, '#000', 'tritonia-q');
    expect(reversed.get('#41 · tritonia-q')).toBe(forward.get('#41 · tritonia-q'));
    expect(reversed.get('#40 · asro68')).toBe(forward.get('#40 · asro68'));
  });

  it('falls back rather than emitting an empty colour for an empty palette', () => {
    expect(seriesColorsByLabel(['#1 · run'], [], '#abc', 'run').get('#1 · run')).toBe('#abc');
  });
});
