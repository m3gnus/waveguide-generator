/**
 * Validation module for the Mathematical Waveguide Generator (MWG).
 * Builds trust in solver and optimization results through reference comparisons
 * and physical sanity checks.
 *
 * @module validation
 */

import { createValidationManager } from './manager.js';

export { createValidationManager };

// Export singleton instance
export default createValidationManager();
