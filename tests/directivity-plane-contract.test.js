import test from "node:test";
import assert from "node:assert/strict";

import {
  exportAsPolarCSV,
  exportAsVACSSpectrum,
} from "../src/ui/simulation/exports.js";
import { renderSolveStatsSummary } from "../src/ui/simulation/results.js";

test("renderSolveStatsSummary derives axes from directivity result keys when metadata omits enabled_axes", () => {
  const markup = renderSolveStatsSummary({
    frequencies: [100],
    directivity: {
      diagonal: [[[0, 0], [30, -2]]],
    },
    metadata: {
      performance: { total_time_seconds: 1.2 },
      directivity: {
        diagonal_angle_degrees: 33,
      },
    },
  });

  assert.match(markup, /Axes/);
  assert.match(markup, /Diagonal/);
  assert.match(markup, /Diagonal plane/);
  assert.match(markup, /33°/);
});

test("exportAsPolarCSV serializes only present directivity planes", async () => {
  const panel = {
    currentSmoothing: "none",
    lastResults: {
      spl_on_axis: { frequencies: [100, 200], spl: [90, 91] },
      directivity: {
        diagonal: [
          [[0, 0], [30, -3]],
          [[0, -1], [30, null]],
        ],
      },
    },
  };

  let writtenFile = null;
  const files = await exportAsPolarCSV(panel, {
    baseName: "horn_12",
    writer: async (file) => {
      writtenFile = file;
      return file.fileName;
    },
  });

  assert.deepEqual(files, ["horn_12_polar.csv"]);
  assert.ok(writtenFile);
  assert.match(writtenFile.content, /Frequency_Hz,Plane,Theta_deg,SPL_norm_dB/);
  assert.match(writtenFile.content, /100,diagonal,0,0\.00/);
  assert.match(writtenFile.content, /100,diagonal,30,-3\.00/);
  assert.match(writtenFile.content, /200,diagonal,30,\n/);
  assert.doesNotMatch(writtenFile.content, /,horizontal,/);
  assert.doesNotMatch(writtenFile.content, /,vertical,/);
});

test("exportAsVACSSpectrum falls back to the first available plane when horizontal is missing", async () => {
  const panel = {
    currentSmoothing: "none",
    lastResults: {
      spl_on_axis: { frequencies: [100], spl: [90] },
      impedance: { frequencies: [100], real: [6], imaginary: [1] },
      directivity: {
        vertical: [[[0, 0], [45, -6]]],
      },
    },
  };

  let writtenFile = null;
  const files = await exportAsVACSSpectrum(panel, {
    baseName: "horn_34",
    writer: async (file) => {
      writtenFile = file;
      return file.fileName;
    },
  });

  assert.deepEqual(files, ["horn_34_spectrum.txt"]);
  assert.ok(writtenFile);
  assert.match(
    writtenFile.content,
    /Data_Legend="Polar, Pressure, Vertical \(far-field\)"/,
  );
  assert.match(writtenFile.content, /Param_Identifier="WG_Polar_V"/);
  assert.doesNotMatch(writtenFile.content, /Param_Identifier="WG_Polar_H"/);
});
