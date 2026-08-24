import { describe, expect, it } from 'vitest';
import meshFixture from '../viewport/test-fixtures/tagged_sources-small.msh?raw';
import { postSolverMesh, solverMeshArtifactToken, solverMeshScene, type SolverMeshResult } from './solverMesh';

const result: SolverMeshResult = {
  msh_text: meshFixture,
  stats: {
    triangle_count: 12,
    vertex_count: 9,
    warnings: [],
    mesh_cache_key: 'abc123',
    mesh_cache_hit: false,
  },
  cut_planes: ['x0', 'y0'],
  quadrants: 1,
};

function fetcherReturning(body: unknown, ok = true, status = 200): typeof fetch {
  return async () => ({
    ok,
    status,
    statusText: ok ? 'OK' : 'Unprocessable Entity',
    json: async () => body,
  }) as Response;
}

describe('postSolverMesh', () => {
  it('returns a validated result and posts the given body verbatim', async () => {
    let sent: RequestInit | undefined;
    const fetcher: typeof fetch = async (_input, init) => {
      sent = init;
      return {
        ok: true, status: 200, statusText: 'OK', json: async () => result,
      } as Response;
    };
    const received = await postSolverMesh('{"design":{}}', fetcher);
    expect(received.stats.mesh_cache_key).toBe('abc123');
    expect(sent?.method).toBe('POST');
    expect(sent?.body).toBe('{"design":{}}');
  });

  it('rejects a malformed payload rather than rendering from it', async () => {
    await expect(postSolverMesh('{}', fetcherReturning({ msh_text: 'x' })))
      .rejects.toThrow('Solver mesh response is invalid');
  });

  it('surfaces the server refusal detail', async () => {
    await expect(postSolverMesh(
      '{}',
      fetcherReturning({ detail: 'Solver mesh exceeds the ceiling' }, false, 422),
    )).rejects.toThrow('Solver mesh exceeds the ceiling');
  });
});

describe('solverMeshScene', () => {
  it('parses the artifact and reconstructs the full model across the cut planes', () => {
    const scene = solverMeshScene('Simulation mesh', result);
    expect(scene.source).toBe('solver');
    expect(scene.artifactToken).toBe(solverMeshArtifactToken('abc123'));
    expect(scene.solvedTriangleCount).toBe(12);
    // Two origin cuts mirror the reduced domain up to fourfold; triangles on a
    // cut plane are not duplicated, so the display count lies in between.
    expect(scene.triangleCount).toBeGreaterThan(0);
    const parsedTriangles = scene.scene.surfaces
      .filter((surface) => surface.solvedDomain)
      .reduce((count, surface) => count + surface.indices.length / 3, 0);
    expect(scene.triangleCount).toBeGreaterThanOrEqual(parsedTriangles);
    // The solver's reduced domain stays marked; mirrored copies are display-only.
    expect(scene.scene.surfaces.some((surface) => surface.solvedDomain)).toBe(true);
    expect(scene.scene.surfaces.some((surface) => !surface.solvedDomain)).toBe(true);
  });
});
