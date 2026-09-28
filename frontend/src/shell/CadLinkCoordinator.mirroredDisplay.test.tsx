/**
 * Stage 3 (S3-3): whenever WG solves a reduced domain, the viewport shows the
 * whole mirrored model -- for a model WG cut itself and for one already cut
 * in CAD (recovered, its mesh reflected) -- on the display tessellation and
 * the solve mesh alike. Display only: the solve inputs are the record's.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { CadReturnIngestRecord } from '../api/cadlink';
import type { DomainDecision } from '../api/domainDecision';
import {
  RECOVERED_HALF_WG_QUARTER,
  RECOVERED_NEGATIVE_HALF,
  REFUSED_CUT,
  WG_HALF,
} from '../api/domainDecision.fixtures';
import { resetCadReturnStore } from '../stores/cadReturn';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import type { FrameScene } from '../viewport/frameScene';
import { showIngestedMeshInViewport, showIngestedSolverMeshInViewport } from './CadLinkCoordinator';

function msh(nodes: Array<[number, number, number]>): string {
  return [
    '$MeshFormat', '2.2 0 8', '$EndMeshFormat',
    '$Nodes', String(nodes.length), ...nodes.map((node, index) => `${index + 1} ${node.join(' ')}`), '$EndNodes',
    '$Elements', '1', '1 2 2 1 1 1 2 3', '$EndElements', '',
  ].join('\n');
}

/** The model as it arrived: cut in CAD at x = 0, the x <= 0 side kept. */
const NEGATIVE_DISPLAY = msh([[-0.1, 0.02, 0], [-0.2, 0.02, 0], [-0.1, 0.05, 0.03]]);
/** The solve mesh: that piece reflected onto the side the solver mirrors. */
const POSITIVE_SOLVE = msh([[0.1, 0.02, 0], [0.1, 0.05, 0.03], [0.2, 0.02, 0]]);
/** A model WG cut itself: the display artifact is the whole of it. */
const WHOLE_DISPLAY = msh([[-0.1, 0.02, 0], [0.1, 0.02, 0], [0, 0.05, 0.03]]);

function record(ingestId: string, decision: DomainDecision | null, symmetry: CadReturnIngestRecord['symmetry']): CadReturnIngestRecord {
  return {
    ingest_id: ingestId, created_at: '', return_id: '',
    manifest_sha256: `sha256:${'1'.repeat(64)}`, artifact_sha256: `sha256:${'2'.repeat(64)}`,
    report_sha256: `sha256:${'3'.repeat(64)}`, acoustic_domain: 'free-space',
    scope: { status: 'clean', degraded_skip_count: 0 },
    sources: [], mesh_sizes: { rigid_size_mm: 4, transition_mm: 4, source_size_mm: {} },
    skipped_source_ids: [], freshness: { verdict: 'unlinked', instances: [] }, findings: [],
    symmetry, healing: {}, sizing_estimate: {}, polar_grid_derivation: {}, tag_map: {},
    ...(decision ? { domain_decision: decision } : {}),
  };
}

function fetcherFor(display: string, solve: string): typeof fetch {
  return (async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith('/viewport-mesh')) return new Response(display, { status: 200 });
    if (path.endsWith('/mesh')) return new Response(solve, { status: 200 });
    return new Response('{}', { status: 404 });
  }) as typeof fetch;
}

function extentX(scene: FrameScene): [number, number] {
  return [scene.bounds.min.x, scene.bounds.max.x];
}

function solvedTriangles(scene: FrameScene): number[][] {
  return scene.surfaces.filter((surface) => surface.solvedDomain).flatMap((surface) => {
    const triangles: number[][] = [];
    for (let offset = 0; offset < surface.indices.length; offset += 3) {
      triangles.push([0, 1, 2].map((corner) => surface.positions[surface.indices[offset + corner] * 3]));
    }
    return triangles;
  });
}

