export { DEFAULTS, HORN_PROFILES, GUIDING_CURVES, MORPH_TARGETS } from './constants.js';
export { applyMorphing, getRoundedRectRadius } from './morphing.js';
export { buildWaveguideMesh, buildHornMesh } from './buildWaveguideMesh.js';
export { validateParameters } from './profiles/validation.js';
export { calculateOSSE, computeOsseRadius } from './profiles/osse.js';
export { calculateROSSE } from './profiles/rosse.js';
export { getGuidingCurveRadius } from './profiles/guidingCurve.js';
export { addEnclosureGeometry } from './mesh/enclosure.js';
export { addFreestandingWallGeometry } from './mesh/freestandingWall.js';
