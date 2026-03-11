import { DesignModule } from '../design/index.js';
import { SimulationModule } from './index.js';
import { GlobalState } from '../../state.js';

/**
 * Prepare the canonical simulation mesh payload.
 * Returns the payload for simulation or throws if invalid.
 */
export function prepareCanonicalSimulationMesh() {
  const designTask = DesignModule.task(
    DesignModule.importState(GlobalState.get(), {
      applyVerticalOffset: true
    })
  );
  const preparedParams = DesignModule.output.simulationParams(designTask);
  const simulationTask = SimulationModule.task(SimulationModule.importDesign(designTask), {
    includeEnclosure: Number(preparedParams.encDepth || 0) > 0,
    adaptivePhi: false
  });
  const payload = SimulationModule.output.mesh(simulationTask);

  const vertexCount = payload.vertices.length / 3;
  const triangleCount = payload.indices.length / 3;

  const maxIndex = Math.max(...payload.indices);
  if (maxIndex >= vertexCount) {
    throw new Error(
      `Invalid mesh: max index ${maxIndex} >= vertex count ${vertexCount}. This indicates simulation mesh corruption.`
    );
  }

  return payload;
}

/**
 * Prepare an OCC adaptive solve request.
 * Returns { waveguidePayload, submitOptions, preparedParams }.
 */
export function prepareOccAdaptiveSolveRequest(options = {}) {
  const state = GlobalState.get();
  const designTask = DesignModule.task(
    DesignModule.importState(state, {
      applyVerticalOffset: true
    })
  );
  const preparedParams = DesignModule.output.simulationParams(designTask);
  const simulationInput = SimulationModule.importDesign(designTask);
  
  const { waveguidePayload, submitOptions } = SimulationModule.output.occAdaptive(simulationInput, {
    mshVersion: options.mshVersion || '2.2',
    simType: options.simType ?? 2
  });

  return { waveguidePayload, submitOptions, preparedParams, stateSnapshot: JSON.parse(JSON.stringify(state)) };
}

export function createSimulationClient() {
  return SimulationModule.output.client();
}
