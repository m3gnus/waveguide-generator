/**
 * The design assembly endpoints (`server/jobs/models_api.py`): the front
 * view's solver-free context, and compiling one captured design revision into
 * its design model's immutable prepared mesh.
 */
import { requestImportedModel, type ImportedModelSummary, type PreparedSummary } from './importedModels';

/** Live outlines and the design mesher's own woofer placement, in horn-throat mm. */
export interface AssemblyFrontContext {
  refusal?: string;
  frame?: 'horn-throat-pre-placement-mm';
  mouth_bottom_mm?: number;
  mouth_top_mm?: number;
  woofers?: Array<{ radiator_id: string; x_mm: number; y_mm: number; radius_mm: number }>;
  mouth_outline_mm?: Array<[number, number]>;
  baffle?: { x0_mm: number; x1_mm: number; y0_mm: number; y1_mm: number; edge_mm: number } | null;
  /** Reference distances; the build certifies the 0.1 mm minimum exactly. */
  clearances?: Record<string, { mouth_mm: number; baffle_edge_mm?: number; woofers: Record<string, number> }>;
  minimum_clearance_mm?: number;
}

export interface AssemblyRadiator {
  radiator_id: string;
  kind: 'classic' | 'piston' | 'ath_cone';
  label: string;
  state: 'compiled' | 'disabled' | 'rigid';
  role: 'HF' | 'LF';
  motion: 'normal' | 'axial';
  motion_axis?: [number, number, number];
  source_ids: string[];
  center_mm?: [number, number];
  radius_mm?: number;
  projected_area_m2?: number;
}

/** A compiled assembly record as a model read returns it, with its solver mesh. */
export interface CompiledAssembly extends PreparedSummary {
  producer: 'design-assembly';
  design: { design_uid: string; name: string; revision: number; content_sha256: string;
    assembly_sha256: string; geometry_sha256: string };
  radiators: AssemblyRadiator[];
  excitations: Array<{ id: string; source_ids: string[]; motion: 'normal' | 'axial';
    motion_axis?: [number, number, number]; patch_weights: Record<string, number> }>;
  signal_groups: Array<{ id: string; label: string; members: string[] }>;
  sources: Array<{ id: string; role: string; radiator_id: string; segment_id: string | null }>;
  source_tags: Record<string, number>;
  msh_text: string;
}

export type AssemblyCompileResult =
  | { status: 'published'; reused: boolean; model: ImportedModelSummary; prepared: CompiledAssembly }
  | { status: 'superseded' };

const post = <T>(path: string, body: unknown, fetcher: typeof fetch, what: string) =>
  requestImportedModel<T>(path, fetcher, what, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });

export const fetchAssemblyContext = (design: Record<string, unknown>, fetcher: typeof fetch = fetch) =>
  post<AssemblyFrontContext>('/api/models/assembly/context', { design }, fetcher, 'The front view');

export const compileAssembly = (body: { design: Record<string, unknown>; design_uid: string; design_name: string; design_revision: number },
  fetcher: typeof fetch = fetch) => post<AssemblyCompileResult>('/api/models/assembly/compile', body, fetcher, 'The assembly');
