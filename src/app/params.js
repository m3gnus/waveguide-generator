import { GlobalState } from '../state.js';
import { isNumericString } from '../geometry/index.js';
import { GeometryModule } from '../modules/geometry/index.js';

export { isNumericString };

export function prepareParamsForMesh(options = {}) {
  const state = GlobalState.get();
  return GeometryModule.import(state.params, {
    type: state.type,
    ...options
  }).params;
}
