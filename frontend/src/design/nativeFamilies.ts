export const NATIVE_FAMILIES = ['OSSE-ADAPTER', 'OSSE-ADAPTER-CONTROLS', 'OSSE-ADAPTER-AXIAL', 'OSSE-ROUNDOVER', 'OSSE-AXIAL', 'OSSE-ARC', 'OSSE-NATIVE', 'SOURCE-DISK'] as const;
export type NativeFamily = typeof NATIVE_FAMILIES[number];

export function nativeSymmetryLimitation(formula: string, symmetry: string): string | null {
  return (NATIVE_FAMILIES as readonly string[]).includes(formula) && symmetry !== 'auto' && symmetry !== 'full'
    ? `${formula} needs the full model. Set Solve domain to Full.`
    : null;
}

export function nativeCadLimitation(formula: string): string | undefined {
  return (NATIVE_FAMILIES as readonly string[]).includes(formula)
    ? `${formula} cannot be sent to CAD Link. Export STEP or STL for the complete native geometry.`
    : undefined;
}
