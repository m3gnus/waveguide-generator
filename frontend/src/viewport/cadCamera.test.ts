import { describe, expect, it } from 'vitest';
import fixture from './solverFrame.v2.fixture.json';
import { cadCameraAxes } from './cadCamera';

const option = (matrix: number[][]) => ({ previewFromRecord: matrix });
describe('CAD camera in the displayed record frame', () => {
  it('faces a +z record from -y with CAD +z up', () => {
    expect(cadCameraAxes(option(fixture.axes['-y']))).toEqual({ direction: [0, -1, 0], up: [0, 0, 1] });
  });
  it('faces an already -y prepared record along its solver +z and +y', () => {
    expect(cadCameraAxes(option(fixture.axes['+z']))).toEqual({ direction: [0, 0, 1], up: [0, 1, 0] });
  });
  it('changes aim when the chosen frame changes without replacing the mesh', () => {
    const mesh = {};
    const first = cadCameraAxes(option(fixture.axes['+z']));
    const next = cadCameraAxes(option(fixture.axes['-y']));
    expect(mesh).toBe(mesh);
    expect(next).not.toEqual(first);
  });
});
