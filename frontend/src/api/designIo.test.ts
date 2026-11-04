import { activityStore } from '../shell/activity';
import { readFileSync } from 'node:fs';
import { failureWords, keepRefusalOriginals, PLAIN_REQUEST_FAILURE } from './httpError';
import { describe, expect, it, vi } from 'vitest';
import { chooseDesignFile, exportGeometryToOutputFolder, hydrateDesignDocument, inspectDesignText, openDesignText, resolveDesignExpressions, sendDesignToCad, serializeDesignDocument } from './designIo';
import { designForFamily, serializeDesign } from '../stores/design';
import { setAnchorAngle } from '../design/customProfileEdit';
import { expectNoJargon } from '../testing/expectNoJargon';

const athRefusalCases: { name: string; text: string; message: string }[] = JSON.parse(
  new TextDecoder().decode(readFileSync('../shared/ath-refusal-fixtures.json')),
);

it('requests constants using the complete saved source and dialect without changing it', async () => {
  const design = designForFamily('OSSE');
  design._expressions = { a0: { raw: '10 + log(100)', value: null, dialect: 'wg-v1' } };
  design._absent = ['scale'];
  const before = JSON.stringify(serializeDesign(design));
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ a0: 12, scale: 1 }), { status: 200 }));
  expect(await resolveDesignExpressions(design, ['a0', 'scale'], fetcher)).toEqual({ a0: 12, scale: 1 });
  expect(fetcher).toHaveBeenCalledExactlyOnceWith('/api/design/resolve-expressions', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ design: JSON.parse(before), paths: ['a0', 'scale'] }),
  });
  expect(JSON.stringify(serializeDesign(design))).toBe(before);
});

it.each([
  [openDesignText, 'Opening design file'], [inspectDesignText, 'Inspecting design file'],
] as const)('tracks %s through response decoding and clears progress on failure', async (open, label) => {
  activityStore.reset();
  let resolve!: (value: unknown) => void;
  const decoded = new Promise(done => { resolve = done; });
  const opening = open('OSSE = {}', async () => ({ ok: true, json: () => decoded }) as Response);
  expect(activityStore.getSnapshot()).toMatchObject([{ kind: 'design-open', label }]);
  await Promise.resolve();
  expect(activityStore.getSnapshot()).toHaveLength(1);
  resolve({ design: {} });
  await opening;
  expect(activityStore.getSnapshot()).toHaveLength(0);
  await expect(open('invalid', async () => Response.json({ detail: 'Choose a valid design file.' }, { status: 422 })))
    .rejects.toThrow('Choose a valid design file.');
  expect(activityStore.getSnapshot()).toHaveLength(0);
});

it('tracks native design file reading through response decoding', async () => {
  activityStore.reset();
  let resolve!: (value: Response) => void;
  const response = new Promise<Response>(done => { resolve = done; });
  const choosing = chooseDesignFile(() => response);
  expect(activityStore.getSnapshot()).toMatchObject([{ kind: 'design-open', label: 'Opening design file' }]);
  resolve(Response.json({ selected: true, filename: 'design.cfg', text: 'OSSE = {}' }));
  expect(await choosing).toMatchObject({ selected: true, filename: 'design.cfg' });
  expect(activityStore.getSnapshot()).toHaveLength(0);
});

async function untouchedRoundTrip(original: Record<string, unknown>): Promise<Record<string, unknown>> {
  let saved: Record<string, unknown> | undefined;
  const fetcher = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    if (String(url) === '/api/design/open') {
      return new Response(JSON.stringify({ design: saved ?? original }), { status: 200 });
    }
    if (String(url) === '/api/design/serialize') {
      saved = JSON.parse(String(init?.body)).design as Record<string, unknown>;
      return new Response(JSON.stringify({ text: 'saved.cfg', suggestedFilename: 'saved.cfg' }), { status: 200 });
    }
    throw new Error(`Unexpected request ${String(url)}`);
  }) as typeof fetch;
  const opened = await openDesignText('old.cfg', fetcher);
  const document = hydrateDesignDocument(opened.design);
  const file = await serializeDesignDocument(document, 'saved', fetcher, undefined, null, '');
  return serializeDesign(hydrateDesignDocument((await openDesignText(file.text, fetcher)).design));
}

