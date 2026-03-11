export { calculateROSSE, calculateOSSE, validateParameters, buildHornMesh, buildWaveguideMesh } from './waveguide.js';
export { parseExpression } from './expression.js';
export { evalParam, parseList, parseQuadrants } from './common.js';
export {
  isNumericString,
  isMWGConfig,
  coerceConfigParams,
  applyAthImportDefaults,
  prepareGeometryParams
} from './params.js';
export { SURFACE_TAGS } from './tags.js';
export { mapVertexToAth, transformVerticesToAth } from './transforms.js';
export { buildCanonicalMeshPayload, buildGeometryArtifacts } from './pipeline.js';
export {
  GeometryModule,
  importGeometryInput,
  importPreparedGeometryInput,
  runGeometryTask,
  getGeometryArtifacts,
  getGeometryMeshOutput,
  getGeometrySimulationOutput,
  getGeometryExportOutput,
  buildGeometryCanonicalOutput
} from '../modules/geometry/index.js';
