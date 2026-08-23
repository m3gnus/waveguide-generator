import { describe, expect, it } from 'vitest';
import { buildRunReportHtml } from './runReport';
import type { ResultPayload } from './types';

const channel: ResultPayload = {
  frequencies: [100, 200, 400],
  spl_on_axis: { frequencies: [100, 200, 400], spl: [90, 92, 91], phase_degrees: [0, 5, 10] },
  di: { frequencies: [100, 200, 400], di: [3, 4, 5] },
  beam_shape: {
    frequencies: [100, 200, 400],
    horizontal_beamwidth_deg: [100, 80, 60],
    vertical_beamwidth_deg: [90, 70, 50],
  },
  metadata: { warnings: ['mesh warning <check>'] },
};

describe('static run report', () => {
  it('renders every channel with inline plots, tables, and escaped evidence', () => {
    const result: ResultPayload = {
      frequencies: [],
      channel_order: ['HF', 'MF'],
      channels: { HF: channel, MF: { ...channel, spl_on_axis: { ...channel.spl_on_axis, spl: [80, 82, 81] } } },
    };
    const html = buildRunReportHtml(result, {
      title: 'Run <42>',
      generatedAt: new Date('2026-08-20T12:00:00Z'),
    });

    expect(html).toContain('<!doctype html>');
    expect(html).toContain('<h1>Run &lt;42&gt;</h1>');
    expect(html).toContain('<h2>HF</h2>');
    expect(html).toContain('<h2>MF</h2>');
    expect(html).toContain('<svg class="plot"');
    expect(html).toContain('Derived acoustics table (3 rows)');
    expect(html).toContain('mesh warning &lt;check&gt;');
    expect(html).not.toContain('mesh warning <check>');
    expect(html).toContain("default-src 'none'");
    expect(html).not.toContain('<script');
  });

  it('states the group delay unit in the header it is written in', () => {
    const options = { title: 'Units', generatedAt: new Date('2026-08-20T12:00:00Z') };
    expect(buildRunReportHtml(channel, options)).toContain('<th>Group delay ms</th>');
    expect(buildRunReportHtml(channel, { ...options, groupDelayUnit: 'cycles' }))
      .toContain('<th>Group delay cycles</th>');
  });

  it('renders a flat legacy result and honest empty derived sections', () => {
    const html = buildRunReportHtml({ frequencies: [] }, {
      title: 'Legacy', generatedAt: new Date('2026-08-20T12:00:00Z'),
    });
    expect(html).toContain('<h2>Result</h2>');
    expect(html).toContain('No samples available for this plot.');
    expect(html).toContain('No result warnings.');
  });
});