describe('design hydration', () => {
  it('keeps an imported ATH Dim box on save', () => {
    const wire = { ...serializeDesign(designForFamily('OSSE')) } as Record<string, unknown>;
    wire.enclosure = { ...(wire.enclosure as Record<string, unknown>), depth: 300, dim_mm: [150, 160, 150, 400] };
    const document = hydrateDesignDocument(wire);
    expect(document.enclosure.dim_mm).toEqual([150, 160, 150, 400]);
    expect((serializeDesign(document).enclosure as Record<string, unknown>).dim_mm).toEqual([150, 160, 150, 400]);
    expect(serializeDesign(designForFamily('OSSE')).enclosure).not.toHaveProperty('dim_mm');
  });

  it('reopens a saved assembly unchanged and leaves a horn-only design without one', async () => {
    const assembly = {
      hf_source: { radiator_id: 'horn-throat', kind: 'classic' },
      woofers: [{ id: '00000000-0000-4000-8000-0000000000aa', label: 'Woofer 1', radius_mm: 80,
        shape: { kind: 'ath_cone' }, placement: { anchor: 'above', gap_mm: 10 }, ath: { key: 'A' } }],
      signal_groups: [{ id: '00000000-0000-4000-8000-0000000000b1', label: 'All',
        members: [{ radiator_id: 'horn-throat' }, { radiator_id: '00000000-0000-4000-8000-0000000000aa', delay_ms: 0.5 }] }],
    };
    const wire = { ...serializeDesign(designForFamily('R-OSSE')), assembly };
    expect((await untouchedRoundTrip(wire)).assembly).toEqual(assembly);
    expect(hydrateDesignDocument(serializeDesign(designForFamily('R-OSSE'))).assembly).toBeUndefined();
  });

  it.each(['R-OSSE', 'OSSE', 'ICW', 'FREEFORM'] as const)(
    'uses the new sweep defaults for omitted or null fields in %s',
    (formula) => {
      for (const fields of [{}, { simulation: { f1: null, f2: null, num_frequencies: null } }]) {
        const design = hydrateDesignDocument({ formula, ...fields });
        expect(design.simulation).toMatchObject({ f1: 50, f2: 16_000, num_frequencies: 32 });
      }
      const explicit = hydrateDesignDocument({ formula, simulation: { f1: 400, f2: 8000, num_frequencies: 20 } });
      expect(explicit.simulation).toMatchObject({ f1: 400, f2: 8000, num_frequencies: 20 });
    },
  );

  it('preserves all advanced ICW inputs across hydration and wire export', () => {
    const seed = { type: 'OSSE', nested: { value: 7, raw: 'kept verbatim' }, L: 120 };
    const advanced = { r_aperture: 110, x_aperture: 120, x_setback: 10, kappa0: -.001,
      kappa_abs_max: .05, dkappa_ds_abs_max: .1, theta_max_deg: 170, pin_mouth_radius: false,
      icw_seed: seed, icw_coeffs: [0, '0.001', .002, 0], icw_S: 180 };
    const design = hydrateDesignDocument({ formula: 'ICW', R: null, ...advanced });
    expect(design.R).toBeUndefined();
    expect(serializeDesign(design)).toMatchObject({ ...advanced, R: null });
    const empty = serializeDesign(hydrateDesignDocument({ formula: 'ICW' }));
    for (const key of Object.keys(advanced)) expect(empty).not.toHaveProperty(key);
  });

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
    expect(design.simulation.f1).toBe(50);
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

  it('opens, saves untouched, and reopens a legacy FREEFORM with expressions and absent fields', async () => {
    const profile = {
      points: [
        { t: 0, r: 12.7 },
        { t: { value: 1 / 3, raw: '1 / 3' }, r: { value: 30, raw: '15 * 2' } },
        { t: 1, r: 140 },
      ],
      throat_angle_deg: 15.5,
      mouth_angle_deg: 60,
    };
    const original = {
      formula: 'FREEFORM', length: { value: 120, raw: '60 * 2' },
      profile_h: profile, profile_v: structuredClone(profile),
      cross_sections: [{ t: 0, shape: 'circle' }, { t: 1, shape: 'ellipse' }],
      mesh: { wall_thickness: null }, scale: null,
    };
    const wire = await untouchedRoundTrip(original);
    expect(wire.length).toEqual(original.length);
    expect((wire.profile_h as typeof profile).points[1]).toEqual(profile.points[1]);
    expect((wire.profile_v as typeof profile).points[1]).toEqual(profile.points[1]);
    expect((wire.mesh as Record<string, unknown>).wall_thickness).toBeNull();
    expect(wire).not.toHaveProperty('shared_profile');
    expect((wire.profile_h as typeof profile).points[1]).not.toHaveProperty('handle_in');
    expect((wire.profile_h as typeof profile).points[1]).not.toHaveProperty('handle_out');
  });

  it('opens, saves untouched, and reopens legacy LOOKUP without converting its table', async () => {
    const original = {
      formula: 'LOOKUP', lookup_profile: [[0, 12.7], [40, 30], [120, 140]],
      a0: { value: 22, raw: '11 * 2' }, scale: null, mesh: { wall_thickness: null },
    };
    const wire = await untouchedRoundTrip(original);
    expect(wire.formula).toBe('LOOKUP');
    expect(wire.lookup_profile).toEqual(original.lookup_profile);
    expect(wire.a0).toEqual(original.a0);
    expect(wire.scale).toBeNull();
    expect((wire.mesh as Record<string, unknown>).wall_thickness).toBeNull();
    expect(wire).not.toHaveProperty('shared_profile');
    expect(wire).not.toHaveProperty('profile_h');
  });

  it('opens, saves untouched, and reopens a Custom profile with shared mode, joins, angles and handles', async () => {
    const h = {
      points: [
        { t: 0, r: 12.7, join: 'corner', handle_out: [5.25, 1.5] },
        { t: 1 / 3, r: 33.316727208506954, join: 'corner', angle_deg: 20, angle_out_deg: 35 },
        { t: 0.6666666666666666, r: 75.57363549552346, handle_in: [-4.123456789012345, -2.5], handle_out: [8.24691357802469, 5] },
        { t: 1, r: 140, angle_deg: 60 },
      ],
      throat_angle_deg: 15.5, mouth_angle_deg: 60,
    };
    // The inactive V is retained exactly, including a throat that differs from H.
    const v = { points: [{ t: 0, r: 14 }, { t: .5, r: 50 }, { t: 1, r: 100 }], throat_angle_deg: 12, mouth_angle_deg: 45 };
    for (const shared of [true, false]) {
      const original = {
        formula: 'FREEFORM', length: 120, shared_profile: shared,
        profile_h: h, profile_v: shared ? v : { ...v, points: [{ t: 0, r: 12.7 }, ...v.points.slice(1)] },
        cross_sections: [{ t: 0, shape: 'circle' }, { t: 1, shape: 'ellipse' }],
      };
      const wire = await untouchedRoundTrip(original);
      expect(wire.shared_profile).toBe(shared);
      expect(wire.profile_h).toEqual(h);
      expect(wire.profile_v).toEqual(original.profile_v);
    }
  });

  it('keeps every legacy FREEFORM absence when its table is edited, and never infers shared mode', async () => {
    const profile = { points: [{ t: 0, r: 12.7 }, { t: .5, r: 60 }, { t: 1, r: 140 }], throat_angle_deg: 15.5, mouth_angle_deg: 60 };
    const legacy = hydrateDesignDocument({
      formula: 'FREEFORM', length: { value: 120, raw: '60 * 2' }, profile_h: profile, profile_v: structuredClone(profile),
      cross_sections: [{ t: 0, shape: 'circle' }, { t: 1, shape: 'ellipse' }], mesh: { wall_thickness: null }, scale: null,
    });
    const edited = setAnchorAngle(legacy, 'H', 1, 'in', 25).after;
    const wire = serializeDesign(edited);
    expect(wire).not.toHaveProperty('shared_profile');
    expect(wire.length).toEqual({ value: 120, raw: '60 * 2' });
    expect(wire.scale).toBeNull();
    expect((wire.mesh as Record<string, unknown>).wall_thickness).toBeNull();
    expect((wire.profile_h as typeof profile).points[1]).toEqual({ t: .5, r: 60, angle_deg: 25 });
    expect(wire.profile_v).toEqual(profile);
  });

  it('keeps legacy sidecars inert while preserving executable wire expressions', () => {
    const legacy = { R: { value: null, raw: '70*(1+cos(p))' },
      'mesh.throat_resolution': { value: 6, raw: '3*2' } };
    const wireR = { value: null, raw: '90*(1+cos(p))' };
    for (const R of [140, wireR]) {
      const design = hydrateDesignDocument({ formula: 'OSSE', R, _expressions: legacy });
      expect(design._expressions).toEqual({
        '_expressions.R': legacy.R,
        '_expressions.mesh.throat_resolution': legacy['mesh.throat_resolution'],
        ...(R === 140 ? {} : { R: wireR }),
      });
      expect(serializeDesign(design)).toMatchObject({ R,
        mesh: { throat_resolution: 6 } });
      expect(serializeDesign(design)).not.toHaveProperty('_expressions');
    }
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
  it('reports the server validation reason without writing a design', async () => {
    const fetcher = (async () => new Response(JSON.stringify({ detail: [
      { loc: ['design', 'a'], msg: 'Value error, Body coverage angle must be below 80 degrees' },
    ] }), { status: 422, headers: { 'Content-Type': 'application/json' } })) as typeof fetch;
    await expect(serializeDesignDocument(designForFamily('OSSE'), 'horn', fetcher))
      .rejects.toThrow('Body coverage angle must be below 80 degrees');
  });

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

it.each(['stl', 'step'] as const)('Stop cancels %s from the design menu before any output-folder write', async kind => {
  activityStore.reset();
  const urls: string[] = [];
  const fetcher: typeof fetch = (url, init) => {
    urls.push(String(url));
    const signal = init!.signal!;
    return new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(signal.reason), { once: true });
    });
  };
  const pending = exportGeometryToOutputFolder(kind, designForFamily('OSSE'), 1, 'horn', undefined, 'solid', fetcher);
  const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  activityStore.getSnapshot()[0].cancel!();
  await rejected;
  expect(urls).toEqual([kind === 'step' ? '/api/export/step?body=solid' : '/api/export/stl']);
  expect(activityStore.getSnapshot()).toEqual([]);
});

describe('Send to CAD requests', () => {
  const identity = {
    designId: 'wgd_01K00000000000000000000000',
    lineageId: 'wgl_01K00000000000000000000000',
    baseEditVersion: 8,
  };

  it.each(['ack first', 'POST first'] as const)('Stop aborts the Send before any delivery result is adopted (%s)', async order => {
    activityStore.reset();
    let signal: AbortSignal | null | undefined;
    let finishPost!: (response: Response) => void;
    const fetcher: typeof fetch = async (url, init) => {
      if (url === '/api/cad-workspace/path') return new Response(JSON.stringify({ selected: true }));
      if (init?.method === 'DELETE') {
        if (order === 'ack first') return new Response(null, { status: 204 });
        finishPost(new Response(JSON.stringify({ detail: 'Operation was cancelled.' }), { status: 499 }));
        return new Promise((_resolve, reject) => init.signal!.addEventListener('abort', () => reject(init.signal!.reason), { once: true }));
      }
      signal = init?.signal;
      return new Promise((resolve, reject) => {
        finishPost = resolve;
        signal?.addEventListener('abort', () => reject(signal?.reason), { once: true });
      });
    };
    const pending = sendDesignToCad(designForFamily('OSSE'), 1, 'horn', null, fetcher, 'stop-send');
    const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    await vi.waitFor(() => expect(signal).toBeDefined());
    activityStore.getSnapshot()[0].cancel!();
    await rejected;
    expect(signal?.aborted).toBe(true);
    expect(activityStore.getSnapshot()).toEqual([]);
    expect(activityStore.typicalMs('send-to-cad')).toBeNull();
  });

  it.each(['headers', 'body'].flatMap(phase => ['deadline', 'Stop'].map(via => ({ phase, via }))))('the preliminary workspace read honours $via while waiting for $phase', async ({ phase, via }) => {
    vi.useFakeTimers();
    // Exercise the WebView fallback timer so fake time also drives the deadline.
    const timeout = AbortSignal.timeout;
    Object.defineProperty(AbortSignal, 'timeout', { value: undefined, configurable: true });
    activityStore.reset();
    try {
      const fetcher = vi.fn<typeof fetch>(async (_url, init) => {
        const wait = () => new Promise<never>((_resolve, reject) => {
          const signal = init!.signal!;
          signal.addEventListener('abort', () => reject(signal.reason), { once: true });
        });
        if (phase === 'headers') return wait();
        return { ok: true, json: wait } as unknown as Response;
      });
      const pending = sendDesignToCad(designForFamily('OSSE'), 1, 'horn', null, fetcher, 'deadline-send');
      const result = pending.catch(error => error);
      if (via === 'Stop') {
        await vi.advanceTimersByTimeAsync(0);
        activityStore.getSnapshot()[0].cancel!();
        await vi.advanceTimersByTimeAsync(0);
      } else await vi.advanceTimersByTimeAsync(30_000);
      expect(await Promise.race([result, Promise.resolve('still waiting')])).toMatchObject({ name: via === 'Stop' ? 'AbortError' : 'TimeoutError' });
      expect(fetcher).toHaveBeenCalledOnce();
      expect(activityStore.getSnapshot()).toEqual([]);
    } finally {
      activityStore.getSnapshot()[0]?.cancel?.();
      Object.defineProperty(AbortSignal, 'timeout', { value: timeout, configurable: true });
      vi.useRealTimers();
    }
  });

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

  it.each(['/cad/wglink/horn.wglink', 'C:\\CAD Files\\horn.wglink'])('reports where the model is after a failed Fusion handoff: %s', async (savedPath) => {
    const fetcher = (async (url: string) => {
      if (url === '/api/cad-workspace/path') return new Response(JSON.stringify({ selected: true, path: '/cad' }));
      return new Response(JSON.stringify({
        bundlePath: savedPath, bundleId: 'wgb_1', exportId: 'wge_1', sequence: 1,
        designHash: 'sha256:design', geometryHash: 'sha256:geometry', artifactSha256: 'sha256:step',
        cadHandoff: 'failed', cadLaunch: false,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }) as typeof fetch;

    const send = sendDesignToCad(
      hydrateDesignDocument({ formula: 'OSSE' }), 1, 'horn', null, fetcher, 'attempt-1',
    );
    await expect(send).rejects.toThrow(`WG saved the model in ${savedPath}, but could not notify Fusion. Open waveguide.step from that folder in Fusion.`);
    const error = await send.catch((reason: Error) => reason);
    expectNoJargon((error as Error).message, { names: [savedPath] });
  });

  it('routes an unconfigured Fusion connection to Settings instead of opening a surprise picker', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ selected: false, path: null }), { status: 200 }),
    );
    await expect(sendDesignToCad(
      hydrateDesignDocument({ formula: 'OSSE' }), 1, 'horn', null, fetcher, 'attempt-1',
    )).rejects.toThrow('Settings → CAD Link');
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledWith('/api/cad-workspace/path', { signal: expect.any(AbortSignal) });
  });
});

