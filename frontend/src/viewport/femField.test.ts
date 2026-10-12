import { describe, expect, it } from 'vitest';
import { buildFemFieldGeometries } from './FemFieldView';
import {
  clippedGhostEdges,
  decodeFemFieldPayload,
  elementMeanSlice,
  femFieldColors,
  femNormalVelocitySlice,
  femSurfaceKind,
  FEM_ROLE_COLORS,
  sliceFemField,
  type FemFieldDomain,
  type FemFieldPayload,
} from './femField';

describe('FEM field entry points', () => {
  it('keeps the view and decoder behind the result scene and API boundary', () => {
    // F9 enables the selected-result scene. Other production components must
    // still go through that scene and the validated result API.
    const sources = import.meta.glob('../**/*.{ts,tsx}', {
      query: '?raw', import: 'default', eager: true,
    });
    const consumers = Object.entries(sources).filter(([path]) => (
      !path.includes('.test.') && !path.endsWith('/FemFieldView.tsx') && !path.endsWith('/femField.ts')
      && !path.endsWith('/FemFieldScene.tsx')
    ));
    expect(consumers.length).toBeGreaterThan(0);
    for (const [path, source] of consumers) {
      const runtimeSource = String(source).replace(/^import type[^\n]*\n/gm, '');
      expect(runtimeSource, path).not.toMatch(/\bFemFieldView\b|['"](?:\.\/|\.\.\/viewport\/)femField['"]/);
    }
  });
});

// A 10 mm cube cut into six tetrahedra around its main diagonal (0 -> 6).
const CORNERS = [
  [0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0],
  [0, 0, 10], [10, 0, 10], [10, 10, 10], [0, 10, 10],
];
const TETS = [
  [0, 1, 2, 6], [0, 2, 3, 6], [0, 3, 7, 6], [0, 7, 4, 6], [0, 4, 5, 6], [0, 5, 1, 6],
];

function cube(): FemFieldDomain {
  return {
    regionId: 'cube',
    points: Float32Array.from(CORNERS.flat()),
    tetrahedra: Uint32Array.from(TETS.flat()),
    surfaces: [
      { name: 'wg-fem-v1|region=cube|group=cone|kind=moving', kind: 'moving', triangles: Uint32Array.from([0, 2, 1, 0, 3, 2]) },
    ],
  };
}

/** p = x + i (2y - z): linear, so P1 interpolation must be exact. */
function linear(domain: FemFieldDomain) {
  const count = domain.points.length / 3;
  const real = new Float32Array(count);
  const imag = new Float32Array(count);
  for (let vertex = 0; vertex < count; vertex += 1) {
    real[vertex] = domain.points[vertex * 3];
    imag[vertex] = 2 * domain.points[vertex * 3 + 1] - domain.points[vertex * 3 + 2];
  }
  return { real, imag };
}

function sliceArea(positions: Float32Array): number {
  let area = 0;
  for (let offset = 0; offset < positions.length; offset += 9) {
    const ax = positions[offset + 3] - positions[offset];
    const ay = positions[offset + 4] - positions[offset + 1];
    const az = positions[offset + 5] - positions[offset + 2];
    const bx = positions[offset + 6] - positions[offset];
    const by = positions[offset + 7] - positions[offset + 1];
    const bz = positions[offset + 8] - positions[offset + 2];
    area += Math.hypot(ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx) / 2;
  }
  return area;
}

describe('sliceFemField', () => {
  it('covers the whole cross-section and reproduces a linear field exactly', () => {
    const domain = cube();
    const values = linear(domain);
    for (const z of [2.5, 5, 7.25]) {
      const slice = sliceFemField(domain, values, { origin: [0, 0, z], normal: [0, 0, 1] });
      expect(sliceArea(slice.positions)).toBeCloseTo(100, 4);
      for (let vertex = 0; vertex < slice.real.length; vertex += 1) {
        const x = slice.positions[vertex * 3];
        const y = slice.positions[vertex * 3 + 1];
        expect(slice.positions[vertex * 3 + 2]).toBeCloseTo(z, 5);
        expect(slice.real[vertex]).toBeCloseTo(x, 4);
        expect(slice.imag[vertex]).toBeCloseTo(2 * y - z, 4);
      }
    }
  });

  it('cuts along an oblique plane with triangles facing the normal', () => {
    const domain = cube();
    const normal: [number, number, number] = [1, 1, 1];
    const slice = sliceFemField(domain, linear(domain), { origin: [5, 5, 5], normal });
    // The plane through the centre normal to the diagonal is a regular hexagon.
    expect(sliceArea(slice.positions)).toBeCloseTo((3 * Math.sqrt(3) / 2) * (10 / Math.sqrt(2)) ** 2, 3);
    for (let offset = 0; offset < slice.positions.length; offset += 9) {
      const p = Array.from(slice.positions.slice(offset, offset + 9));
      const e1 = [p[3] - p[0], p[4] - p[1], p[5] - p[2]];
      const e2 = [p[6] - p[0], p[7] - p[1], p[8] - p[2]];
      const cross = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]];
      expect(cross[0] + cross[1] + cross[2]).toBeGreaterThan(0);
    }
  });

  it('returns nothing for a plane that misses the region', () => {
    const domain = cube();
    const slice = sliceFemField(domain, linear(domain), { origin: [0, 0, 20], normal: [0, 0, 1] });
    expect(slice.positions.length).toBe(0);
  });

  it('refuses values that do not match the vertices', () => {
    const domain = cube();
    expect(() => sliceFemField(domain, { real: new Float32Array(2), imag: new Float32Array(2) }, {
      origin: [0, 0, 5], normal: [0, 0, 1],
    })).toThrow('one complex value per FEM vertex');
  });
});

