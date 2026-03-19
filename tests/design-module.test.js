import test from "node:test";
import assert from "node:assert/strict";

import { getDefaults } from "../src/config/defaults.js";
import { prepareGeometryParams } from "../src/geometry/index.js";
import {
  DesignModule,
  prepareOccExportParams,
  prepareOccSimulationParams,
} from "../src/modules/design/index.js";

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

test("DesignModule OCC normalization outputs centralize simulation/export request prep", () => {
  const prepared = prepareGeometryParams(
    makeRawParams({
      angularSegments: 21.2,
      lengthSegments: 9.1,
      quadrants: "not-a-quadrant",
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
  const occSimulation = DesignModule.output.occSimulationParams(designTask);
  const occExport = DesignModule.output.occExportParams(designTask);

  assert.equal(occSimulation.angularSegments, 21);
  assert.equal(occSimulation.lengthSegments, 10);
  assert.equal(occSimulation.quadrants, 1234);
  assert.equal(occSimulation.throatResolution, 6);
  assert.equal(occSimulation.mouthResolution, 10);
  assert.equal(occSimulation.rearResolution, 14);
  assert.equal(occSimulation.encFrontResolution, "8,10,12,14");
  assert.equal(occSimulation.encBackResolution, "16,18,20,22");

  assert.equal(occExport.angularSegments, 20);
  assert.equal(occExport.lengthSegments, 10);
  assert.equal(occExport.throatResolution, 6);
  assert.equal(occExport.mouthResolution, 10);
  assert.equal(occExport.rearResolution, 14);
  assert.equal(occExport.encFrontResolution, "8,10,12,14");
  assert.equal(occExport.encBackResolution, "16,18,20,22");
  assert.equal(occExport.wallThickness, 5);

  const directOccSimulation = prepareOccSimulationParams(prepared);
  const directOccExport = prepareOccExportParams(prepared);
  assert.equal(
    JSON.stringify(directOccSimulation),
    JSON.stringify(occSimulation),
  );
  assert.equal(JSON.stringify(directOccExport), JSON.stringify(occExport));
});
