import { describe, expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { designFingerprint } from '../results/runCoherence';
import type { CurrentModel } from '../model/currentModel';
import { designForFamily, serializeDesign, type DesignDocument } from '../stores/design';
import { fieldPlaneJob, femFieldJob, fieldPlaneUnavailableTooltip } from './Viewport';

const solved = designForFamily('OSSE');
const elsewhere = { ...structuredClone(solved), L: (solved.L ?? 0) + 5 };
const context: CurrentModel = {
  kind: 'parametric', key: designFingerprint(solved), lineage: null, label: 'Test', viewportShowing: true,
};

function job(id: string, available: boolean, unavailableReason: string | null = null, design: DesignDocument = solved): JobItem {
  return {
    id,
    status: 'complete', has_results: true,
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
    expect(fieldPlaneJob(jobs, 'missing', context)?.id).toBe('newest');
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
    expect(fieldPlaneUnavailableTooltip([job('axisymmetric', false, 'unsupported_axisymmetric_formulation')])).toContain('historical Axisymmetric result');
    expect(fieldPlaneUnavailableTooltip([job('coupled-ib', false, 'unsupported_coupled_infinite_baffle')])).toContain('Mounting to Bare shell');
    expect(fieldPlaneUnavailableTooltip([job('disabled', false, 'disabled_by_option')])).toContain('Keep field plane data');
    expect(fieldPlaneUnavailableTooltip([job('ladder', false, 'unsupported_per_band_mesh_ladder')])).toContain('submit a new solve from the UI');
  });
});


it('hides retained fields when the shown result is a streamed run of the same model', () => {
  const complete = { ...job('complete', true), has_fem_field_artifact: true };
  const live = { ...job('live', false), status: 'running' as const, has_results: false };
  const provisional = { live: { result: { frequencies: [100] } } } as never;
  expect(fieldPlaneJob([live, complete], live.id, context, provisional)).toBeNull();
  expect(femFieldJob([live, complete], live.id, context, provisional)).toBeNull();
});
