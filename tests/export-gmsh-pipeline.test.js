import test from "node:test";
import assert from "node:assert/strict";

import { getDefaults } from "../src/config/defaults.js";
import { prepareGeometryParams } from "../src/geometry/index.js";
import { exportSTEP, prepareExportArtifacts } from "../src/modules/export/useCases.js";

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

test("prepareExportArtifacts requests backend HornLab meshing endpoint", async () => {
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
    const backendUrl = "http://localhost:8000";

    const prepared = makePreparedParams({ encDepth: 180 });
    const result = await prepareExportArtifacts(prepared, { backendUrl });

    assert.equal(result.msh.includes("$MeshFormat"), true);
    assert.equal(requests.length, 2);
    assert.equal(requests[0].url, "http://localhost:8000/health");
    assert.equal(requests[1].url, "http://localhost:8000/api/mesh/build");

    const payload = JSON.parse(requests[1].init.body);
    assert.equal(payload.formula_type, "OSSE");
    assert.equal(payload.n_angular, 20);
    assert.equal(payload.n_length, 10);

    assert.equal(typeof result.msh, "string");
    assert.equal(result.msh.includes("$MeshFormat"), true);
    assert.equal(typeof result.meshStats, "object");
    assert.equal(result.meshStats.nodeCount, 3);
    assert.equal(result.meshStats.elementCount, 1);
    assert.equal(typeof result.artifacts, "object");
    assert.equal(typeof result.payload, "object");
    assert.ok(Array.isArray(result.payload.surfaceTags));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("exportSTEP writes backend single-layer STEP file", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  const written = [];

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
    const state = {
      type: "OSSE",
      params: {
        ...getDefaults("OSSE"),
        type: "OSSE",
        L: "100",
        a: "50",
        a0: "15",
        r0: "12.7",
        encDepth: 240,
        wallThickness: 9,
      },
    };

    const result = await exportSTEP({
      state,
      baseName: "demo",
      backendUrl: "http://localhost:8000",
      writeFile: async (file) => {
        written.push(file);
        return file.fileName;
      },
    });

    assert.deepEqual(result, ["demo.step"]);
    assert.equal(written.length, 1);
    assert.equal(written[0].fileName, "demo.step");
    assert.equal(written[0].content.includes("ISO-10303-21"), true);

    assert.deepEqual(
      requests.map((request) => request.url),
      ["http://localhost:8000/health", "http://localhost:8000/api/mesh/step"],
    );
    const payload = JSON.parse(requests[1].init.body);
    assert.equal(payload.enc_depth, 0);
    assert.equal(payload.wall_thickness, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
