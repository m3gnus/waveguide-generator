export interface SourcePoint { id: string; r_mm: number; z_mm: number }
export interface SourcePatch {
  id: string; start: string; end: string; role: 'moving' | 'rigid'; kind: 'line' | 'arc';
  center_mm: [number, number] | null; direction: 'cw' | 'ccw';
}
export interface SourceDocument {
  contour: { version: 1; physical_source_id: string; rim_id: string; points: SourcePoint[]; segments: SourcePatch[] };
  drive: { channel_id: string; motion: 'normal' | 'axial'; weights: Record<string, number> };
}
export interface SourceValidation {
  document: SourceDocument; geometry_sha256: string; excitation_sha256: string;
  meridian: Record<string, [number, number][]>;
}
export interface SourcePreset { id: string; revision: string; name: string; document: SourceDocument }
export interface SourceAttachment { kind: 'baffle' | 'horn'; dimensions: Record<string, number | [number, number, number]> }

async function response(path: string, method = 'GET', body?: unknown): Promise<Response> {
  const result = await fetch(`/api/source-editor${path}`, {
    method, headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!result.ok) {
    const error = await result.json().catch(() => null) as { detail?: unknown } | null;
    throw new Error(typeof error?.detail === 'string' ? error.detail : `Source editor request failed (${result.status}). Check all required fields.`);
  }
  return result;
}
export const sourceEditorApi = {
  validate: async (document: SourceDocument): Promise<SourceValidation> => (await response('/validate', 'POST', document)).json(),
  expand: async (kind: 'flat' | 'dome' | 'cone', dimensions: Record<string, number>, document: SourceDocument): Promise<SourceValidation> => (await response('/expand', 'POST', {
    kind, dimensions, physical_source_id: document.contour.physical_source_id, rim_id: document.contour.rim_id, channel_id: document.drive.channel_id, motion: document.drive.motion,
  })).json(),
  split: async (document: SourceDocument, segment_index: number): Promise<SourceValidation> => (await response('/split', 'POST', { document, segment_index })).json(),
  presets: async (): Promise<SourcePreset[]> => (await response('/presets')).json(),
  save: async (name: string, document: SourceDocument, previous?: SourcePreset): Promise<SourcePreset> => (await response(previous ? `/presets/${encodeURIComponent(previous.id)}` : '/presets', previous ? 'PUT' : 'POST', {
    name, document, expected_revision: previous?.revision ?? null,
  })).json(),
  delete: async (preset: SourcePreset): Promise<void> => { await response(`/presets/${encodeURIComponent(preset.id)}?revision=${encodeURIComponent(preset.revision)}`, 'DELETE'); },
  export: async (document: SourceDocument, attachment: SourceAttachment, mesh_size_mm: number): Promise<Blob> => (await response('/export', 'POST', { document, attachment, mesh_size_mm })).blob(),
};
