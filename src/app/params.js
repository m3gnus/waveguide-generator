import { GlobalState } from '../state.js';
import { isNumericString } from '../geometry/index.js';
import { ParamModule } from '../modules/param/index.js';

export { isNumericString };

export function prepareParamsForMesh(options = {}) {
  const paramTask = ParamModule.task(ParamModule.importState(GlobalState.get(), options));
  return ParamModule.output.params(paramTask);
}
