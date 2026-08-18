import { describe, expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { fieldPlaneJob, fieldPlaneUnavailableTooltip } from './Viewport';

function job(id: string, available: boolean, unavailableReason: string | null = null): JobItem {
  return {
    id,
    status: 'complete',
    field_plane_available: available,
    unavailable_reason: unavailableReason,
  } as JobItem;
}

describe('viewport field-plane job selection', () => {
  it('uses the selected complete result, otherwise the latest complete result', () => {
    const jobs = [job('newest', true), job('selected', true), job('old', true)];
    expect(fieldPlaneJob(jobs, 'selected')?.id).toBe('selected');
    expect(fieldPlaneJob(jobs, 'missing')?.id).toBe('newest');
  });

  it('does not fall back to older retained traces when the selected or latest run is unavailable', () => {
    expect(fieldPlaneJob([job('newest', true), job('selected', false)], 'selected')).toBeNull();
    expect(fieldPlaneJob([job('newest', false), job('old', true)], null)).toBeNull();
  });

  it('explains why the disabled overlay needs a new solve', () => {
    expect(fieldPlaneUnavailableTooltip([job('legacy', false, 'solve_predates_traces')])).toContain('re-solve');
    expect(fieldPlaneUnavailableTooltip([job('axisymmetric', false, 'unsupported_solve_mode')])).toContain('full-3D Metal');
    expect(fieldPlaneUnavailableTooltip([job('disabled', false, 'disabled_by_option')])).toContain('Keep field plane data');
  });
});
