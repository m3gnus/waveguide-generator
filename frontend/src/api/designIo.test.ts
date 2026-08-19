import { describe, expect, it, vi } from 'vitest';
import { downloadGeometryExport, hydrateDesignDocument, saveDesignDocument, sendDesignToCad } from './designIo';
import { serializeDesign } from '../stores/design';

describe('design hydration', () => {
  it('decodes ATH quadrant digits and derives custom zmap sampling', () => {
    const design = hydrateDesignDocument({
      formula: 'OSSE',
      mesh: { quadrants: 14, sampling_mode: null, z_map_points: '0,.2,1' },
    });
    expect(design.quadrants).toEqual([1, 4]);
    expect(design.mesh.quadrants).toBe(14);
    expect(design.mesh.sampling_mode).toBe('zmap');
  });

  it('uses family defaults for nullable imported numeric fields', () => {
    const design = hydrateDesignDocument({
      formula: 'R-OSSE', R: null,
      source: { radius: null, velocity: null },
      simulation: { f1: null },
    });
    expect(design.R).toBe(140);
    expect(design.source.radius).toBe(-1);
    expect(design.source.velocity).toBe(1);
    expect(design.simulation.f1).toBe(400);
    expect(design._absent).toEqual(expect.arrayContaining(['R', 'source.radius', 'source.velocity', 'simulation.f1']));
  });

  it('keeps an absent ATH key null on the wire while displaying its family default', () => {
    const design = hydrateDesignDocument({ formula: 'R-OSSE', morph: { target_shape: 1, target_width: null } });
    expect(design.morph.target_width).toBe(0);
    expect(design._absent).toContain('morph.target_width');
    expect((serializeDesign(design).morph as Record<string, unknown>).target_width).toBeNull();
    expect(serializeDesign(design)).not.toHaveProperty('_absent');
  });

  it('displays ATH\'s wall default while keeping an absent wall thickness null on the wire', () => {
    const design = hydrateDesignDocument({ formula: 'R-OSSE', mesh: { wall_thickness: null } });
    expect(design.mesh.wall_thickness).toBe(5);
    expect(design._absent).toContain('mesh.wall_thickness');
    expect((serializeDesign(design).mesh as Record<string, unknown>).wall_thickness).toBeNull();
  });

  it('preserves raw and evaluated expression forms through hydrate/serialize', () => {
    const design = hydrateDesignDocument({ formula: 'R-OSSE', R: { value: 280, raw: '140 * 2' }, a: { value: null, raw: 'coverage(p)' } });
    expect(design.R).toBe(280);
    expect(design.a).toBe(25);
    expect(design._expressions).toMatchObject({ R: { value: 280, raw: '140 * 2' }, a: { value: null, raw: 'coverage(p)' } });
    expect(serializeDesign(design)).toMatchObject({ R: { value: 280, raw: '140 * 2' }, a: { value: null, raw: 'coverage(p)' } });
  });
});

describe('geometry export requests', () => {
  function recordingFetcher(): { urls: string[]; fetcher: typeof fetch } {
    const urls: string[] = [];
    const fetcher = (async (url: string) => {
      urls.push(String(url));
      return new Response('ISO-10303-21;', {
        status: 200,
        headers: { 'Content-Type': 'model/step' },
      });
    }) as unknown as typeof fetch;
    return { urls, fetcher };
  }

  const design = hydrateDesignDocument({ formula: 'OSSE', L: 120, a: 45 });

  it('asks for the manufacturable solid by default', async () => {
    const { urls, fetcher } = recordingFetcher();
    await downloadGeometryExport('step', design, 3, 'horn', undefined, undefined, fetcher);
    expect(urls).toEqual(['/api/export/step?body=solid']);
  });

  it('asks for the inner surface when that body is chosen', async () => {
    const { urls, fetcher } = recordingFetcher();
    await downloadGeometryExport('step', design, 3, 'horn', undefined, 'surface', fetcher);
    expect(urls).toEqual(['/api/export/step?body=surface']);
  });

  it('leaves the other geometry exports unqueried', async () => {
    const { urls, fetcher } = recordingFetcher();
    await downloadGeometryExport('stl', design, 3, 'horn', undefined, undefined, fetcher);
    await downloadGeometryExport('profiles', design, 3, 'horn', 'slices', undefined, fetcher);
    expect(urls).toEqual(['/api/export/stl', '/api/export/profiles?kind=slices']);
  });
});

