import { GlobalState } from '../state.js';
import { isNumericString, prepareGeometryParams } from '../geometry/index.js';

export { isNumericString };

export function prepareParamsForMesh(options = {}) {
  const state = GlobalState.get();
  return prepareGeometryParams(state.params, {
    type: state.type,
    ...options
  });
}
