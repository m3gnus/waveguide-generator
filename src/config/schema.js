export const FORMULA_FIELD_ALLOWLIST = Object.freeze({
  'R-OSSE': ['R', 'a', 'a0', 'r0', 'k', 'm', 'b', 'r', 'q', 'tmax'],
  OSSE: ['L', 'a', 'a0', 'r0', 'k', 's', 'n', 'q', 'h'],
  // ICW is axisymmetric-by-construction: the mesher evaluates every ICW
  // param at phi=0, so a per-phi expression would be silently flattened to
  // a constant. Keep the fields plain numbers instead of advertising a
  // formula editor whose input gets dropped.
  ICW: [],
  MORPH: ['morphWidth', 'morphHeight', 'morphCorner', 'morphRate', 'morphFixed'],
  GEOMETRY: [
    'throatExtAngle',
    'throatExtLength',
    'slotLength',
    'rot',
    'gcurveDist',
    'gcurveWidth',
    'gcurveAspectRatio',
    'gcurveSeN',
    'gcurveSf',
    'gcurveSfA',
    'gcurveSfB',
    'gcurveSfM1',
    'gcurveSfM2',
    'gcurveSfN1',
    'gcurveSfN2',
    'gcurveSfN3',
    'gcurveRot',
    'circArcTermAngle',
    'circArcRadius',
  ],
});

