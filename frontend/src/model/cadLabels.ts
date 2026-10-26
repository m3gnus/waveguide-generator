import type { CadReturnBundle } from '../api/cadlink';

/** Human-readable labels only; stored filenames and role ids stay unchanged. */
export const SOURCE_ROLE_LABELS: Readonly<Record<string, string>> = {
  HF: 'HF', MF: 'MF', LF: 'LF', PORT_EXIT: 'Port exit', PORT_EXIT_L: 'Left port exit', PORT_EXIT_R: 'Right port exit',
  PASSIVE_CARDIOID: 'Port or passive opening', RIGID: 'Rigid wall',
};

export const FILE_SURFACE_ROLES = ['HF', 'MF', 'LF', 'PASSIVE_CARDIOID', 'RIGID'] as const;

export function sourceRoleLabel(role: string, { driver = false }: { driver?: boolean } = {}): string {
  const label = SOURCE_ROLE_LABELS[role.toUpperCase()] ?? role;
  return driver && ['HF', 'MF', 'LF'].includes(role.toUpperCase()) ? `${label} driver` : label;
}

export function cadModelLabel(bundle: Pick<CadReturnBundle, 'documentName' | 'name'>): string {
  return bundle.documentName?.trim() || bundle.name.replace(/\.wgreturn$/i, '') || 'CAD model';
}

/** File meshes only declare the solver's two supported open symmetry planes. */
export function fileSymmetryLabel(planes: readonly string[], open = true): string {
  if (planes.includes('x0') && planes.includes('y0')) return `Quarter model${open ? ' · open at x = 0 and y = 0' : ''}`;
  if (planes.includes('x0')) return `Half model${open ? ' · open at x = 0' : ''}`;
  if (planes.includes('y0')) return `Half model${open ? ' · open at y = 0' : ''}`;
  return 'Full model';
}