describe('element field and ghost', () => {
  it('colours each slice triangle with its tetrahedron mean', () => {
    const domain = cube();
    const values = linear(domain);
    const slice = sliceFemField(domain, values, { origin: [0, 0, 5], normal: [0, 0, 1] });
    const mean = elementMeanSlice(domain, values, slice);
    for (let triangle = 0; triangle < slice.tetrahedron.length; triangle += 1) {
      const corners = TETS[slice.tetrahedron[triangle]];
      const expected = corners.reduce((sum, vertex) => sum + CORNERS[vertex][0], 0) / 4;
      expect(mean.real[triangle * 3]).toBeCloseTo(expected, 5);
      expect(mean.real[triangle * 3 + 2]).toBeCloseTo(expected, 5);
    }
  });

  it('draws each kept edge once', () => {
    const domain = cube();
    // Every tetrahedron touches z >= 5, so all 19 edges of the split cube are kept.
    expect(clippedGhostEdges(domain, { origin: [0, 0, 5], normal: [0, 0, 1] }).length).toBe(19 * 6);
    expect(clippedGhostEdges(domain, { origin: [0, 0, 20], normal: [0, 0, 1] }).length).toBe(0);
  });
});

describe('colours, kinds and payload', () => {
  it('maps the loudest vertex to the top of the SPL window', () => {
    const values = { real: Float32Array.from([1, 0.001]), imag: Float32Array.from([0, 0]) };
    const { colors, window } = femFieldColors(values, 'spl', { sequential: ['#000000', '#ffffff'] });
    expect(window.maximum).toBeCloseTo(20 * Math.log10(1 / 2e-5), 5);
    expect(colors[0]).toBeCloseTo(1, 5);
    expect(colors[3]).toBeLessThan(0.1);
  });

  it('reads the kind from the physical name', () => {
    expect(femSurfaceKind('wg-fem-v1|region=a|group=port|kind=interface')).toBe('interface');
    expect(femSurfaceKind('wg-fem-v1|region=a|group=end|kind=plane-wave-termination')).toBe('plane-wave-termination');
    expect(femSurfaceKind('wg-fem-v1|region=a|group=_walls|kind=rigid')).toBe('rigid');
    expect(femSurfaceKind('wg-fem-v1|region=a|group=walls|kind=moving')).toBe('moving');
    expect(femSurfaceKind('wg-fem-v1|region=a|volume')).toBe('rigid');
    expect(femSurfaceKind('wg-import-v1|rigid')).toBe('rigid');
  });

  it('decodes the wire payload and checks its shapes', () => {
    const encode = (array: Float32Array | Uint32Array) => {
      const bytes = new Uint8Array(array.buffer.slice(0));
      let text = '';
      for (const byte of bytes) text += String.fromCharCode(byte);
      return btoa(text);
    };
    const domain = cube();
    const values = linear(domain);
    const payload: FemFieldPayload = {
      schema: 'wg-fem-field-v1',
      region_id: 'cube',
      frequency_hz: 1000,
      points_mm: encode(domain.points),
      tetrahedra: encode(domain.tetrahedra),
      surfaces: [
        { name: 'wg-fem-v1|region=cube|group=walls|kind=moving', triangles: encode(domain.surfaces[0].triangles) },
        { name: 'wg-fem-v1|region=cube|group=_walls|kind=rigid', triangles: encode(domain.surfaces[0].triangles) },
      ],
      pressure_real: encode(values.real),
      pressure_imag: encode(values.imag),
    };
    const decoded = decodeFemFieldPayload(payload);
    expect(Array.from(decoded.domain.tetrahedra)).toEqual(TETS.flat());
    expect(decoded.domain.surfaces.map((surface) => surface.kind)).toEqual(['moving', 'rigid']);
    expect(FEM_ROLE_COLORS[decoded.domain.surfaces[1].kind].fill).toBe('#cfcfcf');
    expect(Array.from(decoded.values.imag)).toEqual(Array.from(values.imag));
    expect(() => decodeFemFieldPayload({ ...payload, pressure_real: encode(new Float32Array(3)) })).toThrow('one value per vertex');
    expect(() => decodeFemFieldPayload({ ...payload, tetrahedra: encode(Uint32Array.from([0, 1, 2, 99])) })).toThrow('missing vertex');
  });
});

