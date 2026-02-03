/**
 * Validation module for MWG - Mathematical Waveguide Generator.
 * Builds trust in solver and optimization results through reference comparisons
 * and physical sanity checks.
 *
 * @module validation
 */

import { createValidationManager } from './manager.js';

export { createValidationManager };

// Export singleton instance
export default createValidationManager();
