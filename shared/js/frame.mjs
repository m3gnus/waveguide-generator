import ruleIds from "../frame-rules.json" with { type: "json" };

export const DEFAULT_MAX_FRAME_BYTES = 32 * 1024 * 1024;
export const MAX_HEADER_BYTES = 64 * 1024;
export const MAX_SECTION_ELEMENTS = 2 ** 28;
export const MAX_SECTION_RANK = 64;
export const NORMAL_LENGTH_TOLERANCE = 1e-3;

const RULE_IDS = new Set(ruleIds);
const DTYPES = Object.freeze({
  f32: { bytes: 4, ctor: Float32Array },
  f64: { bytes: 8, ctor: Float64Array },
  u32: { bytes: 4, ctor: Uint32Array },
  u16: { bytes: 2, ctor: Uint16Array },
  u8: { bytes: 1, ctor: Uint8Array },
  i32: { bytes: 4, ctor: Int32Array },
});

export class FrameDecodeError extends Error {
  constructor(rule, detail = "") {
    if (!RULE_IDS.has(rule)) throw new Error(`unknown frame rule id: ${rule}`);
    super(detail ? `${rule}: ${detail}` : rule);
    this.name = "FrameDecodeError";
    this.rule = rule;
    this.detail = detail;
  }
}

function fail(rule, detail = "") {
  throw new FrameDecodeError(rule, detail);
}

function align8(value) {
  return Math.ceil(value / 8) * 8;
}

