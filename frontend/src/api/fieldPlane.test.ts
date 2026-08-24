import { describe, expect, it, vi } from 'vitest';
import {
  buildFieldPlaneRequest,
  decodeFieldPlane,
  FIELD_PLANE_HEADER_VERSION,
  FIELD_PLANE_ORDERING,
  fetchFieldPlane,
  FieldPlaneHttpError,
  type FieldPlaneHeader,
} from './fieldPlane';

const header: FieldPlaneHeader = {
  version: FIELD_PLANE_HEADER_VERSION,
  request_id: 'request-1',
  job_id: 'job-1',
  frequency_index: 3,
  frequency_hz: 1_000,
  nx: 2,
  ny: 2,
  ordering: FIELD_PLANE_ORDERING,
  phase_convention: 'solver_exp_plus_ikr',
  pressure_unit: 'Pa',
  response_id: 'system',
  geometry_sha256: 'abc123',
  synthesis_revision: 'synthesis-1',
  symmetry_plane: 'yz+xz',
};

function encoded(
  headerOverrides: Partial<Record<keyof FieldPlaneHeader, unknown>> = {},
  values = [1, -1, 2, -2, 3.5, -3.5, 4.25, -4.25],
): ArrayBuffer {
  const json = new TextEncoder().encode(JSON.stringify({ ...header, ...headerOverrides }));
  const buffer = new ArrayBuffer(4 + json.length + values.length * 4);
  const view = new DataView(buffer);
  view.setUint32(0, json.length, true);
  new Uint8Array(buffer, 4, json.length).set(json);
  values.forEach((value, index) => view.setFloat32(4 + json.length + index * 4, value, true));
  return buffer;
}

describe('field-plane binary decoder', () => {
  it('reads an unaligned JSON envelope and de-interleaves little-endian complex32 values', () => {
    const decoded = decodeFieldPlane(encoded());

    expect(decoded.header).toEqual(header);
    expect([...decoded.real]).toEqual([1, 2, 3.5, 4.25]);
    expect([...decoded.imag]).toEqual([-1, -2, -3.5, -4.25]);
  });

  it('defaults absent legacy synthesis and symmetry metadata to null', () => {
    const decoded = decodeFieldPlane(encoded({
      version: 1,
      synthesis_revision: undefined,
      symmetry_plane: undefined,
    }));

    expect(decoded.header.synthesis_revision).toBeNull();
    expect(decoded.header.symmetry_plane).toBeNull();
  });

  it('rejects truncation before and inside the declared header', () => {
    expect(() => decodeFieldPlane(new ArrayBuffer(3))).toThrow(/truncated before the header length/i);
    const buffer = encoded();
    expect(() => decodeFieldPlane(buffer.slice(0, 12))).toThrow(/truncated inside the JSON header/i);
  });

  it('rejects truncated and overlong payloads by exact byte count', () => {
    const buffer = encoded();
    expect(() => decodeFieldPlane(buffer.slice(0, -4))).toThrow(/payload byte count mismatch/i);
    const overlong = new Uint8Array(buffer.byteLength + 1);
    overlong.set(new Uint8Array(buffer));
    expect(() => decodeFieldPlane(overlong.buffer)).toThrow(/payload byte count mismatch/i);
  });

  it('rejects version and ordering drift', () => {
    expect(() => decodeFieldPlane(encoded({ version: 3 }))).toThrow('Unsupported field-plane version: 3');
    expect(() => decodeFieldPlane(encoded({ ordering: 'u-major' }))).toThrow('Unsupported field-plane ordering: u-major');
  });

  it('rejects a present non-string synthesis revision', () => {
    expect(() => decodeFieldPlane(encoded({ synthesis_revision: 42 }))).toThrow(/synthesis_revision must be a non-empty string/i);
  });

  it('accepts null symmetry and rejects invalid symmetry metadata', () => {
    expect(decodeFieldPlane(encoded({ symmetry_plane: null })).header.symmetry_plane).toBeNull();
    expect(() => decodeFieldPlane(encoded({ symmetry_plane: 42 }))).toThrow(/symmetry_plane must be null or a supported symmetry plane/i);
    expect(() => decodeFieldPlane(encoded({ symmetry_plane: 'zx' }))).toThrow(/symmetry_plane must be null or a supported symmetry plane/i);
  });
});

