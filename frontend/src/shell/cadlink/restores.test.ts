import { expect, it, vi } from 'vitest';
import type { JobItem } from '../../api/jobsSocket';
import { restoreCadJobModel } from './restores';

it('does not recall an unnumbered CAD preparation as an archived run', async () => {
  const enterCadWorkspace = vi.fn();
  const reportStatus = vi.fn();
  const reportViewportNotice = vi.fn();
  const showIngestedMesh = vi.fn();
  const job = {
    run_number: null,
    config_summary: { geometry_type: 'imported' },
  } as unknown as JobItem;

  expect(await restoreCadJobModel(job, {
    enterCadWorkspace, reportStatus, reportViewportNotice, showIngestedMesh,
  })).toBe(false);
  expect(enterCadWorkspace).not.toHaveBeenCalled();
  expect(reportStatus).not.toHaveBeenCalled();
});

it('restores the coupled BEAT model without legacy driver conversion', async () => {
  const { cadHistorySetup } = await import('./restores');
  const model = { version: 1, motion_axis: [0, 1, 0], re_ohm: 6, le_h: .0005,
    bl_n_per_a: 7, mmd_kg: .02, cms_m_per_n: .001, rms_n_s_per_m: 1.5 };
  const job = { cad_setup: { drive_channels: [{ id: 'd', source_ids: ['s'], motion: 'axial', exterior_transducer: model }],
    drive_voltage_v: 5.66 }, solve_options: { frequencies_hz: [100, 500] } } as unknown as JobItem;
  const record = { sources: [{ id: 's', default_drive_channel_id: 'd' }], skipped_source_ids: [],
    mesh_sizes: { rigid_size_mm: 8, transition_mm: 20, source_size_mm: { s: 3 } } } as never;
  const restored = cadHistorySetup(job, record);
  expect(restored.driveChannels[0]).toEqual({ id: 'd', source_ids: ['s'], motion: 'axial', exterior_transducer: model });
  expect(restored.driveVoltageV).toBe(5.66);
  expect(restored.channelDrivers).toEqual({});
});
