export { generateMWGConfigContent } from './mwgConfig.js';
export { exportProfilesCSV, exportGmshGeo, compareWithReference } from './profiles.js';
export { exportHornToGeo, exportMSH, exportFullGeo } from './msh.js';
export { exportVerticesToCSV, exportVerticesToCSVWithMetadata, exportCrossSectionProfilesCSV } from './csv.js';
export {
  generateAbecProjectFile,
  generateAbecSolvingFile,
  generateAbecObservationFile,
  extractPolarBlocks,
  generateAbecCoordsFile,
  generateAbecStaticFile
} from './abecProject.js';
export { generateBemppStarterScript } from './bempp.js';

// Browser-compatible STL exports (binary/ASCII generation)
export { exportSTLBinary, exportSTLAscii } from './stl.browser.js';

// Node.js-only exports (require fs/child_process modules)
// These are available in Node.js environments only:
// - import { writeSTLFile } from './src/export/stl.js'
