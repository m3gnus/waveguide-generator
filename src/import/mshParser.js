/**
 * Gmsh 2.2 MSH text-format parser.
 *
 * Extracts vertices, triangle indices (0-based), physical group names,
 * and per-triangle physical tags from a Gmsh 2.2 ASCII mesh file.
 */

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
  const lines = text.split('\n');
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

  // --- $MeshFormat ---
  if (!advanceTo('$MeshFormat')) {
    throw new Error('Missing $MeshFormat section');
  }
  const formatLine = nextLine();
  if (!formatLine || !formatLine.startsWith('2.2')) {
    throw new Error(`Unsupported or missing mesh format version: ${formatLine}`);
  }
  if (!advanceTo('$EndMeshFormat')) {
    throw new Error('Missing $EndMeshFormat');
  }

  // --- $PhysicalNames (optional) ---
  const physicalNames = new Map();
  const savedCursor = cursor;
  if (advanceTo('$PhysicalNames')) {
    const countLine = nextLine();
    const count = parseInt(countLine, 10);
    for (let i = 0; i < count; i++) {
      const pline = nextLine();
      if (!pline) throw new Error('Unexpected end in $PhysicalNames');
      // format: <dim> <id> "<name>"
      const match = pline.match(/^\s*(\d+)\s+(\d+)\s+"([^"]*)"\s*$/);
      if (match) {
        physicalNames.set(parseInt(match[2], 10), match[3]);
      }
    }
    if (!advanceTo('$EndPhysicalNames')) {
      throw new Error('Missing $EndPhysicalNames');
    }
  } else {
    // Rewind if $PhysicalNames not found — it's optional
    cursor = savedCursor;
  }

  // --- $Nodes ---
  if (!advanceTo('$Nodes')) {
    throw new Error('Missing $Nodes section');
  }
  const nodeCountLine = nextLine();
  const nodeCount = parseInt(nodeCountLine, 10);
  if (!Number.isFinite(nodeCount) || nodeCount < 0) {
    throw new Error(`Invalid node count: ${nodeCountLine}`);
  }

  // Temporary storage keyed by 1-based ID
  const nodeMap = new Map();
  let maxNodeId = 0;
  for (let i = 0; i < nodeCount; i++) {
    const nline = nextLine();
    if (!nline) throw new Error('Unexpected end in $Nodes');
    const parts = nline.split(/\s+/);
    const id = parseInt(parts[0], 10);
    const x = parseFloat(parts[1]);
    const y = parseFloat(parts[2]);
    const z = parseFloat(parts[3]);
    nodeMap.set(id, [x, y, z]);
    if (id > maxNodeId) maxNodeId = id;
  }
  if (!advanceTo('$EndNodes')) {
    throw new Error('Missing $EndNodes');
  }

  // Build contiguous vertex array with 0-based mapping
  // If IDs are 1..N contiguous, map directly: 0-based index = id - 1
  const vertices = new Float32Array(nodeCount * 3);
  const idToIndex = new Map();
  let idx = 0;
  for (const [id, [x, y, z]] of nodeMap) {
    vertices[idx * 3] = x;
    vertices[idx * 3 + 1] = y;
    vertices[idx * 3 + 2] = z;
    idToIndex.set(id, idx);
    idx++;
  }

  // --- $Elements ---
  if (!advanceTo('$Elements')) {
    throw new Error('Missing $Elements section');
  }
  const elemCountLine = nextLine();
  const elemCount = parseInt(elemCountLine, 10);
  if (!Number.isFinite(elemCount) || elemCount < 0) {
    throw new Error(`Invalid element count: ${elemCountLine}`);
  }

  const triIndices = [];
  const triTags = [];

  for (let i = 0; i < elemCount; i++) {
    const eline = nextLine();
    if (!eline) throw new Error('Unexpected end in $Elements');
    const parts = eline.split(/\s+/).map(Number);
    // parts: [id, type, num-tags, tag1, tag2, ..., n1, n2, n3]
    const elemType = parts[1];
    if (elemType !== 2) continue; // skip non-triangle elements

    const numTags = parts[2];
    const physicalTag = numTags > 0 ? parts[3] : 0;
    const nodeOffset = 3 + numTags;
    const n1 = parts[nodeOffset];
    const n2 = parts[nodeOffset + 1];
    const n3 = parts[nodeOffset + 2];

    triIndices.push(idToIndex.get(n1), idToIndex.get(n2), idToIndex.get(n3));
    triTags.push(physicalTag);
  }

  if (!advanceTo('$EndElements')) {
    throw new Error('Missing $EndElements');
  }

  return {
    vertices,
    indices: new Uint32Array(triIndices),
    physicalNames,
    physicalTags: new Uint32Array(triTags)
  };
}
