import { DEFAULT_BACKEND_URL } from '../../config/backendUrl.js';
import { ExportModule } from './index.js';
import { DesignModule } from '../design/index.js';

const DEFAULT_EXPORT_BASE_NAME = 'horn';

function isObject(value) {
  return value !== null && typeof value === 'object';
}

function normalizeBaseName(baseName) {
  const normalized = String(baseName || '').trim();
  return normalized || DEFAULT_EXPORT_BASE_NAME;
}

function requireExportState(state) {
  if (!isObject(state) || !isObject(state.params) || typeof state.type !== 'string') {
    throw new Error('Export use cases require an explicit application state snapshot.');
  }
  return state;
}

async function writeExportFiles(files, writeFile) {
  if (typeof writeFile !== 'function') {
    throw new Error('Export use cases require a writeFile callback at the app/UI edge.');
  }

  const writtenFiles = [];
  for (const file of files) {
    writtenFiles.push(await writeFile(file));
  }
  return writtenFiles;
}

/**
 * Prepare OCC export artifacts using the Python mesher (`POST /api/mesh/build`).
 * Returns `{ artifacts, payload, msh, meshStats }`.
 */
export async function prepareExportArtifacts(
  preparedParams,
  { backendUrl = DEFAULT_BACKEND_URL, onStatus, ...options } = {}
) {
  const exportTask = await ExportModule.task(
    ExportModule.importOccMeshBuild(preparedParams, {
      backendUrl,
      onStatus
    }),
    options
  );

  return ExportModule.output.occMesh(exportTask);
}

export function buildStlExportFiles(state, { baseName } = {}) {
  const exportState = requireExportState(state);
  const designTask = DesignModule.task(
    DesignModule.importState(exportState, {
      applyVerticalOffset: false
    })
  );
  const preparedParams = DesignModule.output.exportParams(designTask);
  const exportTask = ExportModule.task(
    ExportModule.importStl(preparedParams, {
      baseName: normalizeBaseName(baseName)
    })
  );
  return ExportModule.output.files(exportTask);
}

export async function exportSTL({ state, baseName, writeFile } = {}) {
  return writeExportFiles(
    buildStlExportFiles(state, { baseName }),
    writeFile
  );
}

export function buildMwgConfigExportFiles(state, { baseName } = {}) {
  const exportState = requireExportState(state);
  const exportTask = ExportModule.task(
    ExportModule.importConfig({
      params: { type: exportState.type, ...exportState.params },
      baseName: normalizeBaseName(baseName)
    })
  );
  return ExportModule.output.files(exportTask);
}

export async function exportMWGConfig({ state, baseName, writeFile } = {}) {
  return writeExportFiles(
    buildMwgConfigExportFiles(state, { baseName }),
    writeFile
  );
}

export function buildProfileCsvExportFiles(vertices, { state, baseName } = {}) {
  if (!vertices || vertices.length === 0) {
    return null;
  }

  const exportState = requireExportState(state);
  const designTask = DesignModule.task(
    DesignModule.importState(exportState, {
      applyVerticalOffset: false
    })
  );
  const preparedParams = DesignModule.output.preparedParams(designTask);

  const exportTask = ExportModule.task(
    ExportModule.importProfileCsv(preparedParams, {
      vertices,
      baseName: normalizeBaseName(baseName)
    })
  );
  return ExportModule.output.files(exportTask);
}

export async function exportProfileCSV(vertices, { state, baseName, writeFile, onMissingMesh } = {}) {
  const files = buildProfileCsvExportFiles(vertices, { state, baseName });
  if (!files) {
    if (typeof onMissingMesh === 'function') {
      onMissingMesh('Please generate a horn model first.');
    }
    return null;
  }
  return writeExportFiles(files, writeFile);
}
