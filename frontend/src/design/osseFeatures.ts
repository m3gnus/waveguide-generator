import type { DesignFamily } from '../stores/design';

export type OsseFeatures = {
  throat: 'none' | 'authored' | 'controls';
  mouth: 'plain' | 'roundover' | 'arc';
  axial: 'off' | 'on';
};

// The supported constructions are an exhaustive list, not independent flags on
// the wire. In particular SOURCE-DISK is not an OSSE option.
const constructions = [
  ['OSSE', 'none', 'plain', 'off'],
  ['OSSE-ADAPTER', 'authored', 'plain', 'off'],
  ['OSSE-ADAPTER-CONTROLS', 'controls', 'plain', 'off'],
  ['OSSE-ADAPTER-AXIAL', 'authored', 'plain', 'on'],
  ['OSSE-ROUNDOVER', 'none', 'roundover', 'off'],
  ['OSSE-AXIAL', 'none', 'plain', 'on'],
  ['OSSE-ARC', 'none', 'arc', 'off'],
] as const;

export function osseFeatures(formula: string): OsseFeatures | undefined {
  const row = constructions.find(([family]) => family === formula);
  return row && { throat: row[1], mouth: row[2], axial: row[3] };
}

export function osseFormula(features: OsseFeatures): DesignFamily | undefined {
  return constructions.find(([, throat, mouth, axial]) => throat === features.throat
    && mouth === features.mouth && axial === features.axial)?.[0];
}

export const OSSE_FEATURE_CONFLICT = 'only one OSSE feature can be active at a time';
export const OSSE_SWITCH_NOTICE = 'Shared OSSE body dimensions and scale are carried over. Native features use a circular, unterminated body (s = 0), a flat source and full-domain mesh. Other profile, expression, shell and feature settings may be reset. Undo restores the complete previous design.';
