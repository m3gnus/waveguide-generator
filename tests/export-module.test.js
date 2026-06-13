import test from "node:test";
import assert from "node:assert/strict";

import { getDefaults } from "../src/config/defaults.js";
import { buildPreparedGeometryMesh } from "../src/geometry/pipeline.js";
import { densifyForSmoothTessellation } from "../src/geometry/tessellation.js";
import { prepareGeometryParams } from "../src/geometry/index.js";
import { ExportModule } from "../src/modules/export/index.js";

function makePreparedParams(overrides = {}) {
  return prepareGeometryParams(
    {
      ...getDefaults("OSSE"),
      type: "OSSE",
      L: "100",
      a: "50",
      a0: "15",
      r0: "12.7",
      angularSegments: 16,
      lengthSegments: 8,
      ...overrides,
    },
    { type: "OSSE", applyVerticalOffset: true },
  );
}

function splitCsvSections(content) {
  return content.trim().split("\r\n\r\n").map((section) => section.split("\r\n"));
}

test("ExportModule HornLab mesh task requests backend mesh build and returns canonical payload", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  const statuses = [];

  globalThis.fetch = async (url, init) => {
    requests.push({ url, init });

    if (url.endsWith("/health")) {
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        async json() {
          return { status: "ok" };
        },
      };
    }

    if (url.endsWith("/api/mesh/build")) {
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        async json() {
          return {
            msh: "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
            generatedBy: "hornlab-waveguide-mesher",
            stats: { nodeCount: 3, elementCount: 1 },
          };
        },
      };
    }

    throw new Error(`Unexpected request URL: ${url}`);
  };

  try {
    const prepared = makePreparedParams({ encDepth: 180 });
    const exportTask = await ExportModule.task(
      ExportModule.importHornlabMesherMeshBuild(prepared, {
        backendUrl: "http://localhost:8000",
        onStatus(message) {
          statuses.push(message);
        },
      }),
    );
    const result = ExportModule.output.hornlabMesherMesh(exportTask);

    assert.deepEqual(statuses, [
      "Connecting to backend...",
      "Building mesh (HornLab mesher)...",
    ]);
    assert.equal(requests.length, 2);
    assert.equal(requests[0].url, "http://localhost:8000/health");
    assert.equal(requests[1].url, "http://localhost:8000/api/mesh/build");
    assert.equal(result.msh.includes("$MeshFormat"), true);
    assert.equal(result.meshStats.nodeCount, 3);
    assert.ok(Array.isArray(result.payload.surfaceTags));

    const payload = JSON.parse(requests[1].init.body);
    assert.equal(payload.formula_type, "OSSE");
    assert.equal(payload.n_angular, 20);
    assert.equal(payload.n_length, 10);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("ExportModule exposes only HornLab mesher mesh exports", () => {
  assert.equal(ExportModule.importOccMeshBuild, undefined);
  assert.equal(ExportModule.output.occMesh, undefined);
  assert.equal(typeof ExportModule.importHornlabMesherMeshBuild, "function");
  assert.equal(typeof ExportModule.output.hornlabMesherMesh, "function");
});

test("ExportModule file tasks return save descriptors for STL, CSV, and config", () => {
  const prepared = makePreparedParams({ encDepth: 0, wallThickness: 0 });

  const stlTask = ExportModule.task(
    ExportModule.importStl(prepared, { baseName: "demo" }),
  );
  const stlFiles = ExportModule.output.files(stlTask);
  assert.equal(stlFiles.length, 1);
  assert.equal(stlFiles[0].fileName, "demo.stl");
  assert.equal(stlFiles[0].content instanceof ArrayBuffer, true);
  assert.equal(new DataView(stlFiles[0].content).getUint32(80, true) > 0, true);

  const csvTask = ExportModule.task(
    ExportModule.importProfileCsv(
      { angularSegments: 4, lengthSegments: 1 },
      {
        vertices: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0]),
        baseName: "demo",
      },
    ),
  );
  const csvFiles = ExportModule.output.files(csvTask);
  assert.deepEqual(
    csvFiles.map((file) => file.fileName),
    ["demo_profiles.csv", "demo_slices.csv"],
  );

  const configTask = ExportModule.task(
    ExportModule.importConfig({
      params: { type: "OSSE", ...prepared },
      baseName: "demo",
    }),
  );
  const configFiles = ExportModule.output.files(configTask);
  assert.equal(configFiles.length, 1);
  assert.equal(configFiles[0].fileName, "demo.txt");
  assert.equal(typeof configFiles[0].content, "string");
});