it('downloads a LOOKUP native copy with a .mwg filename', async () => {
  const fetcher = vi.fn(async (_path: RequestInfo | URL, _options?: RequestInit) => new Response(JSON.stringify({ text: 'native', suggestedFilename: 'lookup.mwg' }), { status: 200 }));
  await serializeDesignDocument(designForFamily('LOOKUP'), 'lookup', fetcher, undefined, null);
  const request = JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body));
  expect(request.filename).toBe('lookup.mwg');
  expect(request.design.lookup_profile).toEqual([[0, 12.7], [120, 140]]);
});

it('uses an explicitly supplied CAD document uid and never borrows the live document uid', async () => {
  const { useDocumentStore } = await import('../stores/document');
  useDocumentStore.getState().setDesignUid('wgu_01K00000000000000000000000');
  const payloads: Record<string, unknown>[] = [];
  const fetcher = (async (url, init) => {
    if (String(url) === '/api/export/wglink') {
      payloads.push(JSON.parse(String(init?.body)));
      return Response.json({ sequence: 1 });
    }
    return Response.json({ selected: true, directory: '/workspace', available: true });
  }) as typeof fetch;
  const historical = 'wgu_01K00000000000000000000001';
  await sendDesignToCad(designForFamily('OSSE'), 1, 'run', null, fetcher, 'history-1', null, undefined, null, historical);
  await sendDesignToCad(designForFamily('OSSE'), 1, 'old-run', null, fetcher, 'history-2');
  expect(payloads[0].designUid).toBe(historical);
  expect(payloads[1]).not.toHaveProperty('designUid');
});


