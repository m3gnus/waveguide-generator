import { describe, expect, it } from 'vitest';
import type { ImportedSolveSubmission } from './actions';
import { designForFamily } from '../stores/design';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { projectSubmittedDesign, projectSubmittedImport, submittedProjectionsEqual } from './submittedProjection';

function options() {
  resetSolveOptionsStore();
  return useSolveOptionsStore.getState().options();
}

function imported(ingestId: string): ImportedSolveSubmission {
  return {
    geometry: {
      type: 'imported', ingest_id: ingestId, manifest_sha256: `manifest:${ingestId}`, artifact_sha256: `artifact:${ingestId}`,
      drive_channels: [{ id: 'hf', source_ids: ['source-hf'], motion: 'normal', driver: { sd_cm2: 80, bl_t_m: 7 } }],
      drive_voltage_v: 2.83,
      mesh: { rigid_size_mm: 5, transition_mm: 4, source_size_mm: { 'source-hf': 2 } },
      acknowledged_findings: ['report:finding'], skipped_source_ids: [], exterior_only: false,
      combine: { members: ['mf', 'hf'], crossovers_hz: [1_200], level_match: true, align: true },
    },
    options: { ...options(), engine: 'metal', symmetry: 'auto', frequency_range: [200, 20_000], num_frequencies: 24 },
  };
}

describe('canonical submitted design projection', () => {
  it('is equal for cloned submitted state and ignores expression sidecars', () => {
    const design = designForFamily('R-OSSE');
    const first = projectSubmittedDesign(design, options());
    const clone = structuredClone(design);
    clone._expressions = { R: { value: design.R!, raw: '70 * 2' } };
    expect(submittedProjectionsEqual(first, projectSubmittedDesign(clone, options()))).toBe(true);
  });

  it('detects family scalar, solve-option, and simulation-block differences', () => {
    const design = designForFamily('R-OSSE');
    const solveOptions = options();
    const baseline = projectSubmittedDesign(design, solveOptions);
    const scalar = structuredClone(design); scalar.R! += 1;
    const simulation = structuredClone(design); simulation.simulation.f2 += 1_000;
    expect(submittedProjectionsEqual(baseline, projectSubmittedDesign(scalar, solveOptions))).toBe(false);
    expect(submittedProjectionsEqual(baseline, projectSubmittedDesign(simulation, solveOptions))).toBe(false);
    expect(submittedProjectionsEqual(baseline, projectSubmittedDesign(design, { ...solveOptions, verbose: true }))).toBe(false);
  });

  it('counts configured morph differences even when the target is None', () => {
    const design = designForFamily('R-OSSE');
    expect(design.morph.target_shape).toBe(0);
    const changed = structuredClone(design);
    changed.morph.target_width = 300;
    expect(submittedProjectionsEqual(
      projectSubmittedDesign(design, options()),
      projectSubmittedDesign(changed, options()),
    )).toBe(false);
  });

  it('projects imported solve inputs and changes only when that solve changes', () => {
    const firstSubmission = imported('wgi_first');
    const baseline = projectSubmittedImport(firstSubmission);
    const evidenceOnly = structuredClone(firstSubmission);
    evidenceOnly.geometry.acknowledged_findings = ['another-report:finding'];
    const alignment = structuredClone(firstSubmission);
    alignment.geometry.combine!.align = false;

    expect(baseline.kind).toBe('imported');
    expect(baseline.geometry).not.toHaveProperty('acknowledged_findings');
    expect(submittedProjectionsEqual(baseline, projectSubmittedImport(evidenceOnly))).toBe(true);
    expect(submittedProjectionsEqual(baseline, projectSubmittedImport(alignment))).toBe(false);
    expect(submittedProjectionsEqual(baseline, projectSubmittedImport(imported('wgi_second')))).toBe(false);
  });
});
