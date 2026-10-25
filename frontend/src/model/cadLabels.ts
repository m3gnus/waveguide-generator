import type { CadReturnBundle } from '../api/cadlink';

/** Human-readable labels only; stored filenames and role ids stay unchanged. */
export const SOURCE_ROLE_LABELS: Readonly<Record<string, string>> = {
  HF: 'HF', MF: 'MF', LF: 'LF', PORT_EXIT: 'Port exit', PASSIVE_CARDIOID: 'Passive radiator',
};

export function sourceRoleLabel(role: string): string {
  return SOURCE_ROLE_LABELS[role.toUpperCase()] ?? role;
}

export function cadModelLabel(bundle: Pick<CadReturnBundle, 'documentName' | 'name'>): string {
  return bundle.documentName?.trim() || bundle.name.replace(/\.wgreturn$/i, '') || 'CAD model';
}