describe('native Open and browser fallback', () => {
  it('returns a chosen native file and explicit cancellation', async () => {
    for (const chosen of [
      { selected: true, outcome: 'chosen', filename: 'horn.cfg', source: '/horn.cfg', text: 'cfg' },
      { selected: false, outcome: 'cancelled' },
    ]) {
      expect(await chooseDesignFile(vi.fn(async () => Response.json(chosen)))).toEqual(chosen);
    }
  });

  it.each([404, 405, 422, 500, 503])('falls back to browser Open on HTTP %s', async (status) => {
    expect(await chooseDesignFile(vi.fn(async () => new Response('', { status })))).toBeNull();
  });

  it.each([
    { selected: false, outcome: 'unavailable' }, { selected: false },
    { selected: true }, null,
  ])('falls back unless the reply is a valid chosen file or explicit cancel: %j', async (reply) => {
    expect(await chooseDesignFile(vi.fn(async () => Response.json(reply)))).toBeNull();
  });

  it('falls back on transport or reply decoding errors', async () => {
    expect(await chooseDesignFile(vi.fn().mockRejectedValue(new Error('offline')))).toBeNull();
    expect(await chooseDesignFile(vi.fn(async () => new Response('not json')))).toBeNull();
  });
});

// Both sides assert the same response contract; these inputs are the review's
// exact files, not a second implementation of the ATH wording rules.
describe('ATH refusal display', () => {
  it.each(athRefusalCases)('$name names the source setting through the final catch', async ({ text, message }) => {
    const fetcher = vi.fn(async () => Response.json({
      detail: { type: 'parse_error', message },
    }, { status: 422 }));
    let shown = '';
    try {
      await openDesignText(text, fetcher);
    } catch (error) {
      shown = failureWords(error);
    }
    expect(shown).toBe(message);
    expect(fetcher).toHaveBeenCalledWith('/api/design/open', expect.objectContaining({ body: text }));
  });
});


