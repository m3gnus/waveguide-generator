import { exportProfilesCSV, exportSlicesCSV } from "../../export/profiles.js";
import { generateMWGConfigContent } from "../../export/mwgConfig.js";
import { exportSTLBinary } from "../../export/stl.browser.js";
import { buildWaveguidePayload } from "../../solver/waveguidePayload.js";
import {
  buildCanonicalMeshPayloadFromShape,
  buildGeometryMeshFromShape,
} from "../../geometry/pipeline.js";
import {
  mapVertexToAth,
  transformVerticesToAth,
} from "../../geometry/transforms.js";
import { GeometryModule } from "../geometry/index.js";
import {
  prepareOccExportParams,
  prepareProfileCsvParams,
} from "../design/index.js";
import { formatDependencyBlockMessage } from "../runtime/health.js";

const EXPORT_MODULE_ID = "export";
const EXPORT_IMPORT_STAGE = "import";
const EXPORT_TASK_STAGE = "task";

const EXPORT_KINDS = Object.freeze({
  OCC_MESH: "occ-mesh",
  STL: "stl",
  PROFILE_CSV: "profile-csv",
  CONFIG: "config",
});

function isObject(value) {
  return value !== null && typeof value === "object";
}

function createExportImportEnvelope(kind, payload) {
  return Object.freeze({
    module: EXPORT_MODULE_ID,
    stage: EXPORT_IMPORT_STAGE,
    kind,
    ...payload,
  });
}

function assertExportImportEnvelope(input, expectedKind = null) {
  if (
    !isObject(input) ||
    input.module !== EXPORT_MODULE_ID ||
    input.stage !== EXPORT_IMPORT_STAGE
  ) {
    throw new Error(
      "Export module task requires input created by ExportModule import helpers.",
    );
  }
  if (expectedKind && input.kind !== expectedKind) {
    throw new Error(
      `Export module task expected "${expectedKind}" input but received "${input.kind}".`,
    );
  }
}

function assertExportTaskEnvelope(result, expectedKind = null) {
  if (
    !isObject(result) ||
    result.module !== EXPORT_MODULE_ID ||
    result.stage !== EXPORT_TASK_STAGE
  ) {
    throw new Error(
      "Export module output requires a result from ExportModule.task().",
    );
  }
  if (expectedKind && result.kind !== expectedKind) {
    throw new Error(
      `Export module output expected "${expectedKind}" result but received "${result.kind}".`,
    );
  }
}

async function fetchBackendHealth(backendUrl) {
  const controller = new AbortController();
  const timeoutMs = 10000;
  const timer = setTimeout(() => {
    controller.abort();
  }, timeoutMs);

  try {
    const res = await fetch(`${backendUrl}/health`, {
      signal: controller.signal,
    });
    clearTimeout(timer);
    if (!res.ok) {
      return null;
    }
    return await res.json();
  } catch (_error) {
    clearTimeout(timer);
    return null;
  }
}

function rotateVerticesForStl(vertices) {
  const rotated = new Float32Array(vertices.length);
  for (let i = 0; i < vertices.length; i += 3) {
    rotated[i] = Number(vertices[i]) || 0;
    rotated[i + 1] = -(Number(vertices[i + 2]) || 0);
    rotated[i + 2] = Number(vertices[i + 1]) || 0;
  }
  return rotated;
}

function buildExportArtifacts(mesh, payload) {
  const verticalOffset = Number(payload?.metadata?.verticalOffset || 0);
  return {
    mesh,
    export: {
      verticalOffset,
      mapVertexToAth,
      transformVerticesToAth,
      toAthVertices(vertices = payload.vertices, transformOptions = {}) {
        return transformVerticesToAth(vertices, {
          verticalOffset,
          offsetSign: 1,
          ...transformOptions,
        });
      },
    },
  };
}

