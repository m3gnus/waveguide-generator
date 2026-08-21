import { describe, expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { designFingerprint, type RunContext } from '../results/runCoherence';
import { designForFamily, serializeDesign, type DesignDocument } from '../stores/design';
import { fieldPlaneJob, fieldPlaneUnavailableTooltip } from './Viewport';

const solved = designForFamily('OSSE');
const elsewhere = { ...structuredClone(solved), L: (solved.L ?? 0) + 5 };
const context: RunContext = {
  mode: 'parametric', designRevision: 3, designFingerprint: designFingerprint(solved), ingestId: null, designId: null,
};

function job(id: string, available: boolean, unavailableReason: string | null = null, design: DesignDocument = solved): JobItem {
  return {
    id,
    status: 'complete',
    config_summary: {},
    design_revision: 3,
    script_snapshot: { version: 1, design: serializeDesign(design) },
    field_plane_available: available,
    unavailable_reason: unavailableReason,
  } as unknown as JobItem;
}

describe('viewport field-plane job selection', () => {
  it('uses only the selected complete result for the model in the viewport', () => {
    const jobs = [job('newest', true), job('selected', true), job('old', true)];
    expect(fieldPlaneJob(jobs, 'selected', context)?.id).toBe('selected');
    expect(fieldPlaneJob(jobs, 'missing', context)).toBeNull();
  });

  it('does not fall back across unavailable runs, edited designs, or models', () => {
    expect(fieldPlaneJob([job('newest', true), job('selected', false)], 'selected', context)).toBeNull();
    expect(fieldPlaneJob([job('selected', true, null, elsewhere)], 'selected', context)).toBeNull();
    expect(fieldPlaneJob([{
      ...job('cad', true),
      config_summary: { geometry_type: 'imported' },
      cad_source: { ingest_id: 'wgi_other' } as JobItem['cad_source'],
    }], 'cad', context)).toBeNull();
  });

  it('explains why the disabled overlay needs a new solve', () => {
    expect(fieldPlaneUnavailableTooltip([job('legacy', false, 'solve_predates_traces')])).toContain('re-solve');
    expect(fieldPlaneUnavailableTooltip([job('axisymmetric', false, 'unsupported_solve_mode')])).toContain('full-3D Metal');
    expect(fieldPlaneUnavailableTooltip([job('disabled', false, 'disabled_by_option')])).toContain('Keep field plane data');
  });
});
