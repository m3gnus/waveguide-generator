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
  it('uses an eligible selected result, then falls back to the newest eligible completed job', () => {
    const jobs = [job('newest', true), job('selected', true), job('old', true)];
    expect(fieldPlaneJob(jobs, 'selected')?.id).toBe('selected');
    expect(fieldPlaneJob(jobs, 'missing')?.id).toBe('newest');
  });

  it('explains why the disabled overlay needs a new solve', () => {
    expect(fieldPlaneUnavailableTooltip([job('legacy', false, 'solve_predates_traces')])).toContain('re-solve');
    expect(fieldPlaneUnavailableTooltip([job('axisymmetric', false, 'unsupported_solve_mode')])).toContain('full-3D Metal');
  });
});