async function runOccMeshExportTask(input, options = {}) {
  assertExportImportEnvelope(input, EXPORT_KINDS.OCC_MESH);

  input.onStatus?.("Connecting to backend...");

  let health = await fetchBackendHealth(input.backendUrl);
  if (!health) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    health = await fetchBackendHealth(input.backendUrl);
  }
  if (!health) {
    throw new Error(
      `Backend health check failed at ${input.backendUrl}.\nStart with: npm start`,
    );
  }

  if (health?.occBuilderReady === false) {
    throw new Error(
      formatDependencyBlockMessage(health, {
        features: ["meshBuild"],
        fallback: "OCC mesh export is unavailable.",
      }),
    );
  }

  input.onStatus?.("Building mesh (Python OCC)...");

  const mshVersion = options.mshVersion || "2.2";
  const occParams = prepareOccExportParams(input.params);
  const requestPayload = buildWaveguidePayload(occParams, mshVersion);

  let response;
  try {
    const res = await fetch(`${input.backendUrl}/api/mesh/build`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestPayload),
    });

    if (!res.ok) {
      const err = await res
        .json()
        .catch(() => ({ detail: `HTTP ${res.status}` }));
      throw new Error(
        `/api/mesh/build failed: ${err.detail || res.statusText}`,
      );
    }

    response = await res.json();
  } catch (err) {
    if (err.message?.includes("/api/mesh/build failed")) throw err;
    throw new Error(`/api/mesh/build request failed: ${err.message}`);
  }

  if (
    !response ||
    response.generatedBy !== "gmsh-occ" ||
    typeof response.msh !== "string"
  ) {
    throw new Error(
      "Invalid response from /api/mesh/build: expected gmsh-occ mesh data.",
    );
  }

  const geometryTask = GeometryModule.task(
    GeometryModule.importPrepared(occParams),
    {
      includeEnclosure: Number(occParams.encDepth || 0) > 0,
    },
  );
  const geometryShape = GeometryModule.output.shape(geometryTask);
  const payload = buildCanonicalMeshPayloadFromShape(geometryShape, {
    includeEnclosure: Number(occParams.encDepth || 0) > 0,
    validateIntegrity: false,
  });
  const mesh = buildGeometryMeshFromShape(geometryShape, {
    includeEnclosure: Number(occParams.encDepth || 0) > 0,
  });

  return Object.freeze({
    module: EXPORT_MODULE_ID,
    stage: EXPORT_TASK_STAGE,
    kind: EXPORT_KINDS.OCC_MESH,
    input,
    result: {
      artifacts: buildExportArtifacts(mesh, payload),
      payload,
      msh: response.msh,
      meshStats: response.stats || null,
    },
  });
}

function runStlExportTask(input) {
  assertExportImportEnvelope(input, EXPORT_KINDS.STL);

  const geometryTask = GeometryModule.task(
    GeometryModule.importPrepared(input.params),
    {
      includeEnclosure: false,
      adaptivePhi: true,
    },
  );
  const geometryShape = GeometryModule.output.shape(geometryTask);
  const { vertices, indices } = buildGeometryMeshFromShape(geometryShape, {
    includeEnclosure: false,
    adaptivePhi: true,
  });
  const stlBinary = exportSTLBinary(
    rotateVerticesForStl(Float32Array.from(vertices)),
    Uint32Array.from(indices),
    input.modelName,
  );

  return Object.freeze({
    module: EXPORT_MODULE_ID,
    stage: EXPORT_TASK_STAGE,
    kind: EXPORT_KINDS.STL,
    input,
    files: [
      {
        content: stlBinary,
        fileName: `${input.baseName}.stl`,
        saveOptions: {
          contentType: "application/sla",
          typeInfo: {
            description: "STL Model",
            accept: { "model/stl": [".stl"] },
          },
        },
      },
    ],
  });
}

