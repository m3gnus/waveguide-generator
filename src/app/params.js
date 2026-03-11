import { GlobalState } from '../state.js';
import { DesignModule } from '../modules/design/index.js';

export function isNumericString(value) {
  return /^-?\d*\.?\d+(e[+-]?\d+)?$/i.test(String(value).trim());
}

export function prepareParamsForMesh(options = {}) {
  const designTask = DesignModule.task(DesignModule.importState(GlobalState.get(), options));
  return DesignModule.output.preparedParams(designTask);
}
