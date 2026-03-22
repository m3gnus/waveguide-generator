import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { MWGConfigParser } from "../src/config/index.js";
import { coerceConfigParams } from "../src/geometry/params.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURES_DIR = join(__dirname, "fixtures", "ath");

test("ATH fixture: rosse-simple.cfg parses correctly", () => {
  const content = readFileSync(join(FIXTURES_DIR, "rosse-simple.cfg"), "utf-8");
  const parsed = MWGConfigParser.parse(content);

  assert.equal(parsed.type, "R-OSSE");
  assert.ok(parsed.params.R, "R-OSSE should have R parameter");
  assert.ok(
    parsed.params.R.includes("cos(p)"),
    "R should be a formula with cos(p)",
  );
  assert.equal(parsed.params.r0, "12.7");
  assert.equal(parsed.params.a0, "15.5");
});

test("ATH fixture: osse-simple.cfg parses correctly", () => {
  const content = readFileSync(join(FIXTURES_DIR, "osse-simple.cfg"), "utf-8");
  const parsed = MWGConfigParser.parse(content);

  assert.equal(parsed.type, "OSSE");
  assert.equal(parsed.params.L, "120");
  assert.ok(parsed.params.a, "OSSE should have a parameter");
  assert.equal(parsed.params.r0, "12.7");
  assert.equal(parsed.params.a0, "10");
});

test("ATH fixture: osse-with-enclosure.cfg parses correctly", () => {
  const content = readFileSync(
    join(FIXTURES_DIR, "osse-with-enclosure.cfg"),
    "utf-8",
  );
  const parsed = MWGConfigParser.parse(content);

  assert.equal(parsed.type, "OSSE");
  assert.equal(parsed.params.L, "130");
  assert.ok(
    parsed.blocks["Mesh.Enclosure"],
    "Should have Mesh.Enclosure block",
  );
  assert.equal(parsed.blocks["Mesh.Enclosure"]._items.Depth, "280");
  assert.equal(parsed.blocks["Mesh.Enclosure"]._items.EdgeRadius, "18");
});

test("ATH fixture: rosse-simple.cfg coerces to valid params", () => {
  const content = readFileSync(join(FIXTURES_DIR, "rosse-simple.cfg"), "utf-8");
  const parsed = MWGConfigParser.parse(content);
  const coerced = coerceConfigParams(parsed.params);

  assert.equal(typeof coerced.scale, "number");
  assert.equal(coerced.scale, 1.0);
  assert.equal(typeof coerced.r0, "number");
  assert.equal(coerced.r0, 12.7);
  assert.equal(typeof coerced.k, "number");
  assert.equal(coerced.k, 2.0);
});

test("ATH fixture: osse-simple.cfg coerces to valid params", () => {
  const content = readFileSync(join(FIXTURES_DIR, "osse-simple.cfg"), "utf-8");
  const parsed = MWGConfigParser.parse(content);
  const coerced = coerceConfigParams(parsed.params);

  assert.equal(typeof coerced.scale, "number");
  assert.equal(coerced.scale, 1.0);
  assert.equal(typeof coerced.L, "number");
  assert.equal(coerced.L, 120);
  assert.equal(typeof coerced.k, "number");
  assert.equal(coerced.k, 7.0);
});

test("ATH fixture: osse-with-enclosure.cfg enclosure params are extracted", () => {
  const content = readFileSync(
    join(FIXTURES_DIR, "osse-with-enclosure.cfg"),
    "utf-8",
  );
  const parsed = MWGConfigParser.parse(content);
  const coerced = coerceConfigParams(parsed.params);

  assert.equal(parsed.params.encDepth, "280");
  assert.equal(parsed.params.encEdge, "18");
  assert.equal(typeof coerced.encDepth, "number");
  assert.equal(coerced.encDepth, 280);
});
