import { GlobalState } from '../state.js';
import { isNumericString } from '../geometry/index.js';
import { DesignModule } from '../modules/design/index.js';

export { isNumericString };

export function prepareParamsForMesh(options = {}) {
  const designTask = DesignModule.task(DesignModule.importState(GlobalState.get(), options));
  return DesignModule.output.preparedParams(designTask);
}
