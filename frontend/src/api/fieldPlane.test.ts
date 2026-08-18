import { describe, expect, it } from 'vitest';
import {
  buildFieldPlaneRequest,
  decodeFieldPlane,
  FIELD_PLANE_ORDERING,
  type FieldPlaneHeader,
} from './fieldPlane';

const header: FieldPlaneHeader = {
  version: 1,
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
    expect(() => decodeFieldPlane(encoded({ version: 2 }))).toThrow('Unsupported field-plane version: 2');
    expect(() => decodeFieldPlane(encoded({ ordering: 'u-major' }))).toThrow('Unsupported field-plane ordering: u-major');
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
});
