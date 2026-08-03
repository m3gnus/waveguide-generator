/** Parsed subset of a Gmsh 2.2 ASCII mesh used by the viewport. */
export interface ParsedMSH {
  vertices: Float32Array;
  indices: Uint32Array;
  physicalNames: Map<number, string>;
  physicalTags: Uint32Array;
}

/**
 * Parse a Gmsh 2.2 ASCII MSH file. This is the typed v2 port of the v1 parser:
 * node ids may be sparse, non-triangle elements are ignored, and the first
 * element tag is retained as its physical-surface id.
 */
export function parseMSH(text: string): ParsedMSH {
  const lines = text.split('\n');
  let cursor = 0;

  const nextLine = (): string | null => {
    while (cursor < lines.length) {
      const line = lines[cursor++].trim();
      if (line.length > 0) return line;
    }
    return null;
  };
  const advanceTo = (marker: string): boolean => {
    while (cursor < lines.length) {
      if (lines[cursor++].trim() === marker) return true;
    }
    return false;
  };
  const parseCount = (line: string | null, label: string): number => {
    if (!/^\d+$/.test(line ?? '')) throw new Error(`Invalid ${label}: ${line}`);
    const count = Number(line);
    if (!Number.isSafeInteger(count)) throw new Error(`Invalid ${label}: ${line}`);
    return count;
  };
  const parseInteger = (value: string | undefined, label: string, minimum = 0): number => {
    if (!/^[+-]?\d+$/.test(value ?? '')) throw new Error(`Invalid ${label}: ${value}`);
    const result = Number(value);
    if (!Number.isSafeInteger(result) || result < minimum) throw new Error(`Invalid ${label}: ${value}`);
    return result;
  };
  const expectEnd = (marker: string): void => {
    if (nextLine() !== marker) throw new Error(`Missing ${marker}`);
  };

  if (!advanceTo('$MeshFormat')) throw new Error('Missing $MeshFormat section');
  const formatLine = nextLine();
  const formatParts = formatLine?.split(/\s+/) ?? [];
  if (formatParts[0] !== '2.2') throw new Error(`Unsupported or missing mesh format version: ${formatLine}`);
  if (formatParts[1] !== '0') throw new Error('Only ASCII Gmsh 2.2 meshes are supported');
  expectEnd('$EndMeshFormat');

  const physicalNames = new Map<number, string>();
  const savedCursor = cursor;
  if (advanceTo('$PhysicalNames')) {
    const count = parseCount(nextLine(), 'physical name count');
    for (let index = 0; index < count; index += 1) {
      const line = nextLine();
      if (!line) throw new Error('Unexpected end in $PhysicalNames');
      const match = line.match(/^\s*(\d+)\s+(\d+)\s+"([^"]*)"\s*$/);
      if (match?.[1] === '2') physicalNames.set(Number(match[2]), match[3]);
    }
    expectEnd('$EndPhysicalNames');
  } else {
    cursor = savedCursor;
  }

  if (!advanceTo('$Nodes')) throw new Error('Missing $Nodes section');
  const nodeCount = parseCount(nextLine(), 'node count');
  const vertices = new Float32Array(nodeCount * 3);
  const idToIndex = new Map<number, number>();
  for (let index = 0; index < nodeCount; index += 1) {
    const line = nextLine();
    if (!line) throw new Error('Unexpected end in $Nodes');
    const parts = line.split(/\s+/);
    const id = parseInteger(parts[0], 'node id', 1);
    if (idToIndex.has(id)) throw new Error(`Duplicate node id: ${id}`);
    const coordinates = parts.slice(1, 4).map(Number);
    if (coordinates.length !== 3 || coordinates.some((coordinate) => !Number.isFinite(coordinate))) {
      throw new Error(`Invalid node coordinates for node ${id}`);
    }
    vertices.set(coordinates, index * 3);
    idToIndex.set(id, index);
  }
  expectEnd('$EndNodes');

  if (!advanceTo('$Elements')) throw new Error('Missing $Elements section');
  const elementCount = parseCount(nextLine(), 'element count');
  const triangleIndices = new Uint32Array(elementCount * 3);
  const triangleTags = new Uint32Array(elementCount);
  let triangleCount = 0;
  for (let index = 0; index < elementCount; index += 1) {
    const line = nextLine();
    if (!line) throw new Error('Unexpected end in $Elements');
    const parts = line.split(/\s+/);
    const elementId = parseInteger(parts[0], 'element id', 1);
    const elementType = parseInteger(parts[1], `element type for element ${elementId}`, 1);
    const tagCount = parseInteger(parts[2], `tag count for element ${elementId}`);
    if (parts.length < 3 + tagCount) throw new Error(`Invalid tag data for element ${elementId}`);
    if (elementType !== 2) continue;

    const physicalTag = tagCount > 0 ? parseInteger(parts[3], `physical tag for element ${elementId}`) : 0;
    const nodeOffset = 3 + tagCount;
    const nodeIds = parts.slice(nodeOffset, nodeOffset + 3).map((value) => parseInteger(value, `node id for element ${elementId}`, 1));
    if (nodeIds.length !== 3) throw new Error(`Missing node ids for triangle element ${elementId}`);
    const target = triangleCount * 3;
    nodeIds.forEach((nodeId, corner) => {
      const nodeIndex = idToIndex.get(nodeId);
      if (nodeIndex === undefined) throw new Error(`Unknown node id ${nodeId} in triangle element ${elementId}`);
      triangleIndices[target + corner] = nodeIndex;
    });
    triangleTags[triangleCount] = physicalTag;
    triangleCount += 1;
  }
  expectEnd('$EndElements');

  return {
    vertices,
    indices: triangleIndices.slice(0, triangleCount * 3),
    physicalNames,
    physicalTags: triangleTags.slice(0, triangleCount),
  };
}
