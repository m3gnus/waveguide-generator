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

function frameWithHeaderBytes(headerBytes, payload = new Uint8Array()) {
  const prefixLength = 8 + headerBytes.length;
  const payloadBase = Math.ceil(prefixLength / 8) * 8;
  const frame = new Uint8Array(payloadBase + payload.length);
  frame.set([0x57, 0x47, 0x46, 0x31]);
  new DataView(frame.buffer).setUint32(4, headerBytes.length, true);
  frame.set(headerBytes, 8);
  frame.set(payload, payloadBase);
  return frame;
}

function rewriteHeader(data, transform) {
  const bytes = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  const headerLength = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getUint32(4, true);
  const payloadBase = Math.ceil((8 + headerLength) / 8) * 8;
  const text = new TextDecoder().decode(bytes.subarray(8, 8 + headerLength));
  const rewritten = new TextEncoder().encode(transform(text));
  return frameWithHeaderBytes(rewritten, bytes.slice(payloadBase));
}

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

test("zero-product shapes still validate rank and individual dimensions", async () => {
  const data = await readFile(path.join(fixtureRoot, "good/curve.bin"));
  for (const shape of [Array(65).fill(0), [0, 2 ** 28 + 1]]) {
    const changed = rewriteHeader(data, (text) => {
      const header = JSON.parse(text);
      header.sections[0].shape = shape;
      return JSON.stringify(header);
    });
    assert.throws(
      () => decodeFrame(changed),
      (error) => error instanceof FrameDecodeError
        && ["section-shape", "section-elements"].includes(error.rule),
    );
  }
});

test("integral decimal JSON fields follow the shared safe-integer predicate", async () => {
  const data = await readFile(path.join(fixtureRoot, "good/curve.bin"));
  const changed = rewriteHeader(data, (text) => text
    .replace('"v":1', '"v":1.0')
    .replace(/"shape":\[([^\]]+)\]/g, (_match, values) => (
      `"shape":[${values.split(",").map((value) => `${value}.0`).join(",")}]`
    ))
    .replace(/"byteOffset":(\d+)/g, '"byteOffset":$1.0')
    .replace(/"byteLength":(\d+)/g, '"byteLength":$1.0'));
  const { header, sections } = decodeFrame(changed);
  assert.equal(header.v, 1);
  assert.equal(Object.values(sections)[0].length, 4);
});