function asBytes(data) {
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  if (ArrayBuffer.isView(data)) return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  throw new TypeError("data must be an ArrayBuffer or an ArrayBuffer view");
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function asSafeInteger(value) {
  return typeof value === "number" && Number.isSafeInteger(value) ? value : null;
}

function descriptors(header, payloadLength) {
  if (!Array.isArray(header.sections)) fail("sections-invalid", "sections must be a list");
  const result = [];
  const names = new Set();
  let expectedOffset = 0;

  for (let index = 0; index < header.sections.length; index += 1) {
    const raw = header.sections[index];
    if (!isObject(raw)) fail("sections-invalid", `section ${index} must be an object`);
    const { name } = raw;
    if (typeof name !== "string" || name.length === 0) fail("section-name", String(index));
    if (names.has(name)) fail("section-duplicate", name);
    names.add(name);

    const dtype = typeof raw.dtype === "string" ? DTYPES[raw.dtype] : undefined;
    if (!dtype) fail("section-dtype", `${name}: ${String(raw.dtype)}`);
    if (!Array.isArray(raw.shape) || raw.shape.length > MAX_SECTION_RANK) {
      fail("section-shape", name);
    }
    const shape = raw.shape.map(asSafeInteger);
    if (shape.some((dim) => dim === null || dim < 0)) fail("section-shape", name);
    let count = 1;
    for (const dim of shape) {
      if (dim > MAX_SECTION_ELEMENTS) fail("section-elements", name);
      count *= dim;
      if (count > MAX_SECTION_ELEMENTS) fail("section-elements", name);
    }
    const offset = asSafeInteger(raw.byteOffset);
    const length = asSafeInteger(raw.byteLength);
    if (offset === null || length === null || offset < 0 || length < 0) {
      fail("section-range", name);
    }
    if (length !== count * dtype.bytes) fail("section-length", name);
    if (offset > payloadLength || length > payloadLength - offset) fail("section-range", name);
    if (offset % 8 !== 0) fail("section-alignment", name);
    if (offset !== expectedOffset) {
      if (offset < expectedOffset) fail("section-overlap", name);
      fail("section-order", name);
    }
    expectedOffset = align8(offset + length);
    result.push({ name, dtypeName: raw.dtype, dtype, shape, offset, length, count });
  }
  if (payloadLength !== expectedOffset) fail("section-range", "payload has missing or trailing bytes");
  return result;
}

function requireSection(meta, surface, field, missingRule) {
  const name = surface[field];
  if (typeof name !== "string" || name.length === 0 || !meta.has(name)) fail(missingRule, String(name));
  return meta.get(name);
}

function validateFidelity(header) {
  const value = header.fidelity;
  if (!isObject(value)) fail("fidelity-invalid", "preview fidelity must be an object");
  const floatFields = [
    "maxChordErrorMmRequested",
    "maxNormalStepDegRequested",
    "maxNormalStepDegAchieved",
  ];
  const intFields = ["minSilhouetteSegmentsRequested", "minSilhouetteSegmentsAchieved"];
  for (const field of floatFields) {
    if (typeof value[field] !== "number" || !Number.isFinite(value[field]) || value[field] < 0) {
      fail("fidelity-invalid", field);
    }
  }
  for (const field of intFields) {
    if (!Number.isSafeInteger(value[field]) || value[field] < 0) fail("fidelity-invalid", field);
  }
  const complete = value.chordMeasurementComplete;
  const unmeasured = value.unmeasuredChordIntervals;
  if (typeof complete !== "boolean" || !Number.isSafeInteger(unmeasured) || unmeasured < 0) {
    fail("fidelity-invalid", "chord measurement completeness");
  }
  if (complete) {
    if (typeof value.maxChordErrorMmAchieved !== "number" || !Number.isFinite(value.maxChordErrorMmAchieved)
      || value.maxChordErrorMmAchieved < 0 || unmeasured !== 0) fail("fidelity-invalid", "maxChordErrorMmAchieved");
  } else if (value.maxChordErrorMmAchieved !== null || unmeasured < 1) {
    fail("fidelity-invalid", "maxChordErrorMmAchieved");
  }
  if (value.vertexCapLimited !== undefined && typeof value.vertexCapLimited !== "boolean") {
    fail("fidelity-invalid", "vertexCapLimited");
  }
  const missed = !complete
    || (value.maxChordErrorMmAchieved !== null && value.maxChordErrorMmAchieved > value.maxChordErrorMmRequested)
    || value.maxNormalStepDegAchieved > value.maxNormalStepDegRequested
    || value.minSilhouetteSegmentsAchieved < value.minSilhouetteSegmentsRequested;
  if (missed && value.vertexCapLimited !== true) fail("fidelity-cap-unreported");
}

function validatePreview(header, sections, descriptorList) {
  if (!Array.isArray(header.surfaces)) fail("preview-surfaces", "surfaces must be a list");
  validateFidelity(header);
  const meta = new Map(descriptorList.map((item) => [item.name, item]));

  for (let surfaceIndex = 0; surfaceIndex < header.surfaces.length; surfaceIndex += 1) {
    const surface = header.surfaces[surfaceIndex];
    if (!isObject(surface)) fail("surface-invalid", String(surfaceIndex));
    if (typeof surface.role !== "string" || surface.role.length === 0) fail("surface-role", String(surfaceIndex));
    if (surface.shading !== "smooth" && surface.shading !== "flat") fail("surface-shading", surface.role);
    if (!["analytic-parametric", "analytic-spline", "exact-planar"].includes(surface.normalMethod)) {
      fail("surface-normal-method", surface.role);
    }
    if (surface.closedPhi !== undefined && typeof surface.closedPhi !== "boolean") {
      fail("surface-closed-phi", surface.role);
    }

    const positionMeta = requireSection(meta, surface, "positions", "surface-section-missing");
    const indexMeta = requireSection(meta, surface, "indices", "surface-section-missing");
    const normalMeta = requireSection(meta, surface, "normals", "normals-missing");
    if (positionMeta.dtypeName !== "f32" || positionMeta.shape.length !== 2 || positionMeta.shape[1] !== 3) {
      fail("surface-position-format", positionMeta.name);
    }
    if (indexMeta.dtypeName !== "u32" || indexMeta.shape.length !== 1) fail("surface-index-format", indexMeta.name);
    if (normalMeta.dtypeName !== "f32" || normalMeta.shape.length !== 2 || normalMeta.shape[1] !== 3) {
      fail("normals-format", normalMeta.name);
    }
    if (normalMeta.shape[0] !== positionMeta.shape[0]) fail("normals-row-mismatch", surface.role);

    const positions = sections[positionMeta.name];
    const normals = sections[normalMeta.name];
    for (const coordinate of positions) {
      if (!Number.isFinite(coordinate)) fail("positions-nonfinite", positionMeta.name);
    }
    for (let row = 0; row < normalMeta.shape[0]; row += 1) {
      const x = normals[row * 3];
      const y = normals[row * 3 + 1];
      const z = normals[row * 3 + 2];
      if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) {
        fail("normals-nonfinite", normalMeta.name);
      }
      if (Math.abs(Math.hypot(x, y, z) - 1) > NORMAL_LENGTH_TOLERANCE) {
        fail("normals-nonunit", normalMeta.name);
      }
    }

    const indices = sections[indexMeta.name];
    const vertexCount = positionMeta.shape[0];
    const check = (start, end) => {
      for (let offset = start; offset < end; offset += 1) {
        if (indices[offset] >= vertexCount) fail("indices-out-of-range", indexMeta.name);
      }
    };
    if (indices.length <= 2048) check(0, indices.length);
    else {
      check(0, 1024);
      check(indices.length - 1024, indices.length);
    }
  }
}

