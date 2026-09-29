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
