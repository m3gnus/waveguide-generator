import { describe, expect, it } from 'vitest';
import { solveModel, type ModelSolvePress } from './actions';
import { importedSolveError } from '../shell/JobsCoordinator';
import { failureWords, PLAIN_REQUEST_FAILURE } from '../api/httpError';

const press = {} as ModelSolvePress;

/** What the user reads for a refusal, through the whole path from the response. */
async function shown(code: string, sentence: string): Promise<string> {
  const refused = (async () => Response.json({ detail: `${code}: ${sentence}`, error: { code, details: {} } }, { status: 422 })) as unknown as typeof fetch;
  const error = await solveModel('m', press, refused).catch((reason: unknown) => reason);
  return failureWords(importedSolveError(error));
}

describe('an imported solve refusal keeps its own fix', () => {
  it.each([
    ['ingestion source tag map', 'passive cardioid requires an ingestion source tag map', 'source tag map, so WG cannot tell'],
    ['port aperture', 'passive cardioid needs PORT_EXIT aperture sources', 'has no port-exit aperture'],
    ['one diaphragm', 'passive cardioid needs exactly one MF diaphragm source', 'exactly one MF diaphragm'],
    ['drive channel', "coupled passive cardioid requires the MF diaphragm's drive channel to carry one driver model", 'its own channel under Drivers'],
    ['port patches', 'coupled passive cardioid requires all PORT_EXIT patches in one drive channel', 'Assign every port exit patch'],
  ])('passive_cardioid_topology: %s', async (_name, sentence, advice) => {
    const words = await shown('passive_cardioid_topology', sentence);
    expect(words).toContain(advice);
    expect(words).toMatch(/^Passive cardioid: /);
  });

  it.each([
    ['polar_grid_narrowing', 'This model needs the horizontal polar plane from −180° to 180°. Turn it on under Directivity map, then press Solve.'],
    ['ingest_mesh_unavailable', 'Prepare this mesh again before solving.'],
    ['ingest_record_incomplete', 'WG could not read its saved copy of this model. Send it again from Fusion.'],
    ['ingest_registry_unavailable', 'WG could not reach its saved models. Reopen WG, then press Solve.'],
    ['ingest_not_found', 'WG cannot find this model. Send it again from Fusion.'],
    ['ingest_sha_mismatch', 'This model changed since it was prepared. Prepare it again, then press Solve.'],
    ['cad_identity_invalid', 'WG could not read its saved copy of this model. Send it again from Fusion.'],
    ['cad_identity_unsupported', 'This model was sent by a newer version of WG. Update WG to open it.'],
    ['drive_source_coverage', 'Every driver surface needs one drive channel. Assign each one under Drivers, then press Solve.'],
    ['required_source_skipped', 'A required driver surface cannot be left out. Turn it back on under Drivers, then press Solve.'],
    ['unknown_skipped_source', 'The driver surfaces changed since this model was prepared. Prepare it again, then press Solve.'],
    ['skipped_sources_mismatch', 'The driver surfaces changed since this model was prepared. Prepare it again, then press Solve.'],
    ['mesh_sizes_not_applicable', 'This is a ready mesh; import a new mesh to change its triangles.'],
    ['mesh_sizes_mismatch', 'The mesh sizes changed since this model was prepared. Prepare it again, then press Solve.'],
    ['native_source_engine', 'Source contours need the Metal engine. Choose Metal under Solver, then press Solve.'],
    ['unacknowledged_findings', "Review this model's findings in CAD Link, then press Solve."],
    ['imported_infinite_baffle_unsupported', 'An imported model solves in free space only. Turn off the infinite baffle, then press Solve.'],
    ['imported_diagonal_inclination_unsupported', 'An imported model needs the diagonal plane at 45°. Set it under Directivity map, then press Solve.'],
    ['imported_axial_legacy_retry', "This run's axial sources were solved under the earlier frame-axis rule. Start a new solve to use per-source axes."],
    ['imported_symmetry_mismatch', 'This model was prepared with different symmetry. Prepare it again, then press Solve.'],
    ['imported_reduced_mesh_inverted', 'An earlier WG build prepared this model inside out. Send it again from Fusion.'],
    ['driver_source_area_unavailable', 'WG could not read its saved copy of this model. Send it again from Fusion.'],
  ])('%s', async (code, sentence) => {
    expect(await shown(code, sentence)).toBe(sentence);
  });

  it('turns a sentence with internal words into the fallback, once classification has seen it', async () => {
    expect(await shown('native_source_contract', 'patch_id must name a source_contour')).toBe(PLAIN_REQUEST_FAILURE);
  });
});
