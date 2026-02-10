export const PARAM_SCHEMA = {
    'R-OSSE': {
        scale: {
            type: 'range',
            label: 'Scale',
            min: 0.1,
            max: 2,
            step: 0.001,
            default: 1.0,
            tooltip: 'Global scaling factor for all length dimensions. Values < 1 shrink the waveguide, > 1 enlarge it. Affects L, r0, morphCorner, and all other length parameters.'
        },
        R: { type: 'expression', label: 'R - Mouth Radius', unit: 'mm', default: 140, tooltip: 'Mouth radius as function of azimuthal angle p. Can be constant or expression.' },
        a: { type: 'expression', label: 'a - Coverage Angle', unit: 'deg', default: 25, tooltip: 'Coverage angle as function of p. Controls horn flare rate.' },
        a0: { type: 'expression', label: 'a0 - Throat Angle', unit: 'deg', default: 15.5, tooltip: 'Initial throat opening angle in degrees. Can be constant or expression like "15 + 2*sin(p)".' },
        r0: { type: 'expression', label: 'r0 - Throat Radius', unit: 'mm', default: 12.7, tooltip: 'Initial throat radius. Can be constant or expression like "12.7 + sin(p)".' },
        k: { type: 'range', label: 'k - Rounding', min: 0.1, max: 10, step: 0.1, default: 2.0, tooltip: 'Controls throat rounding/smoothness.' },
        m: { type: 'range', label: 'm - Apex Shift', min: 0, max: 1, step: 0.01, default: 0.85, tooltip: 'Shifts apex position along the horn axis.' },
        b: { type: 'expression', label: 'b - Bending', default: '0.2', tooltip: 'Controls profile curvature (bending).' },
        r: { type: 'range', label: 'r - Apex Radius', min: 0.01, max: 2, step: 0.01, default: 0.4, tooltip: 'Radius of the apex region.' },
        q: { type: 'range', label: 'q - Shape Factor', min: 0.5, max: 10, step: 0.1, default: 3.4, tooltip: 'Controls overall horn shape profile.' },
        tmax: { type: 'range', label: 'tmax - Truncation', min: 0.5, max: 1.5, step: 0.01, default: 1.0, tooltip: 'Truncates horn at a fraction of computed length.' },
    },
    'OSSE': {
        scale: {
            type: 'range',
            label: 'Scale',
            min: 0.1,
            max: 2,
            step: 0.001,
            default: 1.0,
            tooltip: 'Global scaling factor for all length dimensions. Values < 1 shrink the waveguide, > 1 enlarge it. Affects L, r0, morphCorner, and all other length parameters.'
        },
        L: { type: 'expression', label: 'L - Length of the Waveguide', unit: 'mm', default: 130, tooltip: 'Length of the waveguide (axial length). Can be constant or expression.' },
        a: { type: 'expression', label: 'a - Mouth Coverage Angle', unit: 'deg', default: '45 - 5*cos(2*p)^5 - 2*sin(p)^12', tooltip: 'Mouth coverage angle as function of p.' },
        a0: { type: 'expression', label: 'a0 - Throat Coverage Angle', unit: 'deg', default: 10, tooltip: 'Initial throat coverage angle in degrees. Can be constant or expression.' },
        r0: { type: 'expression', label: 'r0 - Throat Radius', unit: 'mm', default: 12.7, tooltip: 'Initial throat radius. Can be constant or expression like "12.7 + sin(p)".' },
        k: { type: 'range', label: 'k - Flare Constant', min: 0.1, max: 15, step: 0.1, default: 7.0, tooltip: 'Flare constant (rate of expansion).' },
        s: { type: 'expression', label: 's - Termination Shape', default: '0.85 + 0.3*cos(p)^2', tooltip: 'Shape factor for the termination flare.' },
        n: { type: 'range', label: 'n - Termination Curvature', min: 1, max: 10, step: 0.001, default: 4, tooltip: 'Curvature control exponent for the termination.' },
        q: { type: 'range', label: 'q - Termination Smoothness', min: 0.1, max: 2, step: 0.001, default: 0.991, tooltip: 'Transition smoothness parameter at the termination.' },
        h: { type: 'range', label: 'h - Shape Factor', min: 0, max: 10, step: 0.1, default: 0.0, tooltip: 'Additional shape control parameter.' },
    },
    'GEOMETRY': {
        throatProfile: {
            type: 'select',
            label: 'Throat Profile',
            options: [
                { value: 1, label: 'OS-SE (Profile 1)' },
                { value: 3, label: 'Circular Arc (Profile 3)' }
            ],
            default: 1,
            tooltip: 'Profile type: OS-SE or circular arc.'
        },
        throatExtAngle: { type: 'expression', label: 'Throat Ext Angle', unit: 'deg', default: '0', tooltip: 'Half-angle of the optional conical throat extension.' },
        throatExtLength: { type: 'expression', label: 'Throat Ext Length', unit: 'mm', default: '0', tooltip: 'Axial length of the optional conical throat extension.' },
        slotLength: { type: 'expression', label: 'Slot Length', unit: 'mm', default: '0', tooltip: 'Axial length of an initial straight waveguide segment.' },
        rot: { type: 'expression', label: 'Profile Rotation', unit: 'deg', default: '0', tooltip: 'Rotate the computed profile around point [0, r0].' },
        gcurveType: {
            type: 'select',
            label: 'Guiding Curve',
            options: [
                { value: 0, label: 'Explicit Coverage' },
                { value: 1, label: 'Superellipse' },
                { value: 2, label: 'Superformula' }
            ],
            default: 0,
            tooltip: 'Use guiding curve to infer coverage angle.'
        },
        gcurveDist: { type: 'expression', label: 'GCurve Dist', default: '0.5', tooltip: 'Guiding curve distance from throat (fraction or mm).' },
        gcurveWidth: { type: 'expression', label: 'GCurve Width', unit: 'mm', default: '0', tooltip: 'Guiding curve width along X.' },
        gcurveAspectRatio: { type: 'expression', label: 'GCurve Aspect Ratio', default: '1', tooltip: 'Height / width ratio for guiding curve.' },
        gcurveSeN: { type: 'expression', label: 'GCurve SE n', default: '3', tooltip: 'Exponent for guiding superellipse.' },
        gcurveSf: { type: 'expression', label: 'GCurve SF (a,b,m,n1,n2,n3)', default: '', tooltip: 'Superformula parameters as comma list.' },
        gcurveSfA: { type: 'expression', label: 'GCurve SF a', default: '', tooltip: 'Superformula a parameter.' },
        gcurveSfB: { type: 'expression', label: 'GCurve SF b', default: '', tooltip: 'Superformula b parameter.' },
        gcurveSfM1: { type: 'expression', label: 'GCurve SF m1', default: '', tooltip: 'Superformula m1 parameter.' },
        gcurveSfM2: { type: 'expression', label: 'GCurve SF m2', default: '', tooltip: 'Superformula m2 parameter.' },
        gcurveSfN1: { type: 'expression', label: 'GCurve SF n1', default: '', tooltip: 'Superformula n1 parameter.' },
        gcurveSfN2: { type: 'expression', label: 'GCurve SF n2', default: '', tooltip: 'Superformula n2 parameter.' },
        gcurveSfN3: { type: 'expression', label: 'GCurve SF n3', default: '', tooltip: 'Superformula n3 parameter.' },
        gcurveRot: { type: 'expression', label: 'GCurve Rotation', unit: 'deg', default: '0', tooltip: 'Rotate guiding curve anti-clockwise.' },
        circArcTermAngle: { type: 'expression', label: 'CircArc Term Angle', unit: 'deg', default: '1', tooltip: 'Mouth terminal angle for circular arc profile.' },
        circArcRadius: { type: 'expression', label: 'CircArc Radius', unit: 'mm', default: '0', tooltip: 'Explicit radius for circular arc profile.' }
    },
    'MORPH': {
        morphTarget: {
            type: 'select',
            label: 'Target Shape',
            options: [
                { value: 0, label: 'None' },
                { value: 1, label: 'Rectangle' },
                { value: 2, label: 'Circle' }
            ],
            default: 1
        },
        morphWidth: { type: 'number', label: 'Target Width', unit: 'mm', default: 0 },
        morphHeight: { type: 'number', label: 'Target Height', unit: 'mm', default: 0 },
        morphCorner: { type: 'range', label: 'Corner Radius', unit: 'mm', min: 0, max: 100, step: 1, default: 0 },
        morphRate: { type: 'number', label: 'Morph Rate', step: 0.1, default: 3.0 },
        morphFixed: { type: 'range', label: 'Fixed Part', min: 0, max: 1, step: 0.01, default: 0.0 },
        morphAllowShrinkage: {
            type: 'select',
            label: 'Allow Shrinkage',
            options: [
                { value: 0, label: 'No' },
                { value: 1, label: 'Yes' }
            ],
            default: 0
        }
    },
    'MESH': {
        angularSegments: { type: 'number', label: 'Angular Segs', default: 80 },
        lengthSegments: { type: 'number', label: 'Length Segs', default: 20 },
        cornerSegments: { type: 'number', label: 'Corner Segs', default: 4 },
        throatSegments: { type: 'number', label: 'Throat Segs', default: 0 },
        throatResolution: { type: 'number', label: 'Throat Resolution', unit: 'mm', default: 5.0 },
        mouthResolution: { type: 'number', label: 'Mouth Resolution', unit: 'mm', default: 10.0 },
        verticalOffset: { type: 'number', label: 'Vertical Offset', unit: 'mm', default: 0.0, tooltip: 'Vertical offset for simulation/export coordinate system. Does not affect the 3D viewer.' },
        subdomainSlices: { type: 'expression', label: 'Subdomain Slices', default: '', tooltip: 'Comma-separated slice indices for subdomain interfaces.' },
        interfaceOffset: { type: 'expression', label: 'Interface Offset', unit: 'mm', default: '', tooltip: 'Comma-separated interface offsets.' },
        interfaceDraw: { type: 'expression', label: 'Interface Draw', unit: 'mm', default: '', tooltip: 'Comma-separated interface draw depths.' },
        quadrants: {
            type: 'select',
            label: 'Quadrants',
            options: [
                { value: '1234', label: 'Full (1234)' },
                { value: '14', label: 'Half (14)' },
                { value: '12', label: 'Half (12)' },
                { value: '1', label: 'Quadrant (1)' }
            ],
            default: '14',
            tooltip: 'Simulation-only symmetry. The visible model stays full; simulation uses the selected quadrant.'
        },
        wallThickness: { type: 'number', label: 'Wall Thickness', unit: 'mm', default: 0, tooltip: 'Applies only to freestanding horns (Enclosure Depth = 0). Builds a normal-offset wall shell one wall-thickness from the horn surface and a rear disc behind the throat.' },
        rearResolution: { type: 'number', label: 'Rear Resolution', unit: 'mm', default: 10.0 },
    },
    'ENCLOSURE': {
        encDepth: { type: 'number', label: 'Enclosure Depth', unit: 'mm', default: 0 },
        encEdge: { type: 'number', label: 'Edge Radius', unit: 'mm', default: 18 },
        encEdgeType: {
            type: 'select',
            label: 'Edge Type',
            options: [
                { value: 1, label: 'Rounded' },
                { value: 2, label: 'Chamfered' }
            ],
            default: 1
        },
        encSpaceL: { type: 'number', label: 'Space L', default: 25 },
        encSpaceT: { type: 'number', label: 'Space T', default: 25 },
        encSpaceR: { type: 'number', label: 'Space R', default: 25 },
        encSpaceB: { type: 'number', label: 'Space B', default: 25 },
        encFrontResolution: { type: 'expression', label: 'Front Resolution', unit: 'mm', default: '', tooltip: 'Comma-separated front baffle resolutions (q1..q4).' },
        encBackResolution: { type: 'expression', label: 'Back Resolution', unit: 'mm', default: '', tooltip: 'Comma-separated back baffle resolutions (q1..q4).' },
    },
    'SOURCE': {
        sourceShape: {
            type: 'select',
            label: 'Wavefront',
            options: [
                { value: 1, label: 'Spherical Cap' },
                { value: 2, label: 'Flat Disc' }
            ],
            default: 1
        },
        sourceRadius: { type: 'number', label: 'Radius', default: -1 },
        sourceCurv: {
            type: 'select',
            label: 'Curvature',
            options: [
                { value: 0, label: 'Auto' },
                { value: 1, label: 'Convex' },
                { value: -1, label: 'Concave' }
            ],
            default: 0
        },
        sourceVelocity: {
            type: 'select',
            label: 'Velocity',
            options: [
                { value: 1, label: 'Normal' },
                { value: 2, label: 'Axial' }
            ],
            default: 1
        },
        sourceContours: { type: 'expression', label: 'Source Contours', default: '', tooltip: 'Path or inline script for source contours.' }
    },
    'ABEC': {
        abecSimType: {
            type: 'select',
            label: 'ABEC SimType',
            options: [
                { value: 1, label: 'Infinite Baffle' },
                { value: 2, label: 'Free Standing' }
            ],
            default: 1
        },
        abecSimProfile: { type: 'number', label: 'CircSym Profile', default: -1 },
        abecF1: { type: 'number', label: 'Start Freq', unit: 'Hz', default: 400 },
        abecF2: { type: 'number', label: 'End Freq', unit: 'Hz', default: 16000 },
        abecNumFreq: { type: 'number', label: 'Num Freqs', default: 40 },
        abecAbscissa: {
            type: 'select',
            label: 'Abscissa',
            options: [
                { value: 1, label: 'Log' },
                { value: 2, label: 'Linear' }
            ],
            default: 1
        },
        abecMeshFrequency: { type: 'number', label: 'Mesh Freq', unit: 'Hz', default: 1000 }
    },
    // Output actions are handled via export buttons in the UI.
};
