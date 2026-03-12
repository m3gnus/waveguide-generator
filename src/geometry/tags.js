export const SURFACE_TAGS = Object.freeze({
  WALL: 1,
  SOURCE: 2,
  SECONDARY: 3,
  INTERFACE: 4
});

export const FACE_IDENTITY = Object.freeze({
  INNER_WALL: 'inner_wall',
  OUTER_WALL: 'outer_wall',
  MOUTH_RIM: 'mouth_rim',
  THROAT_RETURN: 'throat_return',
  REAR_CAP: 'rear_cap',
  HORN_WALL: 'horn_wall',
  THROAT_DISC: 'throat_disc',
  ENC_FRONT: 'enc_front',
  ENC_SIDE: 'enc_side',
  ENC_REAR: 'enc_rear',
  ENC_EDGE: 'enc_edge'
});

export const MESH_SIZING_CLASS = Object.freeze({
  HORN_INNER_AXIAL: 'horn_inner_axial',
  HORN_REAR_DOMAIN: 'horn_rear_domain',
  THROAT_SOURCE_REGION: 'throat_source_region',
  ENCLOSURE_FRONT: 'enclosure_front',
  ENCLOSURE_REAR: 'enclosure_rear',
  ENCLOSURE_EDGE: 'enclosure_edge'
});

export const SOLVER_BOUNDARY_CLASS = Object.freeze({
  RIGID_WALL: 'RIGID_WALL',
  ACOUSTIC_SOURCE: 'ACOUSTIC_SOURCE',
  IMPEDANCE_APERTURE: 'IMPEDANCE_APERTURE',
  SYMMETRY: 'SYMMETRY'
});

const IDENTITY_TO_SIZING = {
  [FACE_IDENTITY.INNER_WALL]: MESH_SIZING_CLASS.HORN_INNER_AXIAL,
  [FACE_IDENTITY.MOUTH_RIM]: MESH_SIZING_CLASS.HORN_INNER_AXIAL,
  [FACE_IDENTITY.HORN_WALL]: MESH_SIZING_CLASS.HORN_INNER_AXIAL,
  [FACE_IDENTITY.OUTER_WALL]: MESH_SIZING_CLASS.HORN_REAR_DOMAIN,
  [FACE_IDENTITY.THROAT_RETURN]: MESH_SIZING_CLASS.HORN_REAR_DOMAIN,
  [FACE_IDENTITY.REAR_CAP]: MESH_SIZING_CLASS.HORN_REAR_DOMAIN,
  [FACE_IDENTITY.THROAT_DISC]: MESH_SIZING_CLASS.THROAT_SOURCE_REGION,
  [FACE_IDENTITY.ENC_FRONT]: MESH_SIZING_CLASS.ENCLOSURE_FRONT,
  [FACE_IDENTITY.ENC_SIDE]: MESH_SIZING_CLASS.ENCLOSURE_REAR,
  [FACE_IDENTITY.ENC_REAR]: MESH_SIZING_CLASS.ENCLOSURE_REAR,
  [FACE_IDENTITY.ENC_EDGE]: MESH_SIZING_CLASS.ENCLOSURE_EDGE
};

const IDENTITY_TO_BOUNDARY = {
  [FACE_IDENTITY.INNER_WALL]: SOLVER_BOUNDARY_CLASS.RIGID_WALL,
  [FACE_IDENTITY.OUTER_WALL]: SOLVER_BOUNDARY_CLASS.RIGID_WALL,
  [FACE_IDENTITY.MOUTH_RIM]: SOLVER_BOUNDARY_CLASS.RIGID_WALL,
  [FACE_IDENTITY.THROAT_RETURN]: SOLVER_BOUNDARY_CLASS.RIGID_WALL,
  [FACE_IDENTITY.REAR_CAP]: SOLVER_BOUNDARY_CLASS.RIGID_WALL,
  [FACE_IDENTITY.HORN_WALL]: SOLVER_BOUNDARY_CLASS.RIGID_WALL,
  [FACE_IDENTITY.ENC_FRONT]: SOLVER_BOUNDARY_CLASS.RIGID_WALL,
  [FACE_IDENTITY.ENC_SIDE]: SOLVER_BOUNDARY_CLASS.RIGID_WALL,
  [FACE_IDENTITY.ENC_REAR]: SOLVER_BOUNDARY_CLASS.RIGID_WALL,
  [FACE_IDENTITY.ENC_EDGE]: SOLVER_BOUNDARY_CLASS.RIGID_WALL,
  [FACE_IDENTITY.THROAT_DISC]: SOLVER_BOUNDARY_CLASS.ACOUSTIC_SOURCE
};

const BOUNDARY_TO_TAG = {
  [SOLVER_BOUNDARY_CLASS.RIGID_WALL]: SURFACE_TAGS.WALL,
  [SOLVER_BOUNDARY_CLASS.ACOUSTIC_SOURCE]: SURFACE_TAGS.SOURCE,
  [SOLVER_BOUNDARY_CLASS.IMPEDANCE_APERTURE]: SURFACE_TAGS.SECONDARY,
  [SOLVER_BOUNDARY_CLASS.SYMMETRY]: SURFACE_TAGS.INTERFACE
};

export function getFaceIdentityClassifications(identity) {
  return {
    sizingClass: IDENTITY_TO_SIZING[identity],
    boundaryClass: IDENTITY_TO_BOUNDARY[identity],
    surfaceTag: BOUNDARY_TO_TAG[IDENTITY_TO_BOUNDARY[identity]]
  };
}

export function normalizeBuildParams(params, options = {}) {
  return { ...params };
}

export function applyTagRange(tags, range, tagValue) {
  if (!range) return;
  if (Array.isArray(range)) {
    for (const r of range) applyTagRange(tags, r, tagValue);
    return;
  }
  const start = Math.max(0, Number(range.start || 0));
  const end = Math.min(tags.length, Number(range.end || 0));
  if (!(end > start)) return;
  for (let i = start; i < end; i += 1) {
    tags[i] = tagValue;
  }
}

export function buildSurfaceTags(meshData) {
  const triCount = meshData.indices.length / 3;
  const tags = new Array(triCount).fill(SURFACE_TAGS.WALL);

  if (meshData.groups) {
    // Process explicit identities if available
    for (const identity of Object.values(FACE_IDENTITY)) {
      const range = meshData.groups[identity];
      if (range) {
        const tagValue = BOUNDARY_TO_TAG[IDENTITY_TO_BOUNDARY[identity]];
        if (tagValue !== undefined) {
          applyTagRange(tags, range, tagValue);
        }
      }
    }
  }

  // Fallback for legacy "source" group
  if (meshData.groups?.source && !meshData.groups?.[FACE_IDENTITY.THROAT_DISC]) {
    applyTagRange(tags, meshData.groups.source, SURFACE_TAGS.SOURCE);
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
