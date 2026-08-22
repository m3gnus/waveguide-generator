import { describe, expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import type { RunContext } from '../results/runCoherence';
import { fieldPlaneJob, fieldPlaneUnavailableTooltip } from './Viewport';

const context: RunContext = { mode: 'parametric', designRevision: 3, ingestId: null, designId: null };

function job(id: string, available: boolean, unavailableReason: string | null = null, revision = 3): JobItem {
  return {
    id,
    status: 'complete',
    config_summary: {},
    design_revision: revision,
    field_plane_available: available,
    unavailable_reason: unavailableReason,
  } as JobItem;
}

describe('viewport field-plane job selection', () => {
  it('uses only the selected complete result for the model in the viewport', () => {
    const jobs = [job('newest', true), job('selected', true), job('old', true)];
    expect(fieldPlaneJob(jobs, 'selected', context)?.id).toBe('selected');
    expect(fieldPlaneJob(jobs, 'missing', context)).toBeNull();
  });

  it('does not fall back across unavailable runs, revisions, or models', () => {
    expect(fieldPlaneJob([job('newest', true), job('selected', false)], 'selected', context)).toBeNull();
    expect(fieldPlaneJob([job('selected', true, null, 2)], 'selected', context)).toBeNull();
    expect(fieldPlaneJob([{
      ...job('cad', true),
      config_summary: { geometry_type: 'imported' },
      cad_source: { ingest_id: 'wgi_other' } as JobItem['cad_source'],
    }], 'cad', context)).toBeNull();
  });

  it('explains why the disabled overlay needs a new solve', () => {
    expect(fieldPlaneUnavailableTooltip([job('legacy', false, 'solve_predates_traces')])).toContain('re-solve');
    expect(fieldPlaneUnavailableTooltip([job('axisymmetric', false, 'unsupported_axisymmetric_formulation')])).toContain('Solver mode to Full 3D');
    expect(fieldPlaneUnavailableTooltip([job('coupled-ib', false, 'unsupported_coupled_infinite_baffle')])).toContain('Simulation type to Free-standing');
    expect(fieldPlaneUnavailableTooltip([job('disabled', false, 'disabled_by_option')])).toContain('Keep field plane data');
  });
});