function runProfileCsvExportTask(input) {
  assertExportImportEnvelope(input, EXPORT_KINDS.PROFILE_CSV);

  const csvParams = prepareProfileCsvParams(input.params);
  const meshParams = {
    angularSegments: csvParams.angularSegments,
    lengthSegments: csvParams.lengthSegments,
  };

  return Object.freeze({
    module: EXPORT_MODULE_ID,
    stage: EXPORT_TASK_STAGE,
    kind: EXPORT_KINDS.PROFILE_CSV,
    input,
    files: [
      {
        content: exportProfilesCSV(input.vertices, meshParams),
        fileName: `${input.baseName}_profiles.csv`,
        saveOptions: {
          contentType: "text/csv",
          typeInfo: {
            description: "Angular Profiles",
            accept: { "text/csv": [".csv"] },
          },
        },
      },
      {
        content: exportSlicesCSV(input.vertices, meshParams),
        fileName: `${input.baseName}_slices.csv`,
        saveOptions: {
          contentType: "text/csv",
          typeInfo: {
            description: "Length Slices",
            accept: { "text/csv": [".csv"] },
          },
        },
      },
    ],
  });
}

function runConfigExportTask(input) {
  assertExportImportEnvelope(input, EXPORT_KINDS.CONFIG);

  return Object.freeze({
    module: EXPORT_MODULE_ID,
    stage: EXPORT_TASK_STAGE,
    kind: EXPORT_KINDS.CONFIG,
    input,
    files: [
      {
        content: generateMWGConfigContent(input.params),
        fileName: `${input.baseName}.txt`,
        saveOptions: {
          contentType: "text/plain",
          typeInfo: {
            description: "MWG Config",
            accept: { "text/plain": [".txt"] },
          },
        },
      },
    ],
  });
}

export function importOccMeshBuild(
  preparedParams,
  { backendUrl, onStatus } = {},
) {
  return createExportImportEnvelope(EXPORT_KINDS.OCC_MESH, {
    params: preparedParams,
    backendUrl,
    onStatus,
  });
}

export function importStlExport(
  preparedParams,
  { baseName = "waveguide", modelName = "MWG Horn" } = {},
) {
  return createExportImportEnvelope(EXPORT_KINDS.STL, {
    params: preparedParams,
    baseName,
    modelName,
  });
}

export function importProfileCsvExport(
  preparedParams,
  { vertices, baseName = "waveguide" } = {},
) {
  return createExportImportEnvelope(EXPORT_KINDS.PROFILE_CSV, {
    params: preparedParams,
    vertices,
    baseName,
  });
}

export function importConfigExport({ params, baseName = "waveguide" }) {
  return createExportImportEnvelope(EXPORT_KINDS.CONFIG, {
    params,
    baseName,
  });
}

export function runExportTask(input, options = {}) {
  assertExportImportEnvelope(input);

  switch (input.kind) {
    case EXPORT_KINDS.OCC_MESH:
      return runOccMeshExportTask(input, options);
    case EXPORT_KINDS.STL:
      return runStlExportTask(input);
    case EXPORT_KINDS.PROFILE_CSV:
      return runProfileCsvExportTask(input);
    case EXPORT_KINDS.CONFIG:
      return runConfigExportTask(input);
    default:
      throw new Error(`Unsupported export module task: ${input.kind}`);
  }
}

export function getExportFiles(result) {
  assertExportTaskEnvelope(result);
  return Array.isArray(result.files) ? result.files : [];
}

export function getOccMeshBuildResult(result) {
  assertExportTaskEnvelope(result, EXPORT_KINDS.OCC_MESH);
  return result.result;
}

export const ExportModule = Object.freeze({
  id: EXPORT_MODULE_ID,
  importOccMeshBuild,
  importStl: importStlExport,
  importProfileCsv: importProfileCsvExport,
  importConfig: importConfigExport,
  task: runExportTask,
  output: Object.freeze({
    files: getExportFiles,
    occMesh: getOccMeshBuildResult,
  }),
});
