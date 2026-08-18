import { afterEach, describe, expect, it } from 'vitest';
import type { FieldPlaneMaskRequest, FieldPlaneMaskResult } from './fieldPlaneMaskProtocol';
import { useFieldPlaneMaskStore } from './fieldPlaneMaskStore';

const request = (generation: number): FieldPlaneMaskRequest => ({
  type: 'classify',
  generation,
  jobId: 'job-1',
  geometrySha256: 'geometry-a',
  symmetryPlane: 'yz',
  plane: {
    origin_m: [0, 0, 0],
    axis_u: [1, 0, 0],
    axis_v: [0, 1, 0],
    width_m: 1,
    height_m: 1,
    nx: 2,
    ny: 2,
  },
});

const result = (generation: number, value: number): FieldPlaneMaskResult => ({
  type: 'result',
  generation,
  jobId: 'job-1',
  geometrySha256: 'geometry-a',
  symmetryPlane: 'yz',
  nx: 2,
  ny: 2,
  watertight: true,
  snappedVertexCount: 4,
  mask: Uint8Array.of(value, 0, 0, 0).buffer,
});

describe('field-plane mask generations', () => {
  afterEach(() => useFieldPlaneMaskStore.getState().clear());

  it('keeps the previous same-geometry mask while newer work runs and ignores stale results', () => {
    useFieldPlaneMaskStore.getState().begin(request(1));
    useFieldPlaneMaskStore.getState().apply(result(1, 1));
    const previous = useFieldPlaneMaskStore.getState().mask;

    useFieldPlaneMaskStore.getState().begin(request(2));
    expect(useFieldPlaneMaskStore.getState().mask).toBe(previous);
    useFieldPlaneMaskStore.getState().apply(result(1, 9));
    expect(useFieldPlaneMaskStore.getState().mask?.data[0]).toBe(1);

    useFieldPlaneMaskStore.getState().apply(result(2, 2));
    expect(useFieldPlaneMaskStore.getState().mask?.data[0]).toBe(2);
    expect(useFieldPlaneMaskStore.getState().snappedVertexCount).toBe(4);
  });

  it('drops the mask and watertightness when the solved geometry identity changes', () => {
    useFieldPlaneMaskStore.getState().begin(request(1));
    useFieldPlaneMaskStore.getState().apply(result(1, 1));
    useFieldPlaneMaskStore.getState().begin({
      ...request(2),
      geometrySha256: 'geometry-b',
    });

    expect(useFieldPlaneMaskStore.getState().mask).toBeNull();
    expect(useFieldPlaneMaskStore.getState().watertight).toBeNull();
    expect(useFieldPlaneMaskStore.getState().snappedVertexCount).toBeNull();
  });

  it('treats symmetry synthesis as part of the mask geometry identity', () => {
    useFieldPlaneMaskStore.getState().begin(request(1));
    useFieldPlaneMaskStore.getState().apply(result(1, 1));
    useFieldPlaneMaskStore.getState().begin({
      ...request(2),
      symmetryPlane: 'yz+xz',
    });

    expect(useFieldPlaneMaskStore.getState()).toMatchObject({
      symmetryPlane: 'yz+xz',
      mask: null,
      watertight: null,
      snappedVertexCount: null,
    });
    useFieldPlaneMaskStore.getState().apply(result(2, 2));
    expect(useFieldPlaneMaskStore.getState().mask).toBeNull();
  });
});
