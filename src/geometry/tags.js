export const SURFACE_TAGS = Object.freeze({
  WALL: 1,
  SOURCE: 2,
  SECONDARY: 3,
  INTERFACE: 4
});

export function parseInterfaceOffset(value) {
  if (value === undefined || value === null) return 0;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  const text = String(value).trim();
  if (!text) return 0;
  const first = text.split(',')[0].trim();
  const numeric = Number(first);
  return Number.isFinite(numeric) ? numeric : 0;
}

export function normalizeBuildParams(params, options = {}) {
  return { ...params };
}

export function applyTagRange(tags, range, tagValue) {
  if (!range) return;
  const start = Math.max(0, Number(range.start || 0));
  const end = Math.min(tags.length, Number(range.end || 0));
  if (!(end > start)) return;
  for (let i = start; i < end; i += 1) {
    tags[i] = tagValue;
  }
}

export function buildSurfaceTags(meshData, { interfaceEnabled = false } = {}) {
  const triCount = meshData.indices.length / 3;
  const tags = new Array(triCount).fill(SURFACE_TAGS.WALL);

  if (meshData.groups?.source) {
    applyTagRange(tags, meshData.groups.source, SURFACE_TAGS.SOURCE);
  }

  if (interfaceEnabled) {
    applyTagRange(tags, meshData.groups?.enclosure, SURFACE_TAGS.SECONDARY);
    applyTagRange(tags, meshData.groups?.interface, SURFACE_TAGS.INTERFACE);
  }

  return tags;
}

export function countTags(surfaceTags) {
  const counts = {
    [SURFACE_TAGS.WALL]: 0,
    [SURFACE_TAGS.SOURCE]: 0,
    [SURFACE_TAGS.SECONDARY]: 0,
    [SURFACE_TAGS.INTERFACE]: 0
  };
  for (let i = 0; i < surfaceTags.length; i += 1) {
    const tag = Number(surfaceTags[i]);
    if (counts[tag] !== undefined) {
      counts[tag] += 1;
    }
  }
  return counts;
}

export function buildBoundaryConditions() {
  return {
    throat: { type: 'velocity', surfaceTag: SURFACE_TAGS.SOURCE, value: 1.0 },
    wall: { type: 'neumann', surfaceTag: SURFACE_TAGS.WALL, value: 0.0 },
    mouth: { type: 'robin', surfaceTag: SURFACE_TAGS.WALL, impedance: 'spherical' }
  };
}
