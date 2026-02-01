/**
 * AI Surrogate Modeling Module
 * 
 * Provides surrogate models for approximating expensive BEM simulations.
 * 
 * @module surrogate
 */

export { createSurrogateModel } from './regression.js';
export { GaussianProcess } from './gaussianProcess.js';
export { predictWithUncertainty } from './regression.js';