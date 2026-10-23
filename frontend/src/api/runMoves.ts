import type { components } from './generated/openapi';
import { httpErrorMessage } from './httpError';

export type MoveTarget = Pick<components['schemas']['PreviewRunMove'], 'key' | 'name'>;
export interface MoveCandidate {
  job_id: string; source: string; destination: string; preselected: boolean;
  reason: string; files: number; bytes: number; method: string;
}
export interface RunMove {
  id: string; key: string; name: string; phase: string; error: string | null; folder: string; target: string;
  selected: string[]; candidates: MoveCandidate[]; gather: boolean; rename: boolean;
  summary: { runs: number; files: number; bytes: number; fusion_files: number; duplicates: number; saved_bytes: number; rename_runs: number; rename_files: number; rename_bytes: number };
  operations: { src: string; dst: string; kind: string; state: string; method: string }[];
  old_folders: string[];
}
async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/workspace/run-moves${path}`, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await httpErrorMessage(response, 'Could not move earlier runs'));
  return response.json() as Promise<T>;
}
export function previewRunMove(target: MoveTarget, options: { selected?: string[]; gather: boolean; rename: boolean }) {
  return request<RunMove>('/preview', { ...target, ...options });
}
export function executeRunMove(id: string) { return request<RunMove>(`/${id}/execute`, { confirmed: true }); }
export function undoRunMove(id: string) { return request<RunMove>(`/${id}/undo`, {}); }
export function runMoveStatus() { return request<{ reports: RunMove[]; recovered?: RunMove[] }>('/status'); }
export function recoverRunMoves() { return request<{ reports: RunMove[] }>('/recover', {}); }
