import { afterEach, describe, expect, it, vi } from 'vitest';
import { buildRunReportHtml, renderRunReportCharts, runReportChartSpecs } from './runReport';
import { buildDerivedAcoustics } from './derivedAcoustics';
import { activityStore } from '../shell/activity';
import { activityOperation } from '../shell/activityOperation';
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
  metadata: {
    warnings: ['mesh warning <check>'],
    radiated_power: {
      surface_w: [1, 2, 4],
      sphere_w: [1.1, 2.2, 4.4],
      sphere_coverage_sr: 12.566370614359172,
      definition: 'sphere/surface definition <escaped>',
      agreement_db: [0.4139, 0.4139, 0.4139],
    },
  },
};

describe('static run report', () => {
  afterEach(() => { activityStore.reset(); });

  it('renders every channel with embedded hornlab-plots images, tables, and escaped evidence', () => {
    const result: ResultPayload = {
      frequencies: [],
      channel_order: ['HF', 'MF'],
      channels: { HF: channel, MF: { ...channel, spl_on_axis: { ...channel.spl_on_axis, spl: [80, 82, 81] } } },
    };
    const image = 'data:image/png;base64,iVBORw==';
    const html = buildRunReportHtml(result, {
      title: 'Run <42>',
      generatedAt: new Date('2026-08-20T12:00:00Z'),
      charts: { HF: { response: image, beamwidth: image }, MF: { response: image } },
    });

    expect(html).toContain('<!doctype html>');
    expect(html).toContain('<h1>Run &lt;42&gt;</h1>');
    expect(html).toContain('<h2>HF</h2>');
    expect(html).toContain('<h2>MF</h2>');
    expect(html).not.toContain('<svg');
    expect(html.match(/<img class="plot" alt="[^"]+" src="data:image\/png;base64,iVBORw==">/g)).toHaveLength(3);
    // A chart with samples but no image says it was not drawn, not that the
    // run has no samples.
    expect(html.match(/This chart was not drawn for this report\./g)).toHaveLength(1);
    expect(html).not.toContain('No samples available');
    expect(html).toContain('Derived acoustics table (3 rows)');
    expect(html).toContain('Radiated-power cross-check');
    expect(html).toContain('Max |power agreement|');
    expect(html).toContain('Surface power W');
    expect(html).toContain('sphere/surface definition &lt;escaped&gt;');
    expect(html).not.toContain('sphere/surface definition <escaped>');
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

  it('draws each chart from the derived-acoustics rows the table lists', () => {
    const result: ResultPayload = { frequencies: [], channel_order: ['HF'], channels: { HF: channel } };
    const specs = runReportChartSpecs(result);
    expect(specs.map(({ section, chart }) => `${section}:${chart}`)).toEqual(['HF:response', 'HF:beamwidth']);
    const rows = buildDerivedAcoustics(channel).rows;
    const [response, beamwidth] = specs.map(({ spec }) => spec);
    expect(response).toMatchObject({ kind: 'lines', title: 'Response and power response', x: { scale: 'log' }, y: { label: 'Level [dB]' } });
    expect(response.series.map(({ label, x, y, color }) => ({ label, x, y, color }))).toEqual([
      { label: 'On-axis SPL', x: rows.map((row) => row.frequency_hz), y: rows.map((row) => row.on_axis_spl_db), color: 0 },
      { label: 'Power response', x: rows.map((row) => row.frequency_hz), y: rows.map((row) => row.power_response_db_spl_avg), color: 1 },
    ]);
    expect(beamwidth.series.map(({ label, y }) => ({ label, y }))).toEqual([
      { label: 'Horizontal', y: rows.map((row) => row.horizontal_beamwidth_deg) },
      { label: 'Vertical', y: rows.map((row) => row.vertical_beamwidth_deg) },
    ]);
    expect(runReportChartSpecs({ frequencies: [] })).toEqual([]);
  });

  it('renders the charts in the export theme with counted status-bar progress', async () => {
    const result: ResultPayload = { frequencies: [], channel_order: ['HF', 'MF'], channels: { HF: channel, MF: channel } };
    const progress: Array<[number, number]> = [];
    const fetcher = vi.fn<typeof fetch>(async (_input, init) => {
      const shown = activityStore.getSnapshot()[0]?.progress;
      if (shown) progress.push([shown.done, shown.total]);
      const { spec, theme } = JSON.parse(String(init?.body)) as { spec: { title: string }; theme: string };
      return Response.json({ image: `data:image/png;base64,${btoa(`${spec.title}|${theme}`)}` });
    });
    const labels: string[] = [];
    const unsubscribe = activityStore.subscribe(() => {
      const label = activityStore.getSnapshot()[0]?.label;
      if (label && labels.at(-1) !== label) labels.push(label);
    });
    // Inside an export, the chart drawing joins the export's activity and
    // must still say what it is doing.
    const charts = await activityOperation({ kind: 'export', label: 'Exporting HTML report' }, fetcher, (scoped) => renderRunReportCharts(result, 'classic', scoped));
    unsubscribe();
    expect(labels).toEqual(['Exporting HTML report', 'Drawing report charts', 'Exporting HTML report']);
    expect(fetcher).toHaveBeenCalledTimes(4);
    expect(progress).toEqual([[0, 4], [1, 4], [2, 4], [3, 4]]);
    expect(fetcher.mock.calls.every(([input]) => String(input) === '/api/render-chart')).toBe(true);
    expect(atob(charts.MF.beamwidth!.split(',')[1])).toBe('-6 dB beamwidth|classic');
    expect(atob(charts.HF.response!.split(',')[1])).toBe('Response and power response|classic');
    expect(activityStore.getSnapshot()).toEqual([]);
  });
});
