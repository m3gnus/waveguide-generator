export const PARAM_SCHEMA = {
    'R-OSSE': {
        R: { type: 'expression', label: 'R - Mouth Radius', unit: 'mm', default: '140 * (abs(cos(p)/1.6)^3 + abs(sin(p)/1)^4)^(-1/4.5)', tooltip: 'Mouth radius as function of azimuthal angle p. Can be constant or expression.' },
        a: { type: 'expression', label: 'a - Aperture Angle', unit: 'deg', default: '25 * (abs(cos(p)/1.2)^4 + abs(sin(p)/1)^3)^(-1/2.5)', tooltip: 'Aperture (coverage) angle as function of p. Controls horn flare rate.' },
        a0: { type: 'range', label: 'a0 - Throat Angle', unit: 'deg', min: 0, max: 60, step: 0.1, default: 15.5, tooltip: 'Initial throat opening angle in degrees.' },
        r0: { type: 'range', label: 'r0 - Throat Radius', unit: 'mm', min: 1, max: 50, step: 0.1, default: 12.7, tooltip: 'Initial throat radius (typically matches driver radius).' },
        k: { type: 'range', label: 'k - Rounding', min: 0.1, max: 10, step: 0.1, default: 2.0, tooltip: 'Controls throat rounding/smoothness.' },
        m: { type: 'range', label: 'm - Apex Shift', min: 0, max: 1, step: 0.01, default: 0.85, tooltip: 'Shifts apex position along the horn axis.' },
        b: { type: 'expression', label: 'b - Bending', default: '0.2', tooltip: 'Controls profile curvature (bending).' },
        r: { type: 'range', label: 'r - Apex Radius', min: 0.01, max: 2, step: 0.01, default: 0.4, tooltip: 'Radius of the apex region.' },
        q: { type: 'range', label: 'q - Shape Factor', min: 0.5, max: 10, step: 0.1, default: 3.4, tooltip: 'Controls overall horn shape profile.' },
        tmax: { type: 'range', label: 'tmax - Truncation', min: 0.5, max: 1.5, step: 0.01, default: 1.0, tooltip: 'Truncates horn at a fraction of computed length.' },
    },
    'OSSE': {
        L: { type: 'range', label: 'L - Length of the Waveguide', unit: 'mm', min: 10, max: 500, step: 1, default: 120, tooltip: 'Length of the waveguide (axial length).' },
        a: { type: 'expression', label: 'a - Mouth Coverage Angle', unit: 'deg', default: '48.5 - 5.6*cos(2*p)^5 - 31*sin(p)^12', tooltip: 'Mouth coverage angle as function of p.' },
        a0: { type: 'range', label: 'a0 - Throat Coverage Angle', unit: 'deg', min: 0, max: 60, step: 0.1, default: 15.5, tooltip: 'Initial throat coverage angle in degrees.' },
        r0: { type: 'range', label: 'r0 - Throat Radius', unit: 'mm', min: 1, max: 50, step: 0.1, default: 12.7, tooltip: 'Initial throat radius.' },
        k: { type: 'range', label: 'k - Flare Constant', min: 0.1, max: 15, step: 0.1, default: 7.0, tooltip: 'Flare constant (rate of expansion).' },
        s: { type: 'expression', label: 's - Termination Shape', default: '0.58 + 0.2*cos(p)^2', tooltip: 'Shape factor for the termination flare.' },
        n: { type: 'range', label: 'n - Termination Curvature', min: 1, max: 10, step: 0.001, default: 4.158, tooltip: 'Curvature control exponent for the termination.' },
        q: { type: 'range', label: 'q - Termination Smoothness', min: 0.1, max: 2, step: 0.001, default: 0.991, tooltip: 'Transition smoothness parameter at the termination.' },
        h: { type: 'range', label: 'h - Shape Factor', min: 0, max: 10, step: 0.1, default: 0.0, tooltip: 'Additional shape control parameter.' },
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
        morphFixed: { type: 'range', label: 'Fixed Part', min: 0, max: 1, step: 0.01, default: 0.0 }
    },
    'MESH': {
        angularSegments: { type: 'number', label: 'Angular Segs', default: 80 },
        lengthSegments: { type: 'number', label: 'Length Segs', default: 20 },
        cornerSegments: { type: 'number', label: 'Corner Segs', default: 4 },
        quadrants: {
            type: 'select',
            label: 'Quadrants',
            options: [
                { value: '1234', label: 'Full (1234)' },
                { value: '14', label: 'Half (14)' },
                { value: '12', label: 'Half (12)' },
                { value: '1', label: 'Quadrant (1)' }
            ],
            default: '1234'
        },
        wallThickness: { type: 'number', label: 'Wall Thickness', unit: 'mm', default: 5.0 },
        rearShape: {
            type: 'select',
            label: 'Rear Shape',
            options: [
                { value: 0, label: 'None (Open)' },
                { value: 1, label: 'Full Model' },
                { value: 2, label: 'Flat Disc' }
            ],
            default: 0
        },
    },
    'ROLLBACK': {
        rollback: {
            type: 'select',
            label: 'Rollback',
            options: [
                { value: false, label: 'Off' },
                { value: true, label: 'On' }
            ],
            default: false,
            tooltip: 'Add toroidal rollback fold at the mouth'
        },
        rollbackAngle: { type: 'range', label: 'Rollback Angle', unit: 'deg', min: 30, max: 270, step: 1, default: 180, tooltip: 'How far the lip curls back (degrees)' },
        rollbackStart: { type: 'range', label: 'Rollback Start', min: 0.1, max: 0.99, step: 0.01, default: 0.5, tooltip: 'Where the rollback begins (0=throat, 1=mouth)' }
    },
    'ENCLOSURE': {
        encDepth: { type: 'number', label: 'Enclosure Depth', unit: 'mm', default: 280 },
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
        encSpaceB: { type: 'number', label: 'Space B', default: 25 }
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
        sourceVelocity: {
            type: 'select',
            label: 'Velocity',
            options: [
                { value: 1, label: 'Normal' },
                { value: 2, label: 'Axial' }
            ],
            default: 1
        }
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
        abecF1: { type: 'number', label: 'Start Freq', unit: 'Hz', default: 400 },
        abecF2: { type: 'number', label: 'End Freq', unit: 'Hz', default: 16000 },
        abecNumFreq: { type: 'number', label: 'Num Freqs', default: 40 }
    }
};
