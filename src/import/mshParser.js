/**
 * Gmsh 2.2 MSH text-format parser.
 *
 * Extracts vertices, triangle indices (0-based), physical group names,
 * and per-triangle physical tags from a Gmsh 2.2 ASCII mesh file.
 */

import { createPerfTimer } from '../logging/performance.js';

/**
 * Parse a Gmsh 2.2 MSH text string.
 *
 * @param {string} text - Full contents of a .msh file
 * @returns {{
 *   vertices: Float32Array,
 *   indices: Uint32Array,
 *   physicalNames: Map<number, string>,
 *   physicalTags: Uint32Array
 * }}
 */
export function parseMSH(text) {
  const perf = createPerfTimer('parseMSH');
  const lines = text.split('\n');
  perf.mark('split-lines', { lineCount: lines.length });
  let cursor = 0;

  const nextLine = () => {
    while (cursor < lines.length) {
      const line = lines[cursor++].trim();
      if (line.length > 0) return line;
    }
    return null;
  };

  const advanceTo = (marker) => {
    while (cursor < lines.length) {
      const line = lines[cursor++].trim();
      if (line === marker) return true;
    }
    return false;
  };

  const parseCount = (line, label) => {
    if (!/^\d+$/.test(line || '')) {
      throw new Error(`Invalid ${label}: ${line}`);
    }
    const count = Number(line);
    if (!Number.isSafeInteger(count)) {
      throw new Error(`Invalid ${label}: ${line}`);
    }
    return count;
  };

  const parseInteger = (value, label, minimum = 0) => {
    if (!/^[+-]?\d+$/.test(value || '')) {
      throw new Error(`Invalid ${label}: ${value}`);
    }
    const result = Number(value);
    if (!Number.isSafeInteger(result) || result < minimum) {
      throw new Error(`Invalid ${label}: ${value}`);
    }
    return result;
  };

  const expectEnd = (marker) => {
    if (nextLine() !== marker) {
      throw new Error(`Missing ${marker}`);
    }
  };

  // --- $MeshFormat ---
  if (!advanceTo('$MeshFormat')) {
    throw new Error('Missing $MeshFormat section');
  }
  const formatLine = nextLine();
  const formatParts = formatLine ? formatLine.split(/\s+/) : [];
  if (formatParts[0] !== '2.2') {
    throw new Error(`Unsupported or missing mesh format version: ${formatLine}`);
  }
  if (formatParts[1] !== '0') {
    throw new Error('Only ASCII Gmsh 2.2 meshes are supported');
  }
  expectEnd('$EndMeshFormat');
  perf.mark('mesh-format');

  // --- $PhysicalNames (optional) ---
  const physicalNames = new Map();
  const savedCursor = cursor;
  if (advanceTo('$PhysicalNames')) {
    const countLine = nextLine();
    const count = parseCount(countLine, 'physical name count');
    for (let i = 0; i < count; i++) {
      const pline = nextLine();
      if (!pline) throw new Error('Unexpected end in $PhysicalNames');
      // format: <dim> <id> "<name>"
      const match = pline.match(/^\s*(\d+)\s+(\d+)\s+"([^"]*)"\s*$/);
      if (match && match[1] === '2') {
        physicalNames.set(Number(match[2]), match[3]);
      }
    }
    expectEnd('$EndPhysicalNames');
  } else {
    // Rewind if $PhysicalNames not found — it's optional
    cursor = savedCursor;
  }
  perf.mark('physical-names', { physicalNameCount: physicalNames.size });

  // --- $Nodes ---
  if (!advanceTo('$Nodes')) {
    throw new Error('Missing $Nodes section');
  }
  const nodeCountLine = nextLine();
  const nodeCount = parseCount(nodeCountLine, 'node count');

  const vertices = new Float32Array(nodeCount * 3);
  const idToIndex = new Map();
  let maxNodeId = 0;
  for (let i = 0; i < nodeCount; i++) {
    const nline = nextLine();
    if (!nline) throw new Error('Unexpected end in $Nodes');
    const parts = nline.split(/\s+/);
    const id = parseInteger(parts[0], 'node id', 1);
    if (idToIndex.has(id)) {
      throw new Error(`Duplicate node id: ${id}`);
    }
    const coordinates = parts.slice(1, 4).map(Number);
    if (
      coordinates.length !== 3 ||
      coordinates.some((coordinate) => !Number.isFinite(coordinate))
    ) {
      throw new Error(`Invalid node coordinates for node ${id}`);
    }
    const [x, y, z] = coordinates;
    vertices[i * 3] = x;
    vertices[i * 3 + 1] = y;
    vertices[i * 3 + 2] = z;
    idToIndex.set(id, i);
    if (id > maxNodeId) maxNodeId = id;
  }
  expectEnd('$EndNodes');
  perf.mark('nodes-read', { nodeCount, maxNodeId });
  perf.mark('vertices-built', { vertexCount: nodeCount });

  // --- $Elements ---
  if (!advanceTo('$Elements')) {
    throw new Error('Missing $Elements section');
  }
  const elemCountLine = nextLine();
  const elemCount = parseCount(elemCountLine, 'element count');

  const triIndices = new Uint32Array(elemCount * 3);
  const triTags = new Uint32Array(elemCount);
  let triCount = 0;

  for (let i = 0; i < elemCount; i++) {
    const eline = nextLine();
    if (!eline) throw new Error('Unexpected end in $Elements');
    const parts = eline.split(/\s+/);
    // parts: [id, type, num-tags, tag1, tag2, ..., n1, n2, n3]
    const elementId = parseInteger(parts[0], 'element id', 1);
    const elemType = parseInteger(parts[1], `element type for element ${elementId}`, 1);
    const numTags = parseInteger(parts[2], `tag count for element ${elementId}`);
    if (parts.length < 3 + numTags) {
      throw new Error(`Invalid tag data for element ${elementId}`);
    }
    if (elemType !== 2) continue; // skip non-triangle elements

    const physicalTag =
      numTags > 0 ? parseInteger(parts[3], `physical tag for element ${elementId}`) : 0;
    const nodeOffset = 3 + numTags;
    const nodeIds = parts
      .slice(nodeOffset, nodeOffset + 3)
      .map((value) => parseInteger(value, `node id for element ${elementId}`, 1));
    if (nodeIds.length !== 3) {
      throw new Error(`Missing node ids for triangle element ${elementId}`);
    }
    const nodeIndices = nodeIds.map((nodeId) => {
      const nodeIndex = idToIndex.get(nodeId);
      if (nodeIndex === undefined) {
        throw new Error(`Unknown node id ${nodeId} in triangle element ${elementId}`);
      }
      return nodeIndex;
    });

    const triOffset = triCount * 3;
    triIndices[triOffset] = nodeIndices[0];
    triIndices[triOffset + 1] = nodeIndices[1];
    triIndices[triOffset + 2] = nodeIndices[2];
    triTags[triCount] = physicalTag;
    triCount++;
  }
  perf.mark('elements-read', { elementCount: elemCount, triangleCount: triCount });

  expectEnd('$EndElements');

  const result = {
    vertices,
    indices: triIndices.slice(0, triCount * 3),
    physicalNames,
    physicalTags: triTags.slice(0, triCount),
  };
  perf.end({
    vertexCount: result.vertices.length / 3,
    triangleCount: result.indices.length / 3,
  });
  return result;
}
