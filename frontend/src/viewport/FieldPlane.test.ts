import { Vector3 } from 'three';
import { describe, expect, it, vi } from 'vitest';
import type { FieldPlaneSpec } from '../api/fieldPlane';
import { fieldPlaneAnimationFrame, fieldPlaneTransform } from './FieldPlane';

describe('field-plane scene transform', () => {
  it('converts only the solver-metre origin while preserving the declared axes', () => {
    const plane: FieldPlaneSpec = {
      origin_m: [1, 2, 3],
      axis_u: [1, 0, 0],
      axis_v: [0, 0, 1],
      width_m: 2,
      height_m: 4,
      nx: 96,
      ny: 96,
    };
    const transform = fieldPlaneTransform(plane, 1_000);

    expect(new Vector3(0, 0, 0).applyMatrix4(transform).toArray()).toEqual([1_000, 2_000, 3_000]);
    expect(new Vector3(1, 0, 0).applyMatrix4(transform).toArray()).toEqual([1_001, 2_000, 3_000]);
    expect(new Vector3(0, 1, 0).applyMatrix4(transform).toArray()).toEqual([1_000, 2_000, 3_001]);
  });

  it('keeps demand rendering alive only while instantaneous pressure is animating', () => {
    const scheduler = { schedule: vi.fn(() => () => undefined) };
    const phase = fieldPlaneAnimationFrame(true, 0, 0.25, 1, scheduler);

    expect(phase).toBeCloseTo(Math.PI / 2, 10);
    expect(scheduler.schedule).toHaveBeenCalledOnce();
    expect(fieldPlaneAnimationFrame(false, phase, 0.25, 1, scheduler)).toBe(0);
    expect(scheduler.schedule).toHaveBeenCalledOnce();
  });
});