describe('buildFemFieldGeometries', () => {
  it('shows role-coloured walls without a plane, and only the cut and ghost with one', async () => {
    const { buildFemFieldGeometries } = await import('./FemFieldView');
    const domain = cube();
    const values = linear(domain);
    const options = { mode: 'spl' as const, rendering: 'smooth' as const, sequential: ['#000000', '#ffffff'] };
    const walls = buildFemFieldGeometries(domain, values, { ...options, plane: null });
    expect(walls.surfaces.map((surface) => surface.kind)).toEqual(['moving']);
    expect(walls.slice).toBeNull();

    const cut = buildFemFieldGeometries(domain, values, { ...options, plane: { origin: [0, 0, 5], normal: [0, 0, 1] } });
    expect(cut.surfaces).toEqual([]);
    const positions = cut.slice!.getAttribute('position');
    expect(cut.slice!.getAttribute('color').count).toBe(positions.count);
    expect(cut.ghost!.getAttribute('position').count).toBe(19 * 2);
    expect(cut.window!.unit).toBe('dB');
    const element = buildFemFieldGeometries(domain, values, {
      ...options, rendering: 'element', plane: { origin: [0, 0, 5], normal: [0, 0, 1] },
    });
    // Element colours are flat per triangle.
    const colors = element.slice!.getAttribute('color').array as Float32Array;
    expect(colors[0]).toBe(colors[3]);
    expect(colors[0]).toBe(colors[6]);
  });
});

it('particle velocity is constant on P1 cuts and reverses under reflection', () => {
  const domain: FemFieldDomain = { regionId: 'air', surfaces: [],
    points: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]), tetrahedra: new Uint32Array([0, 1, 2, 3]) };
  const f = 100, rho = 1.21, c = 343, k = 2 * Math.PI * f / c;
  const values = { real: new Float32Array([1, Math.cos(k * .001), 1, 1]),
    imag: new Float32Array([0, Math.sin(k * .001), 0, 0]) };
  const plane = { origin: [.2, 0, 0] as [number, number, number], normal: [1, 0, 0] as [number, number, number] };
  const cut = femNormalVelocitySlice(domain, values, plane, f, rho);
  expect(cut.real.length).toBeGreaterThan(0);
  for (const v of cut.real) expect(v).toBeCloseTo(1 / (rho * c), 6);
  const mirrored = { ...domain, points: Float32Array.from(domain.points, (v, i) => i % 3 === 0 ? -v : v) };
  const mirrorCut = femNormalVelocitySlice(mirrored, values, { ...plane, origin: [-.2, 0, 0] }, f, rho);
  expect(mirrorCut.real[0]).toBeCloseTo(-cut.real[0], 7);
});