/**
 * Decode a FRAME-SPEC v1.1 message into zero-copy typed-array views.
 * @param {ArrayBuffer|ArrayBufferView} data
 * @param {{maxFrameBytes?: number}} [options]
 * @returns {{header: object, sections: Record<string, TypedArray>}}
 */
export function decodeFrame(data, { maxFrameBytes = DEFAULT_MAX_FRAME_BYTES } = {}) {
  const bytes = asBytes(data);
  if (bytes.byteLength > maxFrameBytes) fail("frame-too-large", `${bytes.byteLength} > ${maxFrameBytes}`);
  if (bytes.byteLength < 4
      || bytes[0] !== 0x57 || bytes[1] !== 0x47 || bytes[2] !== 0x46 || bytes[3] !== 0x31) {
    fail("bad-magic");
  }
  if (bytes.byteLength < 8) fail("header-truncated");
  const dataView = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const headerLength = dataView.getUint32(4, true);
  if (headerLength > MAX_HEADER_BYTES) fail("header-too-large", String(headerLength));
  const headerEnd = 8 + headerLength;
  if (headerEnd > bytes.byteLength) fail("header-truncated");
  const payloadBase = align8(headerEnd);
  if (payloadBase > bytes.byteLength) fail("header-truncated", "payload alignment padding missing");

  let header;
  try {
    const decoder = new TextDecoder("utf-8", { fatal: true });
    header = JSON.parse(decoder.decode(bytes.subarray(8, headerEnd)));
  } catch (error) {
    fail("header-json", error instanceof Error ? error.message : String(error));
  }
  if (!isObject(header)) fail("header-json", "header must be a JSON object");
  if (asSafeInteger(header.v) !== 1) fail("unsupported-version", String(header.v));
  const descriptorList = descriptors(header, bytes.byteLength - payloadBase);
  const sections = Object.create(null);
  for (const item of descriptorList) {
    const absoluteOffset = bytes.byteOffset + payloadBase + item.offset;
    if (absoluteOffset % item.dtype.bytes !== 0) fail("section-alignment", item.name);
    try {
      sections[item.name] = new item.dtype.ctor(bytes.buffer, absoluteOffset, item.count);
    } catch (error) {
      fail("section-range", error instanceof Error ? error.message : String(error));
    }
  }
  if (header.kind === "preview") validatePreview(header, sections, descriptorList);
  return { header, sections };
}

export const decode = decodeFrame;