test("ExportModule Fusion CSV builds the design export grid instead of using viewport vertices", () => {
  const prepared = makePreparedParams({
    encDepth: 180,
    wallThickness: 8,
    angularSegments: 8,
    lengthSegments: 2,
  });

  const csvTask = ExportModule.task(
    ExportModule.importProfileCsv(prepared, {
      vertices: new Float32Array([999, 999, 999]),
      baseName: "design-grid",
    }),
  );
  const csvFiles = ExportModule.output.files(csvTask);
  const profiles = splitCsvSections(csvFiles[0].content);
  const slices = splitCsvSections(csvFiles[1].content);

  // The default OSSE config carries an implicit rounded-rect morph, so the
  // canonical angle list folds Mesh.CornerSegments (4) into the angularSegments
  // (8) budget: ceil((8 + 4) / 4) = 3 points per quadrant -> 12 rings.
  assert.equal(profiles.length, 12);
  assert.ok(profiles.every((section) => section.length === 3));
  assert.equal(slices.length, 3);
  assert.ok(slices.every((section) => section.length === 13));
  assert.equal(csvFiles[0].content.includes("99.900000;99.900000;99.900000"), false);
});

test("ExportModule STL task uses smooth viewport tessellation density", () => {
  const prepared = makePreparedParams({
    encDepth: 0,
    wallThickness: 0,
    angularSegments: 16,
    lengthSegments: 8,
    cornerSegments: 2,
  });

  const sparseMesh = buildPreparedGeometryMesh(prepared, {
    includeEnclosure: false,
    adaptivePhi: true,
  });
  const stlTask = ExportModule.task(
    ExportModule.importStl(prepared, { baseName: "smooth-demo" }),
  );
  const [stlFile] = ExportModule.output.files(stlTask);
  const triangleCount = new DataView(stlFile.content).getUint32(80, true);

  assert.ok(
    triangleCount > sparseMesh.indices.length / 3,
    "STL export should densify sparse design grids before tessellation",
  );
});

test("ExportModule STL task exports only waveguide skin without wall or throat plate", () => {
  const prepared = makePreparedParams({
    encDepth: 180,
    wallThickness: 8,
    angularSegments: 16,
    lengthSegments: 8,
    cornerSegments: 2,
  });

  const expectedMesh = buildPreparedGeometryMesh(
    densifyForSmoothTessellation({
      ...prepared,
      encDepth: 0,
      wallThickness: 0,
    }),
    {
      includeEnclosure: false,
      adaptivePhi: true,
      omitSource: true,
    },
  );
  const stlTask = ExportModule.task(
    ExportModule.importStl(prepared, { baseName: "skin-demo" }),
  );
  const [stlFile] = ExportModule.output.files(stlTask);
  const triangleCount = new DataView(stlFile.content).getUint32(80, true);

  assert.equal(triangleCount, expectedMesh.indices.length / 3);
  assert.equal(expectedMesh.groups.throat_disc, undefined);
  assert.equal(expectedMesh.groups.freestandingWall, undefined);
  assert.equal(expectedMesh.groups.enclosure, undefined);
});