describe('design save requests', () => {
  it('sends the document identity as the server CAS token', async () => {
    const identity = {
      designId: 'wgd_01K00000000000000000000000',
      lineageId: 'wgl_01K00000000000000000000000',
      baseEditVersion: 8,
    };
    let payload: Record<string, unknown> = {};
    const fetcher = (async (_url: string, init?: RequestInit) => {
      payload = JSON.parse(String(init?.body));
      return new Response(JSON.stringify({
        text: 'saved', suggestedFilename: 'horn.cfg', identity: { ...identity, baseEditVersion: 9 }, forked: false, from: null,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }) as typeof fetch;

    await saveDesignDocument(hydrateDesignDocument({ formula: 'OSSE' }), 'horn', identity, fetcher);
    expect(payload).toMatchObject({ filename: 'horn.cfg', identity });
  });

  it('keeps an imported ATH Mesh.Quadrants value in the save payload', async () => {
    let payload: Record<string, unknown> = {};
    const fetcher = (async (_url: string, init?: RequestInit) => {
      payload = JSON.parse(String(init?.body));
      return new Response(JSON.stringify({
        text: 'Mesh.Quadrants = 14\n', suggestedFilename: 'half-y.cfg',
        identity: { designId: 'wgd_01K00000000000000000000000', lineageId: 'wgl_01K00000000000000000000000', baseEditVersion: 1 },
        forked: false, from: null,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }) as typeof fetch;

    const imported = hydrateDesignDocument({ formula: 'OSSE', mesh: { quadrants: 14 } });
    await saveDesignDocument(imported, 'half-y', null, fetcher);
    expect(payload).toMatchObject({
      filename: 'half-y.cfg',
      design: { mesh: { quadrants: 14 } },
    });
  });

  it('adds the current directivity sweep as v1-compatible ABEC polar blocks', async () => {
    let payload: Record<string, unknown> = {};
    const fetcher = (async (_url: string, init?: RequestInit) => {
      payload = JSON.parse(String(init?.body));
      return new Response(JSON.stringify({
        text: 'saved', suggestedFilename: 'polar.cfg',
        identity: { designId: 'wgd_01K00000000000000000000000', lineageId: 'wgl_01K00000000000000000000000', baseEditVersion: 1 },
        forked: false, from: null,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }) as typeof fetch;

    await saveDesignDocument(hydrateDesignDocument({
      formula: 'OSSE',
      extra_blocks: { Report: { items: { Title: '"stale ATH name"', PolarData: 'SPL_H' }, lines: [] } },
    }), 'Polar Study', null, fetcher, {
      angle_range: [0, 120, 25], angle_step: 5, distance: 4, norm_angle: 8,
      inclination: 35, enabled_axes: ['horizontal', 'diagonal'],
      observation_origin: 'throat', spherical_sampling: true, field_plane: true,
    });

    expect(payload).toMatchObject({ design: { extra_blocks: {
      // The design's own name replaces whatever Title the file was imported
      // with; every other Report key stays passthrough.
      Report: { items: { Title: '"Polar Study"', PolarData: 'SPL_H' } },
      'ABEC.Polars:SPL_H': { items: { MapAngleRange: '0,120,25', Distance: '4', NormAngle: '8' } },
      'ABEC.Polars:SPL_D': { items: { MapAngleRange: '0,120,25', Distance: '4', NormAngle: '8', Inclination: '35' } },
    } } });
    expect((payload.design as { extra_blocks: Record<string, unknown> }).extra_blocks)
      .not.toHaveProperty('ABEC.Polars:SPL_V');
  });
});

describe('Send to CAD requests', () => {
  const identity = {
    designId: 'wgd_01K00000000000000000000000',
    lineageId: 'wgl_01K00000000000000000000000',
    baseEditVersion: 8,
  };

  it('requires the configured WGLink folder and sends identity with one idempotency key', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const fetcher = (async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      if (url === '/api/cad-workspace/path') return new Response(JSON.stringify({ selected: true, path: '/cad' }));
      return new Response(JSON.stringify({
        bundlePath: '/cad/wglink/horn.wglink', bundleId: 'wgb_1', exportId: 'wge_1', sequence: 4,
        designHash: 'sha256:design', geometryHash: 'sha256:geometry', artifactSha256: 'sha256:step',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }) as typeof fetch;

    const result = await sendDesignToCad(
      hydrateDesignDocument({ formula: 'OSSE' }), 12, 'horn', identity, fetcher, 'attempt-1',
    );

    expect(calls.map(({ url }) => url)).toEqual([
      '/api/cad-workspace/path', '/api/export/wglink',
    ]);
    expect(new Headers(calls[1].init?.headers).get('Idempotency-Key')).toBe('attempt-1');
    expect(JSON.parse(String(calls[1].init?.body))).toMatchObject({
      designRevision: 12, baseName: 'horn', identity,
    });
    expect(result).toMatchObject({ sequence: 4, bundlePath: '/cad/wglink/horn.wglink' });
  });

  it('sends a null identity rather than refusing an unsaved design', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const fetcher = (async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      if (url === '/api/cad-workspace/path') return new Response(JSON.stringify({ selected: true, path: '/cad' }));
      return new Response(JSON.stringify({
        bundlePath: '/cad/wglink/horn.wglink', bundleId: 'wgb_1', exportId: 'wge_1', sequence: 1,
        designHash: 'sha256:design', geometryHash: 'sha256:geometry', artifactSha256: 'sha256:step',
        identity: { ...identity, baseEditVersion: 1 },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }) as typeof fetch;

    const result = await sendDesignToCad(
      hydrateDesignDocument({ formula: 'OSSE' }), 1, 'horn', null, fetcher, 'attempt-1',
    );

    expect(calls.map(({ url }) => url)).toEqual(['/api/cad-workspace/path', '/api/export/wglink']);
    expect(JSON.parse(String(calls[1].init?.body))).toMatchObject({ identity: null });
    expect(result.identity).toEqual({ ...identity, baseEditVersion: 1 });
  });

  it('reports a failed Fusion handoff instead of claiming the bundle is opening', async () => {
    const fetcher = (async (url: string) => {
      if (url === '/api/cad-workspace/path') return new Response(JSON.stringify({ selected: true, path: '/cad' }));
      return new Response(JSON.stringify({
        bundlePath: '/cad/wglink/horn.wglink', bundleId: 'wgb_1', exportId: 'wge_1', sequence: 1,
        designHash: 'sha256:design', geometryHash: 'sha256:geometry', artifactSha256: 'sha256:step',
        cadHandoff: 'failed', cadLaunch: false,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }) as typeof fetch;

    await expect(sendDesignToCad(
      hydrateDesignDocument({ formula: 'OSSE' }), 1, 'horn', null, fetcher, 'attempt-1',
    )).rejects.toThrow('bundle was exported, but WG could not notify Fusion');
  });

  it('routes an unconfigured Fusion connection to Settings instead of opening a surprise picker', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ selected: false, path: null }), { status: 200 }),
    );
    await expect(sendDesignToCad(
      hydrateDesignDocument({ formula: 'OSSE' }), 1, 'horn', null, fetcher, 'attempt-1',
    )).rejects.toThrow('Settings → CAD Link');
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledWith('/api/cad-workspace/path');
  });
});
