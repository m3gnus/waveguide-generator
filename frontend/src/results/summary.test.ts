import { describe, expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { summaryGroups, summaryText, type SummaryGroup } from './summary';
import type { ResultPayload } from './types';

/** Group a count the way the summary does — in the runner's locale, not en-US.
 *
 * `toLocaleString()` takes its separator from the machine's region, so spelling
 * the expectation out as '4,500' passes in the US and fails everywhere that
 * groups with a space or a dot. The assertion is that the count is grouped at
 * all, which is what this preserves; an ungrouped '4500' still fails. */
const grouped = (value: number) => value.toLocaleString();

function job(overrides: Partial<JobItem> = {}): JobItem {
  return {
    id: 'abcdef123456', run_number: 7, parent_job_id: null,
    status: 'complete', progress: 1, stage: null, stage_message: null,
    created_at: '2026-08-12T10:00:00', queued_at: '2026-08-12T10:00:00',
    started_at: '2026-08-12T10:00:02', completed_at: '2026-08-12T10:00:14',
    config_summary: {}, solve_options: {} as JobItem['solve_options'], has_results: true, has_mesh_artifact: true,
    label: 'Reference horn', error_message: null, cancellation_requested: false, mesh_stats: null,
    script_snapshot: null, design_revision: 0, polar_grid: {}, rating: null, exported_files: [],
    auto_export_completed_at: null, auto_export_formats: {}, raw_results_file: null,
    mesh_artifact_file: null, log_tail: [], solve_wall_time_seconds: 12.4,
    ...overrides,
  };
}

function row(groups: SummaryGroup[], groupTitle: string, label: string) {
  return groups.find(({ title }) => title === groupTitle)?.rows.find((item) => item.label === label);
}

describe('simulation summary groups', () => {
  it('builds ordered provenance groups for a full parametric result', () => {
    const result: ResultPayload = {
      frequencies: [100, 1_000, 20_000],
      directivity: {
        horizontal: [[[0, 0]]],
        vertical: [[[0, 0]]],
        diagonal: [[[0, 0]]],
      } as ResultPayload['directivity'],
      impedance: { frequencies: [100], real: [1], imaginary: [0] },
      beam_shape: { di_domain: 'sphere' },
      balloon: {
        frequencies: [100, 1_000, 20_000],
        theta_deg: Array.from({ length: 37 }, (_, index) => index * 5),
        phi_deg: Array.from({ length: 72 }, (_, index) => index * 5),
        spl_norm_db: [],
        hemisphere: true,
      },
      metadata: {
        frequency_source: 'generated_grid', frequency_spacing: 'log',
        engine: 'hornlab-metal-bem', solve_path: 'full-3d', solve_path_reason: 'requested full solve',
        symmetry: { requested: 'auto', resolved_quadrants: 1, auto_resolution: { symmetric_x: true, symmetric_y: true } },
        device_interface: { selected: 'metal' }, infinite_baffle: { backend: 'full_3d_coupled' },
        mesh_stats: {
          triangle_count: 4_500, full_domain_triangle_count: 18_000, domain_multiplier: 4,
          vertex_count: 2_350, max_edge_mm: 8, dimensions_m: { width: 0.4, height: 0.25, depth: 0.12 },
          integrity: { valid: true },
        },
        directivity: {
          effective_distance_m: 2, requested_distance_m: 1.5, observation_origin: 'mouth',
          angle_range_degrees: [0, 180], angular_step_degrees: 5, requested_angular_step_degrees: 10,
          sample_count: 37, normalization_angle_degrees: 5, diagonal_angle_degrees: 35.5,
        },
        impedance_units: 'Z/(rho*c)', phase_time_convention: 'exp(+ikr)',
        mesh_validation: { mode: 'warn' }, warning_count: 0, failure_count: 0, partial_success: false,
      },
    };

    const groups = summaryGroups({ result, job: job() });

    expect(groups.map(({ title }) => title)).toEqual([
      'Run', 'Sweep', 'Solve', 'Mesh', 'Measurement', 'Conventions',
    ]);
    expect(row(groups, 'Run', 'Name')?.value).toBe('#7 · Reference horn');
    expect(row(groups, 'Run', 'Solve time')?.value).toBe('12.4 s');
    expect(row(groups, 'Sweep', 'Range')?.value).toBe('100 Hz – 20.0 kHz');
    expect(row(groups, 'Sweep', 'Spacing')?.value).toBe('logarithmic');
    expect(row(groups, 'Solve', 'Path')).toEqual({ label: 'Path', value: 'full 3D', title: 'requested full solve' });
    expect(row(groups, 'Solve', 'Symmetry')?.value).toBe('quadrant 1 · quarter domain (server resolved)');
    expect(row(groups, 'Mesh', 'Triangles')?.value).toBe(grouped(4_500));
    expect(row(groups, 'Mesh', 'Full domain')?.value).toBe(grouped(18_000));
    expect(row(groups, 'Measurement', 'Distance')?.value).toBe('2.00 m from mouth (requested 1.50 m)');
    expect(row(groups, 'Measurement', 'Sampling')?.value).toBe('5° resolved (requested 10°), 37 samples');
    expect(row(groups, 'Measurement', 'Balloon')?.value).toBe('37 × 72 × 3 freq · hemisphere');
    expect(row(groups, 'Conventions', 'Impedance')?.value).toBe('Z/(rho*c)');
  });

  it('handles a minimal result without placeholders or diagnostics', () => {
    const groups = summaryGroups({ result: { frequencies: [100, 200] } });

    expect(groups).toEqual([{
      title: 'Sweep',
      rows: [
        { label: 'Range', value: '100 Hz – 200 Hz' },
        { label: 'Points', value: '2' },
      ],
    }]);
    expect(JSON.stringify(groups)).not.toMatch(/—|undefined|unknown/);
  });

  it('formats zero hertz as real data', () => {
    const groups = summaryGroups({ result: { frequencies: [0, 100] } });
    expect(row(groups, 'Sweep', 'Range')?.value).toBe('0 Hz – 100 Hz');
  });

  it('uses the CAD wrapper for import provenance and marks shared batch timing', () => {
    const channel: ResultPayload = {
      frequencies: [100, 200],
      metadata: { performance: { total_time_seconds: 125 }, engine: 'hornlab-metal-bem', source_ids: ['tweeter'] },
    };
    const manifest = 'sha256:1234567890abcdef';
    const wrapper: ResultPayload = {
      frequencies: [],
      channels: { high: channel, low: { frequencies: [100, 200] } },
      channel_order: ['high', 'low'],
      metadata: {
        geometry_type: 'imported', ingest_id: 'wgi_123', manifest_sha256: manifest,
        solve_path: 'full-3d',
        per_source_frequency_validity: { tweeter: { effective_max_valid_frequency_hz: 20_000 } },
      },
    };

    const groups = summaryGroups({ result: channel, wrapper, channelId: 'high' });

    expect(row(groups, 'Run', 'Solve time')).toEqual({
      label: 'Solve time', value: '2m 05s',
      title: 'Shared batch time for all 2 imported drive channels; not an isolated channel solve.',
    });
    expect(row(groups, 'Import', 'Channel')?.value).toBe('high · 2 channels');
    expect(row(groups, 'Import', 'Manifest')).toEqual({ label: 'Manifest', value: manifest.slice(0, 12), title: manifest });
    expect(row(groups, 'Validity', 'Governing ceiling')?.value).toBe('20.0 kHz');
    expect(row(groups, 'Validity', 'Source tweeter')?.value).toBe('20.0 kHz');
    expect(groups.find(({ title }) => title === 'Validity')?.tone).toBeUndefined();
  });

  it('keeps solved and symmetry-expanded triangle counts distinct', () => {
    const result: ResultPayload = {
      frequencies: [100],
      metadata: { mesh_stats: { triangle_count: 3_000, domain_multiplier: 4, full_domain_triangle_count: 12_000 } },
    };
    const groups = summaryGroups({ result });

    expect(row(groups, 'Mesh', 'Triangles')?.value).toBe(grouped(3_000));
    expect(row(groups, 'Mesh', 'Full domain')).toEqual({
      label: 'Full domain', value: grouped(12_000), title: 'Symmetry-expanded equivalent of the solved mesh.',
    });
  });

  it('lists only directivity planes with samples', () => {
    const result = {
      frequencies: [100],
      directivity: { horizontal: [], vertical: [[[0, 0]]], custom: [[[0, -1]]] },
    } as ResultPayload;
    const groups = summaryGroups({ result });

    expect(row(groups, 'Measurement', 'Planes')?.value).toBe('vertical · custom');
  });

  it('surfaces and caps warning and failure text', () => {
    const result: ResultPayload = {
      frequencies: [0],
      metadata: {
        warnings: ['warning one', 'warning two', 'warning three', 'warning four', 'warning five'],
        warning_count: 5,
        failures: Array.from({ length: 5 }, (_, index) => ({
          frequency_hz: index * 100, stage: 'solve', code: `failure_${index + 1}`, detail: `detail ${index + 1}`,
        })),
        failure_count: 5,
        partial_success: true,
      },
    };

    const diagnostics = summaryGroups({ result }).find(({ title }) => title === 'Diagnostics');

    expect(diagnostics?.tone).toBe('warning');
    expect(diagnostics?.rows.filter(({ label }) => label === 'Warning').map(({ value }) => value)).toEqual([
      'warning one', 'warning two', 'warning three', 'warning four', '+1 more',
    ]);
    expect(diagnostics?.rows.filter(({ label }) => label === 'Failed')).toHaveLength(5);
    expect(diagnostics?.rows.find(({ label, value }) => label === 'Failed' && value.startsWith('0 Hz'))?.value)
      .toBe('0 Hz · solve · failure_1: detail 1');
    expect(diagnostics?.rows.at(-1)?.value).toBe('+1 more');
  });

  it('shows mesh builder warning text, which the solver never copies into metadata.warnings', () => {
    const large = 'Large solve mesh: 5,488 triangles against a warning threshold of 4,500.';
    const result: ResultPayload = {
      frequencies: [1_000],
      metadata: {
        warning_count: 0,
        mesh_validation: { mode: 'warn' },
        mesh_stats: { warnings: [large], integrity: { valid: true } },
      },
    };

    const diagnostics = summaryGroups({ result }).find(({ title }) => title === 'Diagnostics');

    expect(diagnostics?.rows.find(({ label }) => label === 'Mesh validation')?.value).toBe('warnings · warn mode');
    expect(diagnostics?.rows.filter(({ label }) => label === 'Warning').map(({ value }) => value)).toEqual([large]);
  });

  it('does not repeat a mesh warning the solver already reported', () => {
    const shared = 'Large solve mesh: 5,488 triangles.';
    const result: ResultPayload = {
      frequencies: [1_000],
      metadata: { warnings: [shared], warning_count: 1, mesh_stats: { warnings: [shared] } },
    };

    const diagnostics = summaryGroups({ result }).find(({ title }) => title === 'Diagnostics');

    expect(diagnostics?.rows.filter(({ label }) => label === 'Warning')).toHaveLength(1);
  });

  it('reads nested combine warnings even when top-level warning_count is zero', () => {
    const warning = "crossover 2400 Hz is above channel 'high' source validity limit 1200 Hz";
    const result: ResultPayload = {
      frequencies: [100, 20_000],
      metadata: { warnings: [], warning_count: 0, combine: { warnings: [warning] } },
    };

    const diagnostics = summaryGroups({ result }).find(({ title }) => title === 'Diagnostics');

    expect(diagnostics?.tone).toBe('warning');
    expect(diagnostics?.rows.filter(({ label }) => label === 'Warning').map(({ value }) => value)).toEqual([warning]);
  });

  it('reports the governing effective ceiling and every source when they differ', () => {
    const result: ResultPayload = {
      frequencies: [100, 20_000],
      metadata: { source_ids: ['high-source', 'low-source'] },
    };
    const wrapper: ResultPayload = {
      frequencies: [],
      channels: { combined: result },
      metadata: {
        per_source_frequency_validity: {
          'high-source': { effective_max_valid_frequency_hz: 12_000 },
          'low-source': { effective_max_valid_frequency_hz: 1_200 },
        },
      },
    };

    const groups = summaryGroups({ result, wrapper, channelId: 'combined' });
    const validity = groups.find(({ title }) => title === 'Validity');

    expect(validity?.tone).toBe('warning');
    expect(row(groups, 'Validity', 'Governing ceiling')?.value).toBe('1.20 kHz');
    expect(row(groups, 'Validity', 'Source high-source')?.value).toBe('12.0 kHz');
    expect(row(groups, 'Validity', 'Source low-source')?.value).toBe('1.20 kHz');
    expect(row(groups, 'Validity', 'Accuracy')?.value).toBe('Results might not be very accurate above 1.20 kHz.');
  });

  it('names the channel by band and keeps the id the user authored', () => {
    const wrapper: ResultPayload = {
      frequencies: [],
      channel_order: ['drive-mf', 'drive-hf'],
      channels: {
        'drive-mf': { frequencies: [100], metadata: { role: 'MF' } },
        'drive-hf': { frequencies: [100], metadata: { role: 'HF' } },
      },
      metadata: { geometry_type: 'imported' },
    };

    const groups = summaryGroups({
      result: wrapper.channels!['drive-mf'] as ResultPayload,
      wrapper,
      channelId: 'drive-mf',
    });

    expect(row(groups, 'Import', 'Channel')?.value).toBe(`MF (drive-mf) · ${grouped(2)} channels`);
  });

  it('states the crossovers and the alignment delays of a combined channel', () => {
    const combined: ResultPayload = {
      frequencies: [100],
      metadata: {
        combine: {
          members: ['drive-lf', 'drive-mf', 'drive-hf'],
          member_roles: ['LF', 'MF', 'HF'],
          crossovers_hz: [100, 1_000],
          align: true,
          delays_ms: { 'drive-lf': 0, 'drive-mf': 0.25, 'drive-hf': 1.5 },
        },
      },
    };

    const groups = summaryGroups({ result: combined });

    // A payload from before the filter library only ever meant LR4, and the
    // row says so rather than leaving the slope unstated.
    expect(row(groups, 'Combine', 'Crossover')?.value).toBe('LF → MF 100 Hz LR4, MF → HF 1.00 kHz LR4');
    expect(row(groups, 'Combine', 'Delays')?.value).toBe('LF 0.00 ms · MF 0.25 ms · HF 1.50 ms');
    expect(row(groups, 'Combine', 'Gains')).toBeUndefined();
    expect(row(groups, 'Combine', 'Alignment')).toBeUndefined();
  });

  it('states the family, the modes, the gains and the per-pair alignment of a v2 sum', () => {
    const combined: ResultPayload = {
      frequencies: [100],
      metadata: {
        combine: {
          members: ['drive-mf', 'drive-hf'],
          member_roles: ['MF', 'HF'],
          reference: 'drive-hf',
          crossovers_hz: [null],
          channels: {
            'drive-mf': {
              hp: null, lp: { family: 'butterworth', order: 3, fc_hz: 900 },
              gain_db: -1.5, gain_mode: 'auto', delay_ms: 0.45, delay_mode: 'manual',
            },
            'drive-hf': {
              hp: { family: 'lr', order: 4, fc_hz: 1_100 }, lp: null,
              gain_db: 0, gain_mode: 'manual', delay_ms: 0, delay_mode: 'auto',
            },
          },
          pairs: {
            'drive-mf-drive-hf': {
              eval_hz: 1_000, fit_residual_deg: 3, phase_error_at_fc_deg: 4, reverse_null_db: -28.4,
            },
          },
        },
      },
    };

    const groups = summaryGroups({ result: combined });

    expect(row(groups, 'Combine', 'Crossover')?.value).toBe('MF → HF LP 900 Hz BW3 / HP 1.10 kHz LR4');
    expect(row(groups, 'Combine', 'Delays')?.value).toBe('MF 0.45 ms (manual) · HF ref');
    expect(row(groups, 'Combine', 'Gains')?.value).toBe('MF −1.50 dB · HF +0.00 dB (manual)');
    expect(row(groups, 'Combine', 'Alignment')?.value).toBe('MF/HF @ 1.00 kHz: 4° · null −28 dB · fit ±3°');
  });

  it('falls back to channel ids for an unroled sum and omits delays when unaligned', () => {
    const combined: ResultPayload = {
      frequencies: [100],
      metadata: {
        combine: {
          members: ['low', 'high'],
          crossovers_hz: [800],
          align: false,
          delays_ms: { low: 0, high: 0.4 },
        },
      },
    };

    const groups = summaryGroups({ result: combined });

    expect(row(groups, 'Combine', 'Crossover')?.value).toBe('low → high 800 Hz LR4');
    expect(row(groups, 'Combine', 'Delays')).toBeUndefined();
  });

  it('uses a worded balloon status when no balloon block was returned', () => {
    const groups = summaryGroups({
      result: { frequencies: [100], metadata: { balloon_sampling: { status: 'missing_result' } } },
    });
    expect(row(groups, 'Measurement', 'Balloon')?.value).toBe('requested, none returned');
    expect(row(groups, 'Measurement', 'Balloon')?.value).not.toBe('0');
  });

  it('omits the driver row for a spec that names no driver', () => {
    // `label` is optional: a hand-entered driver has no name, and an invented
    // one would be worse than none.
    const groups = summaryGroups({
      result: {
        frequencies: [100],
        metadata: {
          impedance_units: 'ohms',
          drive: { voltage_v: 2.83, rg_ohm: 0.1 },
          driver: {
            spec: { sd_cm2: 55, bl_t_m: 6.2, re_ohm: 5.4, xmax_mm: 4.500000000000001 },
            cone_excursion_mm: { frequencies: [100], values: [1.2], peak_mm: 1.2 },
          },
        },
      },
    });
    expect(row(groups, 'Drive', 'Voltage')?.value).toBe('2.83 V rms · Rg 0.1 Ω');
    expect(row(groups, 'Drive', 'Driver')).toBeUndefined();
    expect(row(groups, 'Drive', 'Peak excursion')?.value).toBe('1.20 mm · 27% of Xmax 4.5 mm');
    expect(row(groups, 'Drive', 'Peak excursion')?.title).toContain('One-way peak');
  });

  it('names the driver the channel was solved with', () => {
    // The solver copies `DriverSpec.label` to `metadata.driver.label` beside
    // the spec, so a driver picked from the library can be read off the card.
    const groups = summaryGroups({
      result: {
        frequencies: [100],
        metadata: {
          drive: { voltage_v: 2.83 },
          driver: { label: 'Acme HD-1', spec: { sd_cm2: 26, label: 'Acme HD-1' } },
        },
      },
    });
    expect(row(groups, 'Drive', 'Driver')?.value).toBe('Acme HD-1');
  });

  it('retains count-only diagnostics from a partial legacy payload', () => {
    const groups = summaryGroups({ result: { frequencies: [], metadata: { warning_count: 2, failure_count: 1 } } });
    expect(groups.find(({ title }) => title === 'Diagnostics')).toMatchObject({
      tone: 'warning',
      rows: [
        { label: 'Warning', value: '2 reported' },
        { label: 'Failed', value: '1 reported' },
      ],
    });
  });
});

describe('summary text', () => {
  it('renders group headings and rows with one trailing newline', () => {
    expect(summaryText([
      { title: 'Run', rows: [{ label: 'Name', value: '#1 · Horn' }] },
      { title: 'Sweep', rows: [{ label: 'Points', value: '3' }, { label: 'Spacing', value: 'linear' }] },
    ])).toBe('RUN\n  Name: #1 · Horn\n\nSWEEP\n  Points: 3\n  Spacing: linear\n');
    expect(summaryText([])).toBe('');
  });
});
