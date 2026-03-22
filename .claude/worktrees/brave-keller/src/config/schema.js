export const FORMULA_FIELD_ALLOWLIST = Object.freeze({
  "R-OSSE": ["R", "a", "a0", "r0", "k", "m", "b", "r", "q", "tmax"],
  OSSE: ["L", "a", "a0", "r0", "k", "s", "n", "q", "h"],
  MORPH: [
    "morphWidth",
    "morphHeight",
    "morphCorner",
    "morphRate",
    "morphFixed",
  ],
  GEOMETRY: [
    "throatExtAngle",
    "throatExtLength",
    "slotLength",
    "rot",
    "gcurveDist",
    "gcurveWidth",
    "gcurveAspectRatio",
    "gcurveSeN",
    "gcurveSf",
    "gcurveSfA",
    "gcurveSfB",
    "gcurveSfM1",
    "gcurveSfM2",
    "gcurveSfN1",
    "gcurveSfN2",
    "gcurveSfN3",
    "gcurveRot",
    "circArcTermAngle",
    "circArcRadius",
  ],
});

export const PARAM_SCHEMA = {
  "R-OSSE": {
    scale: {
      type: "range",
      label: "Scale",
      min: 0.1,
      max: 2,
      step: 0.001,
      default: 1.0,
      tooltip:
        "Scaling factor for waveguide geometry only. Values < 1 shrink the waveguide, > 1 enlarge it. Does not affect enclosure dimensions.",
    },
    R: {
      type: "expression",
      label: "Mouth Radius (R)",
      unit: "mm",
      default: 140,
      tooltip:
        "Mouth radius as a function of azimuthal angle p. Can be constant or an expression.",
    },
    a: {
      type: "expression",
      label: "Mouth Coverage Angle (a)",
      unit: "deg",
      default: 25,
      tooltip:
        "Coverage angle as a function of p. Higher values widen the mouth coverage.",
    },
    a0: {
      type: "expression",
      label: "Throat Coverage Angle (a0)",
      unit: "deg",
      default: 15.5,
      tooltip:
        'Initial throat opening angle in degrees. Can be constant or an expression such as "15 + 2*sin(p)".',
    },
    r0: {
      type: "expression",
      label: "Throat Radius (r0)",
      unit: "mm",
      default: 12.7,
      tooltip:
        'Initial throat radius. Can be constant or an expression such as "12.7 + sin(p)".',
    },
    k: {
      type: "range",
      label: "Throat Rounding (k)",
      min: 0.1,
      max: 10,
      step: 0.1,
      default: 2.0,
      tooltip: "Controls the throat rounding and smoothness.",
    },
    m: {
      type: "range",
      label: "Apex Shift (m)",
      min: 0,
      max: 1,
      step: 0.01,
      default: 0.85,
      tooltip: "Shifts the apex position along the horn axis.",
    },
    b: {
      type: "expression",
      label: "Bending (b)",
      default: "0.2",
      tooltip: "Controls profile curvature.",
    },
    r: {
      type: "range",
      label: "Apex Radius (r)",
      min: 0.01,
      max: 2,
      step: 0.01,
      default: 0.4,
      tooltip: "Radius of the apex region.",
    },
    q: {
      type: "range",
      label: "Shape Factor (q)",
      min: 0.5,
      max: 10,
      step: 0.1,
      default: 3.4,
      tooltip: "Controls the overall horn shape profile.",
    },
    tmax: {
      type: "range",
      label: "Truncation Limit (tmax)",
      min: 0.5,
      max: 1.5,
      step: 0.01,
      default: 1.0,
      tooltip: "Truncates the horn at a fraction of the computed length.",
    },
  },
  OSSE: {
    scale: {
      type: "range",
      label: "Scale",
      min: 0.1,
      max: 2,
      step: 0.001,
      default: 1.0,
      tooltip:
        "Scaling factor for waveguide geometry only. Values < 1 shrink the waveguide, > 1 enlarge it. Does not affect enclosure dimensions.",
    },
    L: {
      type: "expression",
      label: "Horn Length (L)",
      unit: "mm",
      default: 130,
      tooltip: "Axial horn length. Can be constant or an expression.",
    },
    a: {
      type: "expression",
      label: "Mouth Coverage Angle (a)",
      unit: "deg",
      default: "45 - 5*cos(2*p)^5 - 2*sin(p)^12",
      tooltip: "Mouth coverage angle as a function of p.",
    },
    a0: {
      type: "expression",
      label: "Throat Coverage Angle (a0)",
      unit: "deg",
      default: 10,
      tooltip:
        "Initial throat coverage angle in degrees. Can be constant or an expression.",
    },
    r0: {
      type: "expression",
      label: "Throat Radius (r0)",
      unit: "mm",
      default: 12.7,
      tooltip:
        'Initial throat radius. Can be constant or an expression such as "12.7 + sin(p)".',
    },
    k: {
      type: "range",
      label: "Flare Constant (k)",
      min: 0.1,
      max: 15,
      step: 0.1,
      default: 7.0,
      tooltip: "Expansion rate of the horn profile.",
    },
    s: {
      type: "expression",
      label: "Termination Shape (s)",
      default: "0.85 + 0.3*cos(p)^2",
      tooltip: "Shape factor for the termination flare.",
    },
    n: {
      type: "range",
      label: "Termination Curvature (n)",
      min: 1,
      max: 10,
      step: 0.001,
      default: 4,
      tooltip: "Curvature control exponent for the termination.",
    },
    q: {
      type: "range",
      label: "Termination Smoothness (q)",
      min: 0.1,
      max: 2,
      step: 0.001,
      default: 0.991,
      tooltip: "Transition smoothness at the termination.",
    },
    h: {
      type: "range",
      label: "Shape Factor (h)",
      min: 0,
      max: 10,
      step: 0.1,
      default: 0.0,
      tooltip: "Additional shape control parameter.",
    },
  },
  GEOMETRY: {
    throatProfile: {
      type: "select",
      label: "Throat Profile",
      options: [
        { value: 1, label: "OS-SE (Profile 1)" },
        { value: 3, label: "Circular Arc (Profile 3)" },
      ],
      default: 1,
      tooltip: "Profile type: OS-SE or circular arc.",
    },
    throatExtAngle: {
      type: "expression",
      label: "Throat Extension Angle",
      unit: "deg",
      default: "0",
      tooltip: "Half-angle of the optional conical throat extension.",
    },
    throatExtLength: {
      type: "expression",
      label: "Throat Extension Length",
      unit: "mm",
      default: "0",
      tooltip: "Axial length of the optional conical throat extension.",
    },
    slotLength: {
      type: "expression",
      label: "Straight Slot Length",
      unit: "mm",
      default: "0",
      tooltip: "Axial length of an initial straight waveguide segment.",
    },
    rot: {
      type: "expression",
      label: "Profile Rotation",
      unit: "deg",
      default: "0",
      tooltip: "Rotate the computed profile around point [0, r0].",
    },
    gcurveType: {
      type: "select",
      label: "Guiding Curve Mode",
      options: [
        { value: 0, label: "Explicit Coverage" },
        { value: 1, label: "Superellipse" },
        { value: 2, label: "Superformula" },
      ],
      default: 0,
      tooltip: "Use guiding curve to infer coverage angle.",
    },
    gcurveDist: {
      type: "expression",
      label: "Guiding Curve Distance",
      default: "0.5",
      tooltip:
        "Guiding-curve distance from the throat, expressed as a fraction or in millimetres.",
    },
    gcurveWidth: {
      type: "expression",
      label: "Guiding Curve Width",
      unit: "mm",
      default: "0",
      tooltip: "Guiding-curve width along X.",
    },
    gcurveAspectRatio: {
      type: "expression",
      label: "Guiding Curve Aspect Ratio",
      default: "1",
      tooltip: "Height-to-width ratio for the guiding curve.",
    },
    gcurveSeN: {
      type: "expression",
      label: "Guiding Superellipse Exponent",
      default: "3",
      tooltip:
        "Exponent used when the guiding curve runs in superellipse mode.",
    },
    gcurveSf: {
      type: "expression",
      label: "Superformula Tuple",
      default: "",
      tooltip:
        "Comma-separated superformula parameters in the order a,b,m,n1,n2,n3.",
    },
    gcurveSfA: {
      type: "expression",
      label: "Superformula a",
      default: "",
      tooltip: "Superformula a parameter.",
    },
    gcurveSfB: {
      type: "expression",
      label: "Superformula b",
      default: "",
      tooltip: "Superformula b parameter.",
    },
    gcurveSfM1: {
      type: "expression",
      label: "Superformula m1",
      default: "",
      tooltip: "Superformula m1 parameter.",
    },
    gcurveSfM2: {
      type: "expression",
      label: "Superformula m2",
      default: "",
      tooltip: "Superformula m2 parameter.",
    },
    gcurveSfN1: {
      type: "expression",
      label: "Superformula n1",
      default: "",
      tooltip: "Superformula n1 parameter.",
    },
    gcurveSfN2: {
      type: "expression",
      label: "Superformula n2",
      default: "",
      tooltip: "Superformula n2 parameter.",
    },
    gcurveSfN3: {
      type: "expression",
      label: "Superformula n3",
      default: "",
      tooltip: "Superformula n3 parameter.",
    },
    gcurveRot: {
      type: "expression",
      label: "Guiding Curve Rotation",
      unit: "deg",
      default: "0",
      tooltip: "Rotate the guiding curve anticlockwise.",
    },
    circArcTermAngle: {
      type: "expression",
      label: "Circular Arc Terminal Angle",
      unit: "deg",
      default: "1",
      tooltip: "Mouth terminal angle for the circular-arc throat profile.",
    },
    circArcRadius: {
      type: "expression",
      label: "Circular Arc Radius Override",
      unit: "mm",
      default: "0",
      tooltip: "Explicit radius override for the circular-arc throat profile.",
    },
  },
  MORPH: {
    morphTarget: {
      type: "select",
      label: "Target Shape",
      options: [
        { value: 0, label: "None" },
        { value: 1, label: "Rectangle" },
        { value: 2, label: "Circle" },
      ],
      default: 1,
    },
    morphWidth: {
      type: "number",
      label: "Target Width",
      unit: "mm",
      default: 0,
    },
    morphHeight: {
      type: "number",
      label: "Target Height",
      unit: "mm",
      default: 0,
    },
    morphCorner: {
      type: "range",
      label: "Corner Radius",
      unit: "mm",
      min: 0,
      max: 100,
      step: 1,
      default: 0,
    },
    morphRate: { type: "number", label: "Morph Rate", step: 0.1, default: 3.0 },
    morphFixed: {
      type: "range",
      label: "Fixed Part",
      min: 0,
      max: 1,
      step: 0.01,
      default: 0.0,
    },
    morphAllowShrinkage: {
      type: "select",
      label: "Allow Shrinkage",
      options: [
        { value: 0, label: "No" },
        { value: 1, label: "Yes" },
      ],
      default: 0,
    },
  },
  MESH: {
    angularSegments: {
      type: "number",
      label: "Preview Angular Segments",
      default: 80,
      tooltip:
        "Three.js viewport tessellation around the horn circumference. Does not change backend OCC solve/export mesh element sizes.",
    },
    lengthSegments: {
      type: "number",
      label: "Preview Length Segments",
      default: 20,
      tooltip:
        "Three.js viewport tessellation along the horn length. Does not change backend OCC solve/export mesh element sizes.",
    },
    cornerSegments: {
      type: "number",
      label: "Preview Corner Segments",
      default: 4,
      tooltip:
        "Three.js viewport tessellation for rounded corners and morph edges only.",
    },
    throatSegments: {
      type: "number",
      label: "Preview Throat Segments",
      default: 0,
      tooltip:
        "Extra Three.js viewport tessellation near the throat. Does not change backend OCC solve/export mesh element sizes.",
    },
    throatResolution: {
      type: "number",
      label: "Throat Mesh Resolution",
      unit: "mm",
      default: 6.0,
      tooltip:
        "Backend OCC solve/export mesh element size near the throat. Also influences viewport slice spacing unless Throat Slice Density overrides it.",
    },
    mouthResolution: {
      type: "number",
      label: "Mouth Mesh Resolution",
      unit: "mm",
      default: 15.0,
      tooltip:
        "Backend OCC solve/export mesh element size near the mouth. Also influences viewport slice spacing unless Throat Slice Density overrides it.",
    },
    throatSliceDensity: {
      type: "number",
      label: "Preview Slice Bias",
      default: null,
      tooltip:
        "Viewport slice clustering (0.5 = uniform, lower = tighter near the throat). When set, it overrides the throat-to-mouth resolution ratio for viewport slice distribution only.",
    },
    verticalOffset: {
      type: "number",
      label: "Export Vertical Offset",
      unit: "mm",
      default: 0.0,
      tooltip:
        "Vertical offset for the simulation and export coordinate system. Does not affect the 3D viewer.",
    },
    wallThickness: {
      type: "number",
      label: "Wall Thickness",
      unit: "mm",
      default: 0,
      tooltip:
        "Applies only to freestanding horns (Enclosure Depth = 0). Builds a normal-offset wall shell one wall-thickness from the horn surface and a rear disc behind the throat.",
    },
    rearResolution: {
      type: "number",
      label: "Rear Mesh Resolution",
      unit: "mm",
      default: 40.0,
      tooltip:
        "Backend OCC solve/export mesh element size for the rear wall on freestanding thickened horns.",
    },
    quadrants: {
      type: "select",
      label: "Quadrants",
      default: "1234",
      tooltip:
        "Portion of the 3D mesh used for BEM analysis. 1 = Q1 only (x≥0, y≥0); 12 = Q1+Q2 (y≥0); 14 = Q1+Q4 (x≥0); 1234 = full mesh.",
      options: [
        { value: "1234", label: "Full (1234)" },
        { value: "12", label: "Half Y≥0 (12)" },
        { value: "14", label: "Half X≥0 (14)" },
        { value: "1", label: "Quarter Q1 (1)" },
      ],
    },
  },
  ENCLOSURE: {
    encDepth: {
      type: "number",
      label: "Enclosure Depth",
      unit: "mm",
      default: 0,
    },
    encEdge: { type: "number", label: "Edge Radius", unit: "mm", default: 18 },
    encEdgeType: {
      type: "select",
      label: "Edge Finish",
      options: [
        { value: 1, label: "Rounded" },
        { value: 2, label: "Chamfered" },
      ],
      default: 1,
    },
    encSpaceL: { type: "number", label: "Left Margin", default: 25 },
    encSpaceT: { type: "number", label: "Top Margin", default: 25 },
    encSpaceR: { type: "number", label: "Right Margin", default: 25 },
    encSpaceB: { type: "number", label: "Bottom Margin", default: 25 },
    encFrontResolution: {
      type: "expression",
      label: "Front Baffle Mesh Resolution",
      unit: "mm",
      default: "25,25,25,25",
      tooltip:
        "Backend OCC solve/export mesh element sizes for enclosure front-baffle quadrants (q1..q4).",
    },
    encBackResolution: {
      type: "expression",
      label: "Rear Baffle Mesh Resolution",
      unit: "mm",
      default: "40,40,40,40",
      tooltip:
        "Backend OCC solve/export mesh element sizes for enclosure back-baffle quadrants (q1..q4).",
    },
  },
  SOURCE: {
    sourceShape: {
      type: "select",
      label: "Source Surface",
      options: [
        { value: 1, label: "Spherical Cap" },
        { value: 2, label: "Flat Disc" },
      ],
      default: 1,
    },
    sourceRadius: { type: "number", label: "Source Radius", default: -1 },
    sourceCurv: {
      type: "select",
      label: "Source Curvature",
      options: [
        { value: 0, label: "Auto" },
        { value: 1, label: "Convex" },
        { value: -1, label: "Concave" },
      ],
      default: 0,
    },
    sourceVelocity: {
      type: "select",
      label: "Source Velocity",
      options: [
        { value: 1, label: "Normal" },
        { value: 2, label: "Axial" },
      ],
      default: 1,
    },
    sourceContours: {
      type: "expression",
      label: "Source Contours",
      default: "",
      tooltip: "Path or inline script for source contours.",
    },
  },
  SIMULATION: {
    freqStart: {
      type: "number",
      label: "Sweep Start",
      unit: "Hz",
      default: 400,
      min: 20,
      max: 20000,
      step: 10,
      controlId: "freq-start",
      tooltip: "Lowest frequency in the backend BEM sweep.",
    },
    freqEnd: {
      type: "number",
      label: "Sweep End",
      unit: "Hz",
      default: 16000,
      min: 20,
      max: 20000,
      step: 10,
      controlId: "freq-end",
      tooltip: "Highest frequency in the backend BEM sweep.",
    },
    numFreqs: {
      type: "number",
      label: "Frequency Samples",
      default: 40,
      min: 10,
      max: 200,
      step: 1,
      controlId: "freq-steps",
      tooltip: "Number of solved frequencies between the start and end values.",
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
