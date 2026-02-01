/**
 * Workflow management module for ATH Horn Design Platform.
 * Manages the end-to-end design process with state tracking and artifact storage.
 * @module workflow
 */

/**
 * Workflow stage types
 * @typedef {Object} WorkflowStage
 * @property {'geometry'} GEOMETRY - Geometry design stage
 * @property {'mesh'} MESH - Mesh generation stage  
 * @property {'solver'} SOLVER - BEM simulation stage
 * @property {'results'} RESULTS - Results inspection stage
 * @property {'optimization'} OPTIMIZATION - Optimization stage
 * @property {'comparison'} COMPARISON - Design comparison stage
 */

/**
 * Workflow state tracking
 * @typedef {Object} WorkflowState
 * @property {WorkflowStage} currentStage - Current stage in the workflow
 * @property {Object} inputs - Inputs for current stage
 * @property {Object} outputs - Outputs from current stage  
 * @property {Array<{message: string, severity: 'error'|'warning'|'info', timestamp: Date}>} errors - Errors/warnings
 * @property {Array<Object>} artifacts - Inspectable intermediate artifacts
 */

/**
 * Create a new workflow instance
 * @returns {Object} Workflow manager with methods to control the process
 */
export function createWorkflow() {
  let state = {
    currentStage: 'geometry',
    inputs: {},
    outputs: {},
    errors: [],
    artifacts: []
  };

  /**
   * Transition to a new workflow stage
   * @param {string} stage - The new stage to transition to
   * @param {Object} inputs - Inputs for the new stage
   * @returns {Object} Updated workflow state
   */
  function transitionToStage(stage, inputs = {}) {
    const previousStage = state.currentStage;
    
    // Validate stage transition
    const validTransitions = getValidTransitions(previousStage);
    if (!validTransitions.includes(stage)) {
      addError(`Invalid workflow transition from ${previousStage} to ${stage}`, 'error');
      return state;
    }

    // Update state
    state.currentStage = stage;
    state.inputs = inputs;
    state.outputs = {};
    
    // Clear errors when transitioning to a new stage
    state.errors = [];
    
    return state;
  }

  /**
   * Add an artifact to the workflow
   * @param {string} type - Type of artifact (geometry, mesh, solverConfig, results)
   * @param {Object} data - Artifact data
   * @returns {void}
   */
  function addArtifact(type, data) {
    const artifact = {
      type,
      data,
      timestamp: new Date(),
      stage: state.currentStage
    };
    
    state.artifacts.push(artifact);
  }

  /**
   * Add an error or warning to the workflow
   * @param {string} message - Error/warning message
   * @param {'error'|'warning'|'info'} severity - Severity level
   * @returns {void}
   */
  function addError(message, severity = 'error') {
    state.errors.push({
      message,
      severity,
      timestamp: new Date()
    });
  }

  /**
   * Get valid transitions from a given stage
   * @param {string} stage - Current stage
   * @returns {Array<string>} Valid next stages
   */
  function getValidTransitions(stage) {
    const transitions = {
      'geometry': ['mesh'],
      'mesh': ['solver', 'optimization'],
      'solver': ['results', 'optimization'],
      'results': ['comparison', 'optimization'],
      'optimization': ['results', 'comparison'],
      'comparison': ['geometry']
    };
    
    return transitions[stage] || [];
  }

  /**
   * Get current workflow state
   * @returns {WorkflowState} Current workflow state
   */
  function getState() {
    return { ...state };
  }

  /**
   * Reset workflow to initial state
   * @returns {void}
   */
  function reset() {
    state = {
      currentStage: 'geometry',
      inputs: {},
      outputs: {},
      errors: [],
      artifacts: []
    };
  }

  return {
    transitionToStage,
    addArtifact,
    addError,
    getState,
    reset
  };
}

// Export the workflow manager as default
export default createWorkflow();