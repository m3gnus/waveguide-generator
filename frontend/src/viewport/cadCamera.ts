import type { SolverFrameAxisOption } from '../api/solverFrame';
import type { CameraDirection } from './cameraMath';

/** The server maps record coordinates into the chosen solver frame. A camera
 * looking down solver +Z with solver +Y up uses the inverse rotation (the
 * transpose) in the unchanged display mesh's record coordinates. */
export function cadCameraAxes(option: Pick<SolverFrameAxisOption, 'previewFromRecord'>): {
  direction: CameraDirection; up: CameraDirection;
} {
  const matrix = option.previewFromRecord;
  return {
    direction: [matrix[2][0], matrix[2][1], matrix[2][2]],
    up: [matrix[1][0], matrix[1][1], matrix[1][2]],
  };
}
