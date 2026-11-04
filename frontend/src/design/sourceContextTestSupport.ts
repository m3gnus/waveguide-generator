import type { SourceAssemblyDocument, SourceDocument } from '../api/sourceEditor';
import { INITIAL_SOURCE } from './sourceContourEditing';

// Context geometry for the retained diaphragm sketch adapter tests.
function flatSource(id: string, radius: number): SourceDocument {
  const document = structuredClone(INITIAL_SOURCE);
  document.contour.physical_source_id = id; document.contour.rim_id = `${id}.rim`;
  document.contour.points[1].r_mm = radius; document.drive.channel_id = id === 'horn' ? 'hf' : 'lf';
  return document;
}
export const INITIAL_ASSEMBLY: SourceAssemblyDocument = {
  horn: flatSource('horn', 4), woofer: flatSource('woofer', 8),
  dimensions: { width_mm: 70, height_mm: 90, depth_mm: 40, front_z_mm: 7,
    horn_xy_mm: [-13, 17], horn_length_mm: 24, mouth_radius_mm: 10,
    woofer_xy_mm: [9, -15], aperture_radius_mm: 8 },
  phase_plugs: [], mesh_size_mm: 4, passage_refinement: 1,
};
