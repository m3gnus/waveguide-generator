// Keep aligned with docs/reference/EXPORT-CONTRACTS.md (STL row documents the
// axis remap + winding rule) and docs/reference/RESULT-CONTRACTS.md (phase and
// phasor semantics). FRAME-SPEC.md is the binary wire format, not this contract.
export const ARTIFACT_CONVENTIONS = {
  frame: {
    axes: {
      x: 'horizontal',
      y: 'vertical',
      z: 'axial (throat to mouth)',
    },
    axis_remap_matrix: [
      [1, 0, 0],
      [0, -1, 0],
      [0, 0, 1],
    ],
    winding: 'reversed-on-remap',
  },
  units: {
    solver_length: 'm',
    cad_length: 'mm',
    frequency: 'Hz',
    phase: 'degrees',
  },
  phasor: 'exp(-i omega t)',
} as const;

export const ENGINEERING_NPZ_PHASOR = 'exp(+j omega t)' as const;
