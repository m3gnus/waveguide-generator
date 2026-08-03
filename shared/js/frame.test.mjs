import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { decodeFrame, FrameDecodeError } from "./frame.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const fixtureRoot = path.resolve(here, "../frame-fixtures");
const manifest = JSON.parse(await readFile(path.join(fixtureRoot, "manifest.json"), "utf8"));
const good = manifest.fixtures.filter((item) => item.outcome === "ok");
const malformed = manifest.fixtures.filter((item) => item.outcome !== "ok");

for (const fixture of good) {
  test(`decodes ${fixture.file}`, async () => {
    const data = await readFile(path.join(fixtureRoot, fixture.file));
    const { header, sections } = decodeFrame(data);
    assert.equal(header.kind, fixture.kind);
    const shapes = Object.fromEntries(header.sections.map((item) => [item.name, item.shape]));
    assert.deepEqual(shapes, fixture.sectionShapes);
    for (const section of Object.values(sections)) assert.equal(section.buffer, data.buffer);
    for (const check of fixture.spotChecks) {
      assert.ok(Math.abs(sections[check.section][check.index] - check.value) <= 1e-6);
    }
    if (header.kind === "preview") {
      for (const surface of header.surfaces) {
        const positions = sections[surface.positions];
        for (const index of sections[surface.indices]) assert.ok(index < positions.length / 3);
      }
    }
  });
}

for (const fixture of malformed) {
  test(`rejects ${fixture.file} as ${fixture.outcome}`, async () => {
    const data = await readFile(path.join(fixtureRoot, fixture.file));
    assert.throws(
      () => decodeFrame(data),
      (error) => error instanceof FrameDecodeError && error.rule === fixture.outcome,
    );
  });
}

function prng(seed) {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 2 ** 32;
  };
}

test("random truncations of every good fixture are clean errors", async () => {
  const random = prng(0x57474631);
  for (const fixture of good) {
    const data = await readFile(path.join(fixtureRoot, fixture.file));
    for (let iteration = 0; iteration < 64; iteration += 1) {
      const cut = Math.floor(random() * data.length);
      assert.throws(() => decodeFrame(data.subarray(0, cut)), FrameDecodeError);
    }
  }
});

test("random bit flips of every good fixture never crash", async () => {
  const random = prng(0x11f00d);
  for (const fixture of good) {
    const original = await readFile(path.join(fixtureRoot, fixture.file));
    for (let iteration = 0; iteration < 128; iteration += 1) {
      const changed = Uint8Array.from(original);
      const offset = Math.floor(random() * changed.length);
      changed[offset] ^= 1 << Math.floor(random() * 8);
      try {
        decodeFrame(changed);
      } catch (error) {
        assert.ok(error instanceof FrameDecodeError, `unexpected crash: ${error}`);
      }
    }
  }
});
