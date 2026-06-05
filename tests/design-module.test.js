import test from "node:test";
import assert from "node:assert/strict";

import { getDefaults } from "../src/config/defaults.js";
import { prepareGeometryParams } from "../src/geometry/index.js";
import {
  DesignModule,
  prepareBackendMeshExportParams,
  prepareBackendMeshSimulationParams,
} from "../src/modules/design/index.js";
import { resolveAutoQuadrants } from "../src/modules/design/symmetry.js";

function makeRawParams(overrides = {}) {
  return {
    ...getDefaults("OSSE"),
    type: "OSSE",
    L: "120",
    a: "45",
    a0: "15.5",
    r0: "12.7",
    angularSegments: 24,
    lengthSegments: 10,
    ...overrides,
  };
}

test("DesignModule prepares state params with staged import, task, and output parity", () => {
  const rawParams = makeRawParams({ encDepth: 180, quadrants: "1" });
  const designInput = DesignModule.import(rawParams, {
    type: "OSSE",
    applyVerticalOffset: true,
  });
  const designTask = DesignModule.task(designInput);
  const expected = prepareGeometryParams(rawParams, {
    type: "OSSE",
    applyVerticalOffset: true,
  });

  assert.equal(designInput.module, "design");
  assert.equal(designInput.stage, "import");
  assert.equal(designTask.stage, "task");
  assert.equal(
    JSON.stringify(DesignModule.output.preparedParams(designTask)),
    JSON.stringify(expected),
  );
});

test("DesignModule.importState derives type and params from app state", () => {
  const state = {
    type: "OSSE",
    params: makeRawParams({ L: "150", scale: 2 }),
  };

  const designTask = DesignModule.task(
    DesignModule.importState(state, {
      applyVerticalOffset: false,
    }),
  );

  assert.equal(DesignModule.output.preparedParams(designTask).type, "OSSE");
  assert.equal(
    DesignModule.output.preparedParams(designTask).verticalOffset,
    0,
  );
  assert.equal(DesignModule.output.preparedParams(designTask).L, 300);
});

test("DesignModule output helpers preserve pre-prepared params", () => {
  const preparedParams = prepareGeometryParams(
    makeRawParams({
      scale: 2,
      L: "100",
      r0: "10",
    }),
    {
      type: "OSSE",
      applyVerticalOffset: true,
    },
  );

  const designTask = DesignModule.task(
    DesignModule.importPrepared(preparedParams),
  );

  assert.equal(DesignModule.output.preparedParams(designTask), preparedParams);
  assert.equal(DesignModule.output.exportParams(designTask), preparedParams);
  assert.equal(
    DesignModule.output.simulationParams(designTask),
    preparedParams,
  );
});

test("DesignModule backend mesh normalization outputs centralize simulation/export request prep", () => {
  const prepared = prepareGeometryParams(
    makeRawParams({
      angularSegments: 21.2,
      lengthSegments: 9.1,
      quadrants: "1",
      scale: 2,
      throatResolution: 3,
      mouthResolution: 5,
      rearResolution: 7,
      encFrontResolution: [4, 5, 6, 7],
      encBackResolution: [8, 9, 10, 11],
      encDepth: 0,
      wallThickness: 0,
    }),
    { type: "OSSE", applyVerticalOffset: true },
  );

  const designTask = DesignModule.task(DesignModule.importPrepared(prepared));
  const backendMeshSimulation = DesignModule.output.backendMeshSimulationParams(designTask);
  const backendMeshExport = DesignModule.output.backendMeshExportParams(designTask);

  assert.equal(backendMeshSimulation.angularSegments, 21);
  assert.equal(backendMeshSimulation.lengthSegments, 10);
  assert.equal(backendMeshSimulation.quadrants, 1);
  assert.equal(backendMeshSimulation.throatResolution, 6);
  assert.equal(backendMeshSimulation.mouthResolution, 10);
  assert.equal(backendMeshSimulation.rearResolution, 14);
  assert.equal(backendMeshSimulation.encFrontResolution, "4,5,6,7");
  assert.equal(backendMeshSimulation.encBackResolution, "8,9,10,11");

  assert.equal(backendMeshExport.angularSegments, 20);
  assert.equal(backendMeshExport.lengthSegments, 10);
  assert.equal(backendMeshExport.quadrants, 1);
  assert.equal(backendMeshExport.throatResolution, 6);
  assert.equal(backendMeshExport.mouthResolution, 10);
  assert.equal(backendMeshExport.rearResolution, 14);
  assert.equal(backendMeshExport.encFrontResolution, "4,5,6,7");
  assert.equal(backendMeshExport.encBackResolution, "8,9,10,11");
  assert.equal(backendMeshExport.wallThickness, 5);

  const directBackendMeshSimulation = prepareBackendMeshSimulationParams(prepared);
  const directBackendMeshExport = prepareBackendMeshExportParams(prepared);
  assert.equal(
    JSON.stringify(directBackendMeshSimulation),
    JSON.stringify(backendMeshSimulation),
  );
  assert.equal(JSON.stringify(directBackendMeshExport), JSON.stringify(backendMeshExport));
});

test("DesignModule auto quadrants chooses quarter, half, or full from symmetry", () => {
  const quarter = prepareGeometryParams(
    makeRawParams({
      quadrants: "auto",
      a: "45 - 5*cos(2*p)^4",
      s: "0.85 + 0.3*cos(p)^2",
      verticalOffset: 0,
    }),
    { type: "OSSE", applyVerticalOffset: true },
  );
  const half = prepareGeometryParams(
    makeRawParams({
      quadrants: "auto",
      a: "45 - 5*cos(2*p)^4",
      s: "0.85 + 0.3*cos(p)^2",
      verticalOffset: 80,
    }),
    { type: "OSSE", applyVerticalOffset: true },
  );
  const full = prepareGeometryParams(
    makeRawParams({
      quadrants: "auto",
      a: "45 + 2*sin(p) + cos(p)",
      s: "0.85",
      verticalOffset: 0,
    }),
    { type: "OSSE", applyVerticalOffset: true },
  );

  assert.equal(resolveAutoQuadrants(quarter), 1);
  assert.equal(prepareBackendMeshSimulationParams(quarter).quadrants, 1);
  assert.equal(resolveAutoQuadrants(half), 14);
  assert.equal(prepareBackendMeshSimulationParams(half).quadrants, 14);
  assert.equal(resolveAutoQuadrants(full), 1234);
  assert.equal(prepareBackendMeshSimulationParams(full).quadrants, 1234);
});

test("DesignModule exposes only HornLab backend mesh normalization outputs", () => {
  assert.equal(typeof prepareBackendMeshSimulationParams, "function");
  assert.equal(typeof prepareBackendMeshExportParams, "function");
  assert.equal(DesignModule.output.occSimulationParams, undefined);
  assert.equal(DesignModule.output.occExportParams, undefined);
});
