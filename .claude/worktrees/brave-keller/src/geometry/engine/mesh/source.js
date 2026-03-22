export function generateThroatSource(vertices, ringCount, fullCircle) {
  if (!Number.isFinite(ringCount) || ringCount < 2) {
    return { center: null, edges: [] };
  }

  let centerX = 0;
  let centerY = 0;
  let centerZ = 0;

  for (let i = 0; i < ringCount; i += 1) {
    centerX += vertices[i * 3];
    centerY += vertices[i * 3 + 1];
    centerZ += vertices[i * 3 + 2];
  }

  centerX /= ringCount;
  centerY /= ringCount;
  centerZ /= ringCount;

  const segmentCount = fullCircle ? ringCount : Math.max(0, ringCount - 1);
  const edges = [];

  for (let i = 0; i < segmentCount; i += 1) {
    const a = i;
    const b = fullCircle ? (i + 1) % ringCount : i + 1;
    edges.push([b, a]);
  }

  return { center: [centerX, centerY, centerZ], edges };
}
