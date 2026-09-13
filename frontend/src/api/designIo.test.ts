import { describe, expect, it, vi } from 'vitest';
import { convertDesignToFreeform, exportGeometryToOutputFolder, freeformFromProfileCsv, hydrateDesignDocument, sendDesignToCad, serializeDesignDocument } from './designIo';
import { designForFamily, serializeDesign } from '../stores/design';

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

describe('FREEFORM conversion of a rolled-back profile', () => {
  // The default R-OSSE meridian (R 140, tmax 1) as the pinned mesher exports
  // it, thinned to its last rows: z peaks at 137.52 mm, then the lip rolls
  // back to the 140 mm mouth at z = 119.71 mm.
  const rolledBack: [number, number][] = [
    [0, 12.7], [40, 31], [80, 58], [120, 92], [131.14, 102.93], [134.97, 110.79],
    [137.15, 118.25], [137.52, 125.08], [135.96, 131], [132.43, 135.72], [126.97, 138.85], [119.71, 140],
  ];
  /** A profile export with one H and one V meridian, each given as [z, r] in mm. */
  const exportCsv = (horizontal: [number, number][], vertical: [number, number][] = horizontal) => [
    '# x_cm;y_cm;z_cm',
    ...horizontal.map(([z, r]) => `${r / 10};0;${z / 10}`),
    '',
    ...vertical.map(([z, r]) => `0;${r / 10};${z / 10}`),
    '',
  ].join('\r\n');
  const increasing = (values: number[]) => values.every((value, index) => index === 0 || value > values[index - 1]);

  it('ends the profile at its fold on a vertical tangent and says what it left out', () => {
    const { design, notice } = freeformFromProfileCsv(exportCsv(rolledBack), designForFamily('R-OSSE'));
    expect(design.length).toBeCloseTo(137.52, 6);
    for (const profile of [design.profile_h!, design.profile_v!]) {
      // Cropping at the mouth's z read the radius on the way out, near 94 mm.
      // The sample 0.37 mm short of the fold is dropped: the editor wants every
      // interior point at least 1 mm from either end.
      expect(profile.points).toHaveLength(7);
      expect(profile.points.at(-2)!.t * design.length!).toBeCloseTo(134.97, 6);
      expect(profile.points.at(-1)!.t).toBe(1);
      expect(profile.points.at(-1)!.r).toBeCloseTo(125.08, 6);
      expect(increasing(profile.points.map((point) => point.t))).toBe(true);
      expect(profile.mouth_angle_deg).toBe(90);
    }
    expect(notice).toBe('The R-OSSE profile rolls back: it reaches z = 137.5 mm, then curls back toward the throat. '
      + 'A FREEFORM profile cannot fold back, so the converted profile ends there and leaves out the rolled-back lip. '
      + 'Mouth radius: horizontal 125.1 mm (source 140.0 mm), vertical 125.1 mm (source 140.0 mm).');
  });

  it('crops the other plane at a shorter fold and keeps its flaring mouth angle', () => {
    const shorter = rolledBack.map(([z, r]): [number, number] => [.9 * z, r]);
    const { design, notice } = freeformFromProfileCsv(exportCsv(rolledBack, shorter), designForFamily('R-OSSE'));
    const length = .9 * 137.52;
    expect(design.length).toBeCloseTo(length, 6);
    expect(design.profile_v!.points.at(-1)!.r).toBeCloseTo(125.08, 6);
    expect(design.profile_v!.mouth_angle_deg).toBe(90);
    // H is cut on its way out, between the samples at z = 120 and 131.14 mm.
    expect(design.profile_h!.points.at(-1)!.t).toBe(1);
    expect(design.profile_h!.points.at(-1)!.r).toBeCloseTo(92 + (length - 120) / (131.14 - 120) * (102.93 - 92), 6);
    expect(design.profile_h!.mouth_angle_deg).toBeCloseTo(Math.atan2(102.93 - 92, 131.14 - 120) * 180 / Math.PI, 6);
    expect(notice).toContain('horizontal 95.7 mm (source 140.0 mm), vertical 125.1 mm (source 140.0 mm)');
  });

  it('converts a profile that never folds as before, without a notice, skipping a repeated z', () => {
    const { design, notice } = freeformFromProfileCsv(exportCsv([[0, 12.7], [50, 30], [50, 30], [100, 80]]), designForFamily('OSSE'));
    expect(notice).toBeUndefined();
    expect(design.length).toBeCloseTo(100, 6);
    expect(design.profile_h!.points.map((point) => point.t)).toEqual([0, expect.closeTo(.5, 6), 1]);
    expect(design.profile_h!.mouth_angle_deg).toBeCloseTo(Math.atan2(50, 50) * 180 / Math.PI, 6);
  });

  it('keeps the fold as the last of at most 64 anchors', () => {
    // 151 rows rise to z = 100 mm, and 50 more roll back.
    const dense = Array.from({ length: 201 }, (_unused, row): [number, number] => [100 * Math.sin(Math.PI * row / 300), 10 + row / 2]);
    const { design } = freeformFromProfileCsv(exportCsv(dense), designForFamily('R-OSSE'));
    expect(design.length).toBeCloseTo(100, 6);
    expect(design.profile_h!.points).toHaveLength(64);
    expect(design.profile_h!.points.at(-1)!.r).toBeCloseTo(85, 6);
    expect(increasing(design.profile_h!.points.map((point) => point.t))).toBe(true);
  });

  it('returns the notice from the whole conversion, with one export for an unmorphed design', async () => {
    const requests: string[] = [];
    const fetcher = async (url: string) => {
      requests.push(url);
      return url.startsWith('/api/export/profiles') ? new Response(exportCsv(rolledBack), { status: 200 }) : new Response('', { status: 404 });
    };
    const { design, notice } = await convertDesignToFreeform(designForFamily('R-OSSE'), fetcher as typeof fetch);
    expect(requests).toEqual(['/api/design/convert?family=FREEFORM', '/api/export/profiles?kind=profiles']);
    expect(design.formula).toBe('FREEFORM');
    expect(design.profile_h!.points.at(-1)!.r).toBeCloseTo(125.08, 6);
    expect(notice).toContain('rolls back');
  });
});