describe('field-plane request builder', () => {
  it('builds the versioned system request without sharing mutable axis arrays', () => {
    const plane = {
      origin_m: [0, 0, 0] as [number, number, number],
      axis_u: [1, 0, 0] as [number, number, number],
      axis_v: [0, 0, 1] as [number, number, number],
      width_m: 1,
      height_m: 2,
      nx: 96,
      ny: 96,
    };
    const request = buildFieldPlaneRequest({ requestId: 'request-2', plane, frequencyIndex: 4 });

    expect(request).toEqual({
      version: 1,
      request_id: 'request-2',
      plane,
      frequency_index: 4,
      response: { id: 'system' },
    });
    expect(request.plane.axis_u).not.toBe(plane.axis_u);
  });

  it('enforces the server grid and orthonormal-axis limits before fetching', () => {
    const plane = {
      origin_m: [0, 0, 0] as [number, number, number],
      axis_u: [1, 0, 0] as [number, number, number],
      axis_v: [0, 0, 1] as [number, number, number],
      width_m: 1,
      height_m: 1,
      nx: 96,
      ny: 96,
    };
    expect(() => buildFieldPlaneRequest({
      requestId: 'request-3',
      plane: { ...plane, nx: 257 },
      frequencyIndex: 0,
    })).toThrow(/2–256/);
    expect(() => buildFieldPlaneRequest({
      requestId: 'request-4',
      plane: { ...plane, axis_v: [1, 0, 0] },
      frequencyIndex: 0,
    })).toThrow(/unit length and orthogonal/);
  });

  it('accepts channel and member response forms and rejects bare prefixes', () => {
    const plane = {
      origin_m: [0, 0, 0] as [number, number, number],
      axis_u: [1, 0, 0] as [number, number, number],
      axis_v: [0, 0, 1] as [number, number, number],
      width_m: 1,
      height_m: 1,
      nx: 96,
      ny: 96,
    };
    const build = (responseId: Parameters<typeof buildFieldPlaneRequest>[0]['responseId']) =>
      buildFieldPlaneRequest({ requestId: 'request-5', plane, frequencyIndex: 0, responseId });

    expect(build('channel:default').response.id).toBe('channel:default');
    expect(build('member:left').response.id).toBe('member:left');
    expect(() => build('member:' as `member:${string}`)).toThrow(/'member:<id>'/);
    expect(() => build('channel:' as `channel:${string}`)).toThrow(/'member:<id>'/);
  });
});

describe('field-plane request errors', () => {
  it('carries the replacement request id from a superseded response', async () => {
    const request = buildFieldPlaneRequest({
      requestId: 'request-5',
      plane: {
        origin_m: [0, 0, 0],
        axis_u: [1, 0, 0],
        axis_v: [0, 0, 1],
        width_m: 1,
        height_m: 1,
        nx: 96,
        ny: 96,
      },
      frequencyIndex: 0,
    });
    const fetcher = vi.fn(async () => new Response(JSON.stringify({
      detail: {
        code: 'superseded',
        message: 'request was superseded',
        replacement_request_id: 'request-6',
      },
    }), { status: 429, statusText: 'Too Many Requests' })) as typeof fetch;

    await expect(fetchFieldPlane('job-1', request, fetcher)).rejects.toEqual(expect.objectContaining({
      status: 429,
      code: 'superseded',
      replacementRequestId: 'request-6',
    } satisfies Partial<FieldPlaneHttpError>));
  });

  it.each([
    {
      code: 'unsupported_axisymmetric_formulation',
      message: 'Axisymmetric meridian solves do not retain exterior field traces.',
      remedy: 'Set Solver mode to Full 3D and re-solve with Metal or BEMPP.',
    },
    {
      code: 'unsupported_coupled_infinite_baffle',
      message: 'Coupled infinite-baffle solves do not retain exterior field traces.',
      remedy: 'Set Simulation type to Free-standing and re-solve with Metal or BEMPP.',
    },
  ])('retains the $code remedy from a backward-compatible 422 body', async ({ code, message, remedy }) => {
    const request = buildFieldPlaneRequest({
      requestId: 'request-7',
      plane: {
        origin_m: [0, 0, 0],
        axis_u: [1, 0, 0],
        axis_v: [0, 0, 1],
        width_m: 1,
        height_m: 1,
        nx: 96,
        ny: 96,
      },
      frequencyIndex: 0,
    });
    const fetcher = vi.fn(async () => new Response(JSON.stringify({
      detail: message,
      error_contract_version: 1,
      code,
      message,
      remedy,
    }), { status: 422, statusText: 'Unprocessable Entity' })) as typeof fetch;

    await expect(fetchFieldPlane('job-1', request, fetcher)).rejects.toEqual(expect.objectContaining({
      status: 422,
      code,
      message,
      remedy,
    } satisfies Partial<FieldPlaneHttpError>));
  });
});
