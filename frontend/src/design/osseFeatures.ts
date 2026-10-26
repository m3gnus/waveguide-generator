import type { DesignDocument, DesignFamily } from '../stores/design';

export type OsseFeatures = {
  throat: 'none' | 'authored' | 'controls';
  mouth: 'plain' | 'roundover' | 'arc';
  axial: 'off' | 'on';
};

// Legacy markers remain the canonical spelling wherever one exists.
const constructions = [
  ['OSSE', 'none', 'plain', 'off'],
  ['OSSE-ADAPTER', 'authored', 'plain', 'off'],
  ['OSSE-ADAPTER-CONTROLS', 'controls', 'plain', 'off'],
  ['OSSE-ADAPTER-AXIAL', 'authored', 'plain', 'on'],
  ['OSSE-ROUNDOVER', 'none', 'roundover', 'off'],
  ['OSSE-AXIAL', 'none', 'plain', 'on'],
  ['OSSE-ARC', 'none', 'arc', 'off'],
] as const;

export function osseFeatures(formula: string, design?: DesignDocument): OsseFeatures | undefined {
  if (formula === 'OSSE-NATIVE') {
    if (!design) return { throat: 'none', mouth: 'roundover', axial: 'on' };
    return {
      throat: design.throat_adapter?.mode ?? 'none',
      mouth: design.mesh.mouth_roundover_radius_mm != null ? 'roundover' : design.terminating_arc ? 'arc' : 'plain',
      axial: design.axial_scale === undefined ? 'off' : 'on',
    };
  }
  const row = constructions.find(([family]) => family === formula);
  return row && { throat: row[1], mouth: row[2], axial: row[3] };
}

export function osseFormula(features: OsseFeatures): DesignFamily | undefined {
  const legacy = constructions.find(([, throat, mouth, axial]) => throat === features.throat
    && mouth === features.mouth && axial === features.axial)?.[0];
  if (legacy) return legacy;
  if (features.throat !== 'controls' && features.mouth !== 'plain'
    && (features.throat === 'authored' || features.axial === 'on')) return 'OSSE-NATIVE';
  return undefined;
}

export const OSSE_FEATURE_CONFLICT = 'controls adapters cannot be combined with other features';
export const OSSE_SWITCH_NOTICE = 'Shared OSSE body dimensions and scale are carried over. Native features use a circular, unterminated body (s = 0), a flat source and full-domain mesh. Other profile, shell and feature settings may be reset. Undo restores the complete previous design.';