test("ExportModule HornLab mesh build uses design-layer export normalization for request payload", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];

  globalThis.fetch = async (url, init) => {
    requests.push({ url, init });

    if (url.endsWith("/health")) {
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        async json() {
          return { status: "ok" };
        },
      };
    }

    if (url.endsWith("/api/mesh/build")) {
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        async json() {
          return {
            msh: "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
            generatedBy: "hornlab-waveguide-mesher",
            stats: { nodeCount: 3, elementCount: 1 },
          };
        },
      };
    }

    throw new Error(`Unexpected request URL: ${url}`);
  };

  try {
    const prepared = makePreparedParams({
      encDepth: 0,
      wallThickness: 0,
      angularSegments: 21.2,
      lengthSegments: 9.1,
      scale: 2,
      throatResolution: 3,
      mouthResolution: 5,
      rearResolution: 7,
      quadrants: "not-a-quadrant",
    });

    await ExportModule.task(
      ExportModule.importHornlabMesherMeshBuild(prepared, {
        backendUrl: "http://localhost:8000",
      }),
    );

    const payload = JSON.parse(requests[1].init.body);
    assert.equal(payload.n_angular, 20);
    assert.equal(payload.n_length, 10);
    assert.equal(payload.throat_res, 6);
    assert.equal(payload.mouth_res, 10);
    assert.equal(payload.rear_res, 14);
    assert.equal(payload.wall_thickness, 5);
    assert.equal(payload.quadrants, 1234);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("ExportModule STEP task requests a single-layer inner-surface export", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  const statuses = [];

  globalThis.fetch = async (url, init) => {
    requests.push({ url, init });

    if (url.endsWith("/health")) {
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        async json() {
          return { status: "ok", mesherReady: true };
        },
      };
    }

    if (url.endsWith("/api/mesh/step")) {
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        async json() {
          return {
            step: "ISO-10303-21;\nEND-ISO-10303-21;\n",
            generatedBy: "hornlab-waveguide-mesher",
            stats: {
              singleLayer: true,
              hasWallThickness: false,
              hasEnclosure: false,
              hasSourceCap: false,
            },
          };
        },
      };
    }

    throw new Error(`Unexpected request URL: ${url}`);
  };

  try {
    const prepared = makePreparedParams({
      encDepth: 180,
      wallThickness: 8,
    });

    const exportTask = await ExportModule.task(
      ExportModule.importStep(prepared, {
        backendUrl: "http://localhost:8000",
        baseName: "demo",
        onStatus(message) {
          statuses.push(message);
        },
      }),
    );
    const files = ExportModule.output.files(exportTask);

    assert.deepEqual(statuses, [
      "Connecting to backend...",
      "Building inner-surface STEP...",
    ]);
    assert.equal(requests.length, 2);
    assert.equal(requests[0].url, "http://localhost:8000/health");
    assert.equal(requests[1].url, "http://localhost:8000/api/mesh/step");

    const payload = JSON.parse(requests[1].init.body);
    assert.equal(payload.step_body, "inner_surface");
    assert.equal(payload.enc_depth, 0);
    assert.equal(payload.wall_thickness, 0);
    assert.equal(payload.quadrants, 1234);
    assert.equal(payload.n_angular, 100);
    assert.equal(payload.n_length, 10);
    assert.equal(payload.corner_segments, 8);

    assert.equal(files.length, 1);
    assert.equal(files[0].fileName, "demo.step");
    assert.equal(files[0].content.includes("ISO-10303-21"), true);
    assert.equal(files[0].saveOptions.contentType, "model/step");
    assert.deepEqual(files[0].stats, {
      singleLayer: true,
      hasWallThickness: false,
      hasEnclosure: false,
      hasSourceCap: false,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("ExportModule STEP task tolerates health payloads without mesherReady", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];

  globalThis.fetch = async (url, init) => {
    requests.push({ url, init });

    if (url.endsWith("/health")) {
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        async json() {
          return { status: "ok" };
        },
      };
    }

    if (url.endsWith("/api/mesh/step")) {
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        async json() {
          return {
            step: "ISO-10303-21;\nEND-ISO-10303-21;\n",
            generatedBy: "hornlab-waveguide-mesher",
            stats: {},
          };
        },
      };
    }

    throw new Error(`Unexpected request URL: ${url}`);
  };

  try {
    const prepared = makePreparedParams();

    const exportTask = await ExportModule.task(
      ExportModule.importStep(prepared, {
        backendUrl: "http://localhost:8000",
        baseName: "demo",
      }),
    );
    const files = ExportModule.output.files(exportTask);

    assert.deepEqual(
      requests.map((request) => request.url),
      ["http://localhost:8000/health", "http://localhost:8000/api/mesh/step"],
    );
    assert.equal(files[0].fileName, "demo.step");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("ExportModule HornLab mesh task requires an explicit backend URL", async () => {
  const prepared = makePreparedParams({ encDepth: 180 });

  await assert.rejects(
    () =>
      ExportModule.task(
        ExportModule.importHornlabMesherMeshBuild(prepared),
      ),
    /requires a backendUrl/,
  );
});

test("ExportModule HornLab mesh task retries a transient backend health failure once", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];

  globalThis.fetch = async (url, init) => {
    requests.push({ url, init });

    if (url.endsWith("/health") && requests.length === 1) {
      return {
        ok: false,
        status: 503,
        statusText: "Service Unavailable",
        async json() {
          return { status: "starting" };
        },
      };
    }

    if (url.endsWith("/health")) {
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        async json() {
          return { status: "ok" };
        },
      };
    }

    if (url.endsWith("/api/mesh/build")) {
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        async json() {
          return {
            msh: "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
            generatedBy: "hornlab-waveguide-mesher",
            stats: { nodeCount: 3, elementCount: 1 },
          };
        },
      };
    }

    throw new Error(`Unexpected request URL: ${url}`);
  };

  try {
    const prepared = makePreparedParams({ encDepth: 180 });

    await ExportModule.task(
      ExportModule.importHornlabMesherMeshBuild(prepared, {
        backendUrl: "http://localhost:8000",
      }),
      { healthRetryDelayMs: 0 },
    );

    assert.deepEqual(
      requests.map((request) => request.url),
      [
        "http://localhost:8000/health",
        "http://localhost:8000/health",
        "http://localhost:8000/api/mesh/build",
      ],
    );
    assert.equal(requests.every((request) => request.init?.signal instanceof AbortSignal), true);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("ExportModule HornLab mesh task surfaces dependency doctor guidance before mesh build when mesh runtime is blocked", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];

  globalThis.fetch = async (url) => {
    requests.push(url);

    if (url.endsWith("/health")) {
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        async json() {
          return {
            status: "ok",
            mesherReady: false,
            dependencyDoctor: {
              components: [
                {
                  id: "gmsh_python",
                  name: "Gmsh Python API",
                  category: "required",
                  status: "missing",
                  featureImpact:
                    "/api/mesh/build and backend meshing are unavailable.",
                  guidance: [
                    "Install gmsh package: pip install -r server/requirements-gmsh.txt",
                  ],
                },
              ],
            },
          };
        },
      };
    }

    throw new Error(`Unexpected request URL: ${url}`);
  };

  try {
    const prepared = makePreparedParams({ encDepth: 180 });

    await assert.rejects(
      () =>
        ExportModule.task(
          ExportModule.importHornlabMesherMeshBuild(prepared, {
            backendUrl: "http://localhost:8000",
          }),
        ),
      /Install gmsh package/,
    );

    assert.deepEqual(requests, ["http://localhost:8000/health"]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