describe('the whole mirrored model of a reduced solve', () => {
  beforeEach(() => {
    resetCadReturnStore();
    importedMeshStore.clear();
    workspaceModeStore.setMode('cad');
  });

  afterEach(() => {
    importedMeshStore.clear();
    workspaceModeStore.setMode('parametric');
  });

  it('mirrors the display of a model cut in CAD and recovered, marking its plane', async () => {
    const cut = record('wgi_cut_display', RECOVERED_NEGATIVE_HALF, { cut_planes: [], domain_planes: ['x0'] });
    await showIngestedMeshInViewport(cut, 'PartyMEH', undefined, fetcherFor(NEGATIVE_DISPLAY, POSITIVE_SOLVE));
    const scene = importedMeshStore.getSnapshot().cad!.scene;
    expect(scene.surfaces.reduce((count, surface) => count + surface.indices.length / 3, 0)).toBe(2);
    expect(extentX(scene)[0]).toBeCloseTo(-0.2);
    expect(extentX(scene)[1]).toBeCloseTo(0.2);
    expect(scene.symmetryPlanes).toEqual(['x0']);
    // The piece the solver assembles is marked: the positive side.
    for (const xs of solvedTriangles(scene)) expect(Math.min(...xs)).toBeGreaterThanOrEqual(0);
  });

  it('mirrors the solve mesh of the same model across its CAD cut', async () => {
    const cut = record('wgi_cut_solve', RECOVERED_NEGATIVE_HALF, { cut_planes: [], domain_planes: ['x0'] });
    expect(await showIngestedSolverMeshInViewport(cut, 'PartyMEH', fetcherFor(NEGATIVE_DISPLAY, POSITIVE_SOLVE))).toBeNull();
    const scene = importedMeshStore.getSnapshot().cadSolver!.scene;
    expect(extentX(scene)[0]).toBeCloseTo(-0.2);
    expect(extentX(scene)[1]).toBeCloseTo(0.2);
    expect(scene.symmetryPlanes).toEqual(['x0']);
    expect(importedMeshStore.getSnapshot().cadSolver!.solvedTriangleCount).toBe(1);
  });

  it('mirrors a CAD half that WG also cuts: a quarter shows the whole model', async () => {
    const quarter = record('wgi_quarter', RECOVERED_HALF_WG_QUARTER, { cut_planes: ['y0'], domain_planes: ['x0', 'y0'] });
    const positiveDisplay = msh([[0.1, 0.02, 0], [0.1, -0.05, 0.03], [0.2, 0.02, 0]]);
    await showIngestedMeshInViewport(quarter, 'Quarter', undefined, fetcherFor(positiveDisplay, POSITIVE_SOLVE));
    const scene = importedMeshStore.getSnapshot().cad!.scene;
    expect(extentX(scene)).toEqual([expect.closeTo(-0.2), expect.closeTo(0.2)]);
    expect(scene.symmetryPlanes).toEqual(['x0', 'y0']);
  });

  it('leaves a WG-cut model\'s whole display as it is, marking the solved half', async () => {
    const whole = record('wgi_wg_half', WG_HALF, { cut_planes: ['x0'], domain_planes: ['x0'] });
    await showIngestedMeshInViewport(whole, 'Whole', undefined, fetcherFor(WHOLE_DISPLAY, POSITIVE_SOLVE));
    const scene = importedMeshStore.getSnapshot().cad!.scene;
    expect(scene.surfaces.reduce((count, surface) => count + surface.indices.length / 3, 0)).toBe(1);
    expect(scene.symmetryPlanes).toEqual(['x0']);
  });

  it('mirrors nothing for a refused model: nothing is solved', async () => {
    const refused = record('wgi_refused', REFUSED_CUT, { cut_planes: [], domain_planes: [] });
    await showIngestedMeshInViewport(refused, 'Refused', undefined, fetcherFor(NEGATIVE_DISPLAY, POSITIVE_SOLVE));
    const scene = importedMeshStore.getSnapshot().cad!.scene;
    expect(extentX(scene)[1]).toBeLessThanOrEqual(0);
    expect(scene.symmetryPlanes).toBeUndefined();
  });

  it('mirrors an earlier build\'s CAD-cut record from its domain planes', async () => {
    const legacy = record('wgi_legacy', null, { cut_planes: [], domain_planes: ['x0'] });
    await showIngestedMeshInViewport(legacy, 'Legacy', undefined, fetcherFor(NEGATIVE_DISPLAY, POSITIVE_SOLVE));
    expect(extentX(importedMeshStore.getSnapshot().cad!.scene)[1]).toBeCloseTo(0.2);
  });
});