export const PARAM_SCHEMA = {
  'R-OSSE': {
    scale: {
      type: 'range',
      label: 'Scale',
      min: 0.1,
      max: 2,
      step: 0.001,
      default: 1.0,
      tooltip:
        'Scaling factor for waveguide geometry only. Values < 1 shrink the waveguide, > 1 enlarge it. Does not affect enclosure dimensions.',
    },
    R: {
      type: 'expression',
      label: 'Mouth Radius (R)',
      unit: 'mm',
      default: 140,
      tooltip: 'Mouth radius as a function of azimuthal angle p. Can be constant or an expression.',
    },
    a: {
      type: 'expression',
      label: 'Mouth Coverage Angle (a)',
      unit: 'deg',
      default: 25,
      tooltip: 'Coverage angle as a function of p. Higher values widen the mouth coverage.',
    },
    a0: {
      type: 'expression',
      label: 'Throat Coverage Angle (a0)',
      unit: 'deg',
      default: 15.5,
      tooltip:
        'Initial throat opening angle in degrees. Can be constant or an expression such as "15 + 2*sin(p)".',
    },
    r0: {
      type: 'expression',
      label: 'Throat Radius (r0)',
      unit: 'mm',
      default: 12.7,
      tooltip: 'Initial throat radius. Can be constant or an expression such as "12.7 + sin(p)".',
    },
    k: {
      type: 'range',
      label: 'Throat Rounding (k)',
      min: 0.1,
      max: 10,
      step: 0.1,
      default: 2.0,
      tooltip: 'Higher values make the throat transition rounder and smoother.',
    },
    m: {
      type: 'range',
      label: 'Apex Shift (m)',
      min: 0,
      max: 1,
      step: 0.01,
      default: 0.85,
      tooltip: 'Moves the virtual apex along the horn axis.',
    },
    b: {
      type: 'expression',
      label: 'Bending (b)',
      default: '0.2',
      tooltip: 'Bends the profile curve between throat and mouth.',
    },
    r: {
      type: 'range',
      label: 'Apex Radius (r)',
      min: 0.01,
      max: 2,
      step: 0.01,
      default: 0.4,
      tooltip: 'Size of the rounded virtual apex region.',
    },
    q: {
      type: 'range',
      label: 'Shape Factor (q)',
      min: 0.5,
      max: 10,
      step: 0.1,
      default: 3.4,
      tooltip: 'Changes how quickly the profile opens from throat to mouth.',
    },
    tmax: {
      type: 'range',
      label: 'Truncation Limit (tmax)',
      min: 0.5,
      max: 1,
      step: 0.01,
      default: 1.0,
      tooltip: 'Cuts the profile at this fraction of its computed length.',
    },
  },
  OSSE: {
    scale: {
      type: 'range',
      label: 'Scale',
      min: 0.1,
      max: 2,
      step: 0.001,
      default: 1.0,
      tooltip:
        'Scaling factor for waveguide geometry only. Values < 1 shrink the waveguide, > 1 enlarge it. Does not affect enclosure dimensions.',
    },
    L: {
      type: 'expression',
      label: 'Horn Length (L)',
      unit: 'mm',
      default: 130,
      tooltip: 'Axial horn length. Can be constant or an expression.',
    },
    a: {
      type: 'expression',
      label: 'Mouth Coverage Angle (a)',
      unit: 'deg',
      default: '45 - 5*cos(2*p)^5 - 2*sin(p)^12',
      tooltip: 'Mouth coverage angle as a function of p.',
    },
    a0: {
      type: 'expression',
      label: 'Throat Coverage Angle (a0)',
      unit: 'deg',
      default: 10,
      tooltip: 'Initial throat coverage angle in degrees. Can be constant or an expression.',
    },
    r0: {
      type: 'expression',
      label: 'Throat Radius (r0)',
      unit: 'mm',
      default: 12.7,
      tooltip: 'Initial throat radius. Can be constant or an expression such as "12.7 + sin(p)".',
    },
    k: {
      type: 'range',
      label: 'Flare Constant (k)',
      min: 0.1,
      max: 15,
      step: 0.1,
      default: 7.0,
      tooltip: 'Higher values make the profile expand faster from the throat.',
    },
    s: {
      type: 'expression',
      label: 'Termination Shape (s)',
      default: '0.85 + 0.3*cos(p)^2',
      tooltip: 'Sets the mouth-end flare shape.',
    },
    n: {
      type: 'range',
      label: 'Termination Curvature (n)',
      min: 1,
      max: 10,
      step: 0.001,
      default: 4,
      tooltip: 'Higher values sharpen the mouth-end curvature.',
    },
    q: {
      type: 'range',
      label: 'Termination Smoothness (q)',
      min: 0.1,
      max: 2,
      step: 0.001,
      default: 0.991,
      tooltip: 'Smooths the transition into the mouth-end flare.',
    },
    h: {
      type: 'range',
      label: 'Shape Factor (h)',
      min: 0,
      max: 10,
      step: 0.1,
      default: 0.0,
      tooltip: 'Adds extra profile shaping when nonzero.',
    },
  },
  ICW: {
    scale: {
      type: 'range',
      label: 'Scale',
      min: 0.1,
      max: 2,
      step: 0.001,
      default: 1.0,
      tooltip:
        'Scaling factor for waveguide geometry only. Values < 1 shrink the waveguide, > 1 enlarge it. Does not affect enclosure dimensions.',
    },
    r0: {
      type: 'expression',
      label: 'Throat Radius (r0)',
      unit: 'mm',
      default: 12.7,
      tooltip: 'Initial throat radius. Can be constant or an expression such as "12.7 + sin(p)".',
    },
    a0: {
      type: 'expression',
      label: 'Throat Half-Angle (a0)',
      unit: 'deg',
      default: 15.0,
      tooltip:
        "Throat half-angle in degrees (the curve's initial wall angle). Can be constant or an expression.",
    },
    L: {
      type: 'expression',
      label: 'Horn Length (L)',
      unit: 'mm',
      default: 120,
      tooltip: 'Target axial horn length. The intrinsic-curvature solver sizes the curve to it.',
    },
    R: {
      type: 'expression',
      label: 'Mouth Radius (R)',
      unit: 'mm',
      default: 150,
      tooltip: 'Target mouth radius. The intrinsic-curvature solver sizes the curve to it.',
    },
    coverage_angle: {
      type: 'number',
      label: 'Coverage Angle',
      unit: 'deg',
      min: 0,
      max: 80,
      step: 1,
      default: 0,
      tooltip:
        '0 = off (pure size targets). >0 holds a constant wall angle (the constant-directivity plateau) between Hold Start and Hold End; Mouth Radius then becomes an emergent output rather than a target. Flat baffle only.',
    },
    hold_start: {
      type: 'number',
      label: 'Coverage Hold Start',
      unit: 'σ',
      min: 0.05,
      max: 0.9,
      step: 0.01,
      default: 0.3,
      tooltip:
        'Normalised arc-length (0..1 throat→mouth) where the coverage plateau begins. Only used when Coverage Angle > 0.',
    },
    hold_end: {
      type: 'number',
      label: 'Coverage Hold End',
      unit: 'σ',
      min: 0.1,
      max: 0.95,
      step: 0.01,
      default: 0.7,
      tooltip:
        'Normalised arc-length where the coverage plateau ends. Must exceed Hold Start. Only used when Coverage Angle > 0.',
    },
    n_coeff: {
      type: 'number',
      label: 'Curvature Coefficients (n_coeff)',
      // The cubic curvature B-spline needs >= degree+1 = 4 coefficients (fewer hard-errors in
      // the solver), and the default flat-baffle target is only feasible from 5 up, so 4 always
      // fails. Floor at 5 so no selectable value 422s on the shipped defaults.
      min: 5,
      max: 24,
      step: 1,
      default: 6,
      tooltip:
        'Number of curvature-spline control coefficients the solver uses. More coefficients allow finer wall shaping.',
    },
    termination: {
      type: 'select',
      label: 'Termination',
      options: [
        { value: 'flat_baffle', label: 'Flat Baffle' },
        { value: 'rollback', label: 'Rollback' },
      ],
      default: 'flat_baffle',
      tooltip:
        'Mouth termination. Flat baffle ends tangent to the baffle (theta1 = 90 deg, curvature continuous); rollback curls the lip past 90 deg.',
    },
    theta1_deg: {
      type: 'number',
      label: 'Rollback Curl Angle',
      unit: 'deg',
      min: 91,
      max: 179,
      step: 1,
      default: 160,
      tooltip:
        'Rollback only: terminal wall angle past 90 deg — how far the mouth lip curls back. Must exceed 90. Ignored for Flat Baffle.',
    },
    depth: {
      type: 'number',
      label: 'Rollback Depth',
      unit: 'mm',
      min: 1,
      step: 1,
      default: 100,
      tooltip:
        'Rollback only: target total axial depth of the curled mouth. A rollback solve REQUIRES this (Mouth Radius then sets the aperture radius). Ignored for Flat Baffle.',
    },
  },
  GEOMETRY: {
    throatProfile: {
      type: 'select',
      label: 'Throat Profile',
      options: [
        { value: 1, label: 'OS-SE (Profile 1)' },
        { value: 3, label: 'Circular Arc (Profile 3)' },
      ],
      default: 1,
      tooltip: 'Chooses the throat transition profile: OS-SE or circular arc.',
    },
    throatExtAngle: {
      type: 'expression',
      label: 'Throat Extension Angle',
      unit: 'deg',
      default: '0',
      tooltip: 'Half-angle of the optional conical throat extension.',
    },
    throatExtLength: {
      type: 'expression',
      label: 'Throat Extension Length',
      unit: 'mm',
      default: '0',
      tooltip: 'Axial length of the optional conical throat extension.',
    },
    slotLength: {
      type: 'expression',
      label: 'Straight Slot Length',
      unit: 'mm',
      default: '0',
      tooltip: 'Axial length of an initial straight waveguide segment.',
    },
    rot: {
      type: 'expression',
      label: 'Profile Rotation',
      unit: 'deg',
      default: '0',
      tooltip: 'Rotate the computed profile around point [0, r0].',
    },
    gcurveType: {
      type: 'select',
      label: 'Guiding Curve Mode',
      options: [
        { value: 0, label: 'Explicit Coverage' },
        { value: 1, label: 'Superellipse' },
        { value: 2, label: 'Superformula' },
      ],
      default: 0,
      tooltip: 'Chooses whether coverage is entered directly or derived from a guide shape.',
    },
    gcurveDist: {
      type: 'expression',
      label: 'Guiding Curve Distance',
      default: '0.5',
      tooltip: 'Distance from throat to guide shape, as a fraction or millimetres.',
    },
    gcurveWidth: {
      type: 'expression',
      label: 'Guiding Curve Width',
      unit: 'mm',
      default: '0',
      tooltip: 'Width of the guide shape along the X axis.',
    },
    gcurveAspectRatio: {
      type: 'expression',
      label: 'Guiding Curve Aspect Ratio',
      default: '1',
      tooltip: 'Height divided by width for the guide shape.',
    },
    gcurveSeN: {
      type: 'expression',
      label: 'Guiding Superellipse Exponent',
      default: '3',
      tooltip: 'Exponent used when the guiding curve runs in superellipse mode.',
    },
    gcurveSf: {
      type: 'expression',
      label: 'Superformula Tuple',
      default: '',
      tooltip: 'Comma-separated superformula parameters in the order a,b,m,n1,n2,n3.',
    },
    gcurveSfA: {
      type: 'expression',
      label: 'Superformula a',
      default: '',
      tooltip: 'Superformula a parameter.',
    },
    gcurveSfB: {
      type: 'expression',
      label: 'Superformula b',
      default: '',
      tooltip: 'Superformula b parameter.',
    },
    gcurveSfM1: {
      type: 'expression',
      label: 'Superformula m1',
      default: '',
      tooltip: 'Superformula m1 parameter.',
    },
    gcurveSfM2: {
      type: 'expression',
      label: 'Superformula m2',
      default: '',
      tooltip: 'Superformula m2 parameter.',
    },
    gcurveSfN1: {
      type: 'expression',
      label: 'Superformula n1',
      default: '',
      tooltip: 'Superformula n1 parameter.',
    },
    gcurveSfN2: {
      type: 'expression',
      label: 'Superformula n2',
      default: '',
      tooltip: 'Superformula n2 parameter.',
    },
    gcurveSfN3: {
      type: 'expression',
      label: 'Superformula n3',
      default: '',
      tooltip: 'Superformula n3 parameter.',
    },
    gcurveRot: {
      type: 'expression',
      label: 'Guiding Curve Rotation',
      unit: 'deg',
      default: '0',
      tooltip: 'Rotates the guide shape counterclockwise.',
    },
    circArcTermAngle: {
      type: 'expression',
      label: 'Circular Arc Terminal Angle',
      unit: 'deg',
      default: '1',
      tooltip: 'Mouth terminal angle for the circular-arc throat profile.',
    },
    circArcRadius: {
      type: 'expression',
      label: 'Circular Arc Radius Override',
      unit: 'mm',
      default: '0',
      tooltip: 'Explicit radius override for the circular-arc throat profile.',
    },
  },
  MORPH: {
    morphTarget: {
      type: 'select',
      label: 'Target Shape',
      options: [
        { value: 0, label: 'None' },
        { value: 1, label: 'Rectangle' },
        { value: 2, label: 'Circle' },
      ],
      default: 1,
      tooltip: 'Shape the mouth morphs toward. None leaves the generated outline unchanged.',
    },
    morphWidth: {
      type: 'number',
      label: 'Target Width',
      unit: 'mm',
      default: 0,
      tooltip: 'Final target width for rectangle or circle morphing. Use 0 for auto.',
    },
    morphHeight: {
      type: 'number',
      label: 'Target Height',
      unit: 'mm',
      default: 0,
      tooltip: 'Final target height for rectangle morphing. Use 0 for auto.',
    },
    morphCorner: {
      type: 'range',
      label: 'Corner Radius',
      unit: 'mm',
      min: 0,
      max: 100,
      step: 1,
      default: 0,
      tooltip: 'Corner radius for rectangular target shapes.',
    },
    morphRate: {
      type: 'number',
      label: 'Morph Rate',
      step: 0.1,
      default: 3.0,
      tooltip: 'Higher values make the profile reach the target shape sooner.',
    },
    morphFixed: {
      type: 'range',
      label: 'Fixed Part',
      min: 0,
      max: 1,
      step: 0.01,
      default: 0.0,
      tooltip: 'Fraction of the throat-end profile kept unchanged before morphing starts.',
    },
    morphAllowShrinkage: {
      type: 'select',
      label: 'Allow Shrinkage',
      options: [
        { value: 0, label: 'No' },
        { value: 1, label: 'Yes' },
      ],
      default: 0,
      tooltip: 'Allows morphing to make sections smaller if needed to reach the target shape.',
    },
  },
  MESH: {
    angularSegments: {
      type: 'number',
      label: 'Surface Angular Samples',
      default: 40,
      tooltip:
        'Surface sampling around the horn circumference for mesher geometry input. The live viewport uses lightweight HornLab mesher tessellation.',
    },
    lengthSegments: {
      type: 'number',
      label: 'Surface Length Samples',
      default: 20,
      tooltip:
        'Surface sampling along the horn length for mesher geometry input. The live viewport uses lightweight HornLab mesher tessellation.',
    },
    cornerSegments: {
      type: 'number',
      label: 'Surface Corner Samples',
      default: 4,
      tooltip: 'Surface sampling for rounded corners and morph edges.',
    },
    throatSegments: {
      type: 'number',
      label: 'Throat Slice Samples',
      default: 0,
      tooltip:
        'Extra surface samples near the throat. The live viewport mesh is still generated by HornLab mesher viewport tessellation.',
    },
    throatResolution: {
      type: 'number',
      label: 'Throat Mesh Resolution',
      unit: 'mm',
      default: 6.0,
      tooltip:
        'HornLab mesher solve/export element size near the throat. Also influences viewport slice spacing unless Throat Slice Density overrides it.',
    },
    mouthResolution: {
      type: 'number',
      label: 'Mouth Mesh Resolution',
      unit: 'mm',
      default: 20.0,
      tooltip:
        'HornLab mesher solve/export element size near the mouth. Also influences viewport slice spacing unless Throat Slice Density overrides it.',
    },
    throatSliceDensity: {
      type: 'number',
      label: 'Preview Slice Bias',
      default: null,
      tooltip:
        'Viewport slice clustering (0.5 = uniform, lower = tighter near the throat). When set, it overrides the throat-to-mouth resolution ratio for viewport slice distribution only.',
    },
    verticalOffset: {
      type: 'number',
      label: 'Export Vertical Offset',
      unit: 'mm',
      default: 0.0,
      tooltip:
        'Vertical offset for the simulation and export coordinate system. Does not affect the 3D viewer.',
    },
    wallThickness: {
      type: 'number',
      label: 'Wall Thickness',
      unit: 'mm',
      default: 0,
      tooltip:
        'Applies only to freestanding horns (Enclosure Depth = 0). Builds a normal-offset wall shell one wall-thickness from the horn surface and a rear disc behind the throat.',
    },
    rearResolution: {
      type: 'number',
      label: 'Rear Mesh Resolution',
      unit: 'mm',
      default: 40.0,
      tooltip:
        'HornLab mesher solve/export element size for the rear wall on freestanding thickened horns.',
    },
    quadrants: {
      type: 'select',
      label: 'Quadrants',
      default: '1234',
      tooltip:
        'Symmetry-reduced solve/export mesh domain. Use Auto to choose the smallest domain that keeps detected geometry symmetry.',
      autoAction: 'quadrants',
      options: [
        { value: '1234', label: 'Full (1234)' },
        { value: '12', label: 'Half Y≥0 (12)' },
        { value: '14', label: 'Half X≥0 (14)' },
        { value: '1', label: 'Quarter Q1 (1)' },
      ],
    },
  },
  ENCLOSURE: {
    encDepth: {
      type: 'number',
      label: 'Enclosure Depth',
      unit: 'mm',
      default: 0,
      tooltip: 'Depth of the rear enclosure behind the front baffle. Use 0 for no enclosure.',
    },
    encEdge: {
      type: 'number',
      label: 'Edge Radius',
      unit: 'mm',
      default: 18,
      tooltip: 'Radius or chamfer size applied to the enclosure front edges.',
    },
    encEdgeType: {
      type: 'select',
      label: 'Edge Finish',
      options: [
        { value: 1, label: 'Rounded' },
        { value: 2, label: 'Chamfered' },
      ],
      default: 1,
      tooltip: 'Chooses rounded or chamfered treatment for enclosure front edges.',
    },
    encSpaceL: {
      type: 'number',
      label: 'Left Margin',
      default: 25,
      tooltip: 'Baffle margin from the horn mouth to the left enclosure edge.',
    },
    encSpaceT: {
      type: 'number',
      label: 'Top Margin',
      default: 25,
      tooltip: 'Baffle margin from the horn mouth to the top enclosure edge.',
    },
    encSpaceR: {
      type: 'number',
      label: 'Right Margin',
      default: 25,
      tooltip: 'Baffle margin from the horn mouth to the right enclosure edge.',
    },
    encSpaceB: {
      type: 'number',
      label: 'Bottom Margin',
      default: 25,
      tooltip: 'Baffle margin from the horn mouth to the bottom enclosure edge.',
    },
    encFrontResolution: {
      type: 'expression',
      label: 'Front Baffle Mesh Resolution',
      unit: 'mm',
      default: '25,25,25,25',
      tooltip:
        'HornLab mesher solve/export element sizes for enclosure front-baffle quadrants (q1..q4).',
    },
    encBackResolution: {
      type: 'expression',
      label: 'Rear Baffle Mesh Resolution',
      unit: 'mm',
      default: '40,40,40,40',
      tooltip:
        'HornLab mesher solve/export element sizes for enclosure back-baffle quadrants (q1..q4).',
    },
  },
  SOURCE: {
    sourceShape: {
      type: 'select',
      label: 'Source Surface',
      options: [
        { value: 1, label: 'Spherical Cap' },
        { value: 2, label: 'Flat Disc' },
      ],
      default: 1,
      tooltip: 'Chooses the source surface shape placed at the throat.',
    },
    sourceRadius: {
      type: 'number',
      label: 'Source Radius',
      default: -1,
      tooltip: 'Radius of the source surface. Use -1 to match the throat automatically.',
    },
    sourceCurv: {
      type: 'select',
      label: 'Source Curvature',
      options: [
        { value: 0, label: 'Auto' },
        { value: 1, label: 'Convex' },
        { value: -1, label: 'Concave' },
      ],
      default: 0,
      tooltip: 'Chooses source curvature direction. Auto picks the default for the shape.',
    },
    sourceVelocity: {
      type: 'select',
      label: 'Source Velocity',
      options: [
        { value: 1, label: 'Normal' },
        { value: 2, label: 'Axial' },
      ],
      default: 1,
      tooltip: 'Chooses whether source velocity follows the surface normal or horn axis.',
    },
    sourceContours: {
      type: 'expression',
      label: 'Source Contours',
      default: '',
      tooltip: 'File path or inline script defining custom source contours.',
    },
  },
  SIMULATION: {
    freqStart: {
      type: 'number',
      label: 'Sweep Start',
      unit: 'Hz',
      default: 400,
      min: 20,
      max: 20000,
      step: 10,
      controlId: 'freq-start',
      tooltip: 'Lowest frequency in the backend BEM sweep.',
    },
    freqEnd: {
      type: 'number',
      label: 'Sweep End',
      unit: 'Hz',
      default: 16000,
      min: 20,
      max: 20000,
      step: 10,
      controlId: 'freq-end',
      tooltip: 'Highest frequency in the backend BEM sweep.',
    },
    numFreqs: {
      type: 'number',
      label: 'Frequency Samples',
      default: 20,
      min: 10,
      max: 200,
      step: 1,
      controlId: 'freq-steps',
      tooltip: 'Number of solved frequencies between the start and end values.',
    },
    simType: {
      type: 'select',
      label: 'Simulation Type',
      default: '2',
      controlId: 'sim-type',
      tooltip:
        'BEM boundary condition. Free-standing radiates into full space. Infinite baffle ' +
        'uses the Metal native xy image-plane solve and currently requires full azimuth.',
      options: [
        { value: '2', label: 'Free-standing' },
        { value: '1', label: 'Infinite baffle' },
      ],
    },
  },
  // Output actions are handled via export buttons in the UI.
};

for (const [group, keys] of Object.entries(FORMULA_FIELD_ALLOWLIST)) {
  const schemaGroup = PARAM_SCHEMA[group];
  if (!schemaGroup) continue;
  for (const key of keys) {
    if (schemaGroup[key]) {
      schemaGroup[key].supportsFormula = true;
    }
  }
}