it('keeps unquoted internal names private even on a design parse refusal', async () => {
  const fetcher = vi.fn(async () => Response.json({
    detail: { type: 'parse_error', message: 'Source.InternalState cannot use mesh.private_value.' },
  }, { status: 422 }));
  let shown = '';
  try {
    await openDesignText('OSSE = {\nL=80\n}\n', fetcher);
  } catch (error) {
    shown = failureWords(error);
  }
  expect(shown).toBe(PLAIN_REQUEST_FAILURE);
});


it.each(['stl', 'step', 'send'] as const)('Stop while reading a refused %s body stays an abort and records no problem', async kind => {
  activityStore.reset();
  const recorded = vi.fn();
  keepRefusalOriginals(recorded);
  let reading = false;
  const fetcher: typeof fetch = async (url, init) => {
    if (url === '/api/cad-workspace/path') return Response.json({ selected: true });
    if (init?.method === 'DELETE') return new Response(null, { status: 204 });
    const response = new Response(null, { status: 422 });
    response.json = () => new Promise((_resolve, reject) => {
      reading = true;
      const signal = init!.signal!;
      signal.addEventListener('abort', () => reject(signal.reason), { once: true });
    });
    return response;
  };
  try {
    const design = designForFamily('OSSE');
    const pending = kind === 'send' ? sendDesignToCad(design, 1, 'horn', null, fetcher)
      : exportGeometryToOutputFolder(kind, design, 1, 'horn', undefined, 'solid', fetcher);
    const stopped = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    await vi.waitFor(() => expect(reading).toBe(true));
    activityStore.getSnapshot()[0].cancel!();
    await stopped;
    expect(recorded).not.toHaveBeenCalled();
    expect(activityStore.getSnapshot()).toEqual([]);
  } finally { keepRefusalOriginals(() => {}); }
});