it('real, imaginary and manual colour limits apply to retained plane geometry', () => {
  const domain: FemFieldDomain = { regionId: 'air', surfaces: [], points: new Float32Array(0), tetrahedra: new Uint32Array(0) };
  const values = { real: new Float32Array(0), imag: new Float32Array(0) };
  const planeField = { positions: new Float32Array(9), real: new Float32Array([1, 1, 1]), imag: new Float32Array([-1, -1, -1]), unit: 'Pa' as const,
    samplePositions: new Float32Array(9), maskReasons: new Uint8Array(3) };
  const options = { plane: { origin: [0, 0, 0] as [number, number, number], normal: [0, 0, 1] as [number, number, number] },
    rendering: 'smooth' as const, sequential: ['#000000', '#ffffff'], planeField,
    manualWindow: { minimum: -2, maximum: 2, unit: 'Pa' as const } };
  const real = buildFemFieldGeometries(domain, values, { ...options, mode: 'real' });
  const imag = buildFemFieldGeometries(domain, values, { ...options, mode: 'imaginary' });
  expect(real.window).toEqual(options.manualWindow);
  expect(Array.from(real.slice!.getAttribute('color').array)).not.toEqual(Array.from(imag.slice!.getAttribute('color').array));
});

it('combined rendering joins the exact tetrahedron cut to the exterior plane', () => {
  const domain = cube(), values = linear(domain);
  const options = { plane: { origin: [0, 0, 5] as [number, number, number], normal: [0, 0, 1] as [number, number, number] },
    mode: 'normalized' as const, rendering: 'smooth' as const, sequential: ['#000000', '#ffffff'] };
  const interior = buildFemFieldGeometries(domain, values, options);
  const exterior = { positions: new Float32Array([20, 0, 5, 20, 10, 5, 30, 0, 5]),
    real: new Float32Array([1, 2, 3]), imag: new Float32Array(3), unit: 'Pa' as const,
    samplePositions: new Float32Array([20, 0, 5, 20, 10, 5, 30, 0, 5]), maskReasons: new Uint8Array(3) };
  const combined = buildFemFieldGeometries(domain, values, { ...options, planeField: exterior, includeInterior: true });
  const count = interior.slice!.getAttribute('position').count;
  expect(combined.slice!.getAttribute('position').count).toBe(count + 3);
  expect(Array.from(combined.slice!.getAttribute('position').array).slice(0, count * 3))
    .toEqual(Array.from(interior.slice!.getAttribute('position').array));
  expect(combined.fieldMaxSplDb).toBe(interior.fieldMaxSplDb);
});

it('omits degenerate tetrahedra from velocity without throwing during render', () => {
  const domain: FemFieldDomain = { regionId: 'flat', surfaces: [],
    points: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0]), tetrahedra: new Uint32Array([0, 1, 2, 3]) };
  const values = { real: new Float32Array([1, 2, 3, 4]), imag: new Float32Array(4) };
  const plane = { origin: [.5, 0, 0] as [number, number, number], normal: [1, 0, 0] as [number, number, number] };
  const slice = femNormalVelocitySlice(domain, values, plane, 100);
  expect(slice.positions.length).toBe(0);
  const built = buildFemFieldGeometries(domain, values, { plane, mode: 'real', velocity: { frequencyHz: 100, density: 1.21 },
    rendering: 'smooth', sequential: ['#000000', '#ffffff'] });
  expect(built.slice?.getAttribute('position').count).toBe(0);
});