describe('geometry export requests', () => {
  /**
   * A `Response` whose `blob()` hands back *this environment's* `Blob`.
   *
   * `Response.blob()` from Node returns a Node `Blob`, and jsdom's `FormData`
   * accepts only a jsdom one -- so `writeToOutputFolder` threw
   * "parameter 2 is not of type 'Blob'" on CI's Node 20 while passing on a newer
   * local Node, where the two happen to be compatible. In a browser there is only
   * ever one `Blob`, so the mismatch is an artefact of the harness rather than
   * anything the export path does; overriding `blob()` alone keeps `ok`, the
   * headers and `responseFilename` exactly as production sees them.
   */
  function exportResponse(body: string, type: string, warning?: string): Response {
    const response = new Response(body, {
      status: 200,
      headers: { 'Content-Type': type, ...(warning ? { 'X-Export-Warning': warning } : {}) },
    });
    return Object.assign(response, { blob: async () => new Blob([body], { type }) });
  }

  function recordingFetcher(): { urls: string[]; fetcher: typeof fetch } {
    const urls: string[] = [];
    const fetcher = (async (url: string) => {
      urls.push(String(url));
      // The export is written into the output folder, not handed to the
      // browser: an `<a download>` does nothing in the desktop WebView2 window.
      if (String(url) === '/api/workspace/write-export') {
        return new Response(
          JSON.stringify({ directory: 'C:/Output/horn', files: ['horn.step'] }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      return exportResponse('ISO-10303-21;', 'model/step');
    }) as unknown as typeof fetch;
    return { urls, fetcher };
  }

  const design = hydrateDesignDocument({ formula: 'OSSE', L: 120, a: 45 });

  it('asks for the manufacturable solid by default', async () => {
    const { urls, fetcher } = recordingFetcher();
    await exportGeometryToOutputFolder('step', design, 3, 'horn', undefined, undefined, fetcher);
    expect(urls).toEqual(['/api/export/step?body=solid', '/api/workspace/write-export']);
  });

  it('asks for the inner surface when that body is chosen', async () => {
    const { urls, fetcher } = recordingFetcher();
    await exportGeometryToOutputFolder('step', design, 3, 'horn', undefined, 'surface', fetcher);
    expect(urls).toEqual(['/api/export/step?body=surface', '/api/workspace/write-export']);
  });

  it('leaves the other geometry exports unqueried', async () => {
    const { urls, fetcher } = recordingFetcher();
    await exportGeometryToOutputFolder('stl', design, 3, 'horn', undefined, undefined, fetcher);
    await exportGeometryToOutputFolder('profiles', design, 3, 'horn', 'slices', undefined, fetcher);
    expect(urls).toEqual([
      '/api/export/stl', '/api/workspace/write-export',
      '/api/export/profiles?kind=slices', '/api/workspace/write-export',
    ]);
  });

  it('returns an STL warning after the file has been written successfully', async () => {
    const { fetcher } = recordingFetcher();
    const warningFetcher = (async (url: string, init?: RequestInit) => (
      String(url) === '/api/export/stl'
        ? exportResponse('stl', 'application/sla', 'fine detail was coarsened')
        : fetcher(url, init)
    )) as typeof fetch;
    const result = await exportGeometryToOutputFolder(
      'stl', design, 3, 'horn', undefined, undefined, warningFetcher,
    );
    expect(result.warning).toBe('fine detail was coarsened');
    expect(result.directory).toBe('C:/Output/horn');
  });
});

describe('design copy serialization requests', () => {
  it('uses the non-mutating endpoint without a CadLink identity', async () => {
    let url = '';
    let payload: Record<string, unknown> = {};
    const fetcher = (async (requestedUrl: string, init?: RequestInit) => {
      url = requestedUrl;
      payload = JSON.parse(String(init?.body));
      return new Response(JSON.stringify({
        text: 'serialized', suggestedFilename: 'horn.cfg',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }) as typeof fetch;

    await serializeDesignDocument(hydrateDesignDocument({ formula: 'OSSE' }), 'horn', fetcher);
    expect(url).toBe('/api/design/serialize');
    expect(payload).toMatchObject({ filename: 'horn.cfg' });
    expect(payload).not.toHaveProperty('identity');
  });

  it('keeps an imported ATH Mesh.Quadrants value in the serialized copy', async () => {
    let payload: Record<string, unknown> = {};
    const fetcher = (async (_url: string, init?: RequestInit) => {
      payload = JSON.parse(String(init?.body));
      return new Response(JSON.stringify({
        text: 'Mesh.Quadrants = 14\n', suggestedFilename: 'half-y.cfg',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }) as typeof fetch;

    const imported = hydrateDesignDocument({ formula: 'OSSE', mesh: { quadrants: 14 } });
    await serializeDesignDocument(imported, 'half-y', fetcher);
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
        text: 'serialized', suggestedFilename: 'polar.cfg',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }) as typeof fetch;

    await serializeDesignDocument(hydrateDesignDocument({
      formula: 'OSSE',
      extra_blocks: { Report: { items: { Title: '"stale ATH name"', PolarData: 'SPL_H' }, lines: [] } },
    }), 'Polar Study', fetcher, {
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
      { documentId: 'fusion:doc-a', instanceId: 'instance-b', returnStateHash: 'sha256:return-a' },
    );

    expect(calls.map(({ url }) => url)).toEqual([
      '/api/cad-workspace/path', '/api/export/wglink',
    ]);
    expect(new Headers(calls[1].init?.headers).get('Idempotency-Key')).toBe('attempt-1');
    expect(JSON.parse(String(calls[1].init?.body))).toMatchObject({
      designRevision: 12,
      baseName: 'horn',
      identity,
      expectedFusionDocumentId: 'fusion:doc-a',
      expectedFusionInstanceId: 'instance-b',
      expectedFusionReturnStateHash: 'sha256:return-a',
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
