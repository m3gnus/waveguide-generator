
export const PARAM_SCHEMA = {
    'R-OSSE': {
        R: { type: 'expression', label: 'Mouth Radius', unit: 'mm', default: '140 * (abs(cos(p)/1.6)^3 + abs(sin(p)/1)^4)^(-1/4.5)', tooltip: 'Mouth radius as function of azimuthal angle p. Can be constant or expression.' },
        a: { type: 'expression', label: 'Aperture Angle', unit: 'deg', default: '25 * (abs(cos(p)/1.2)^4 + abs(sin(p)/1)^3)^(-1/2.5)', tooltip: 'Aperture angle as function of p. Controls horn flare rate.' },
        a0: { type: 'range', label: 'Throat Angle', unit: 'deg', min: 0, max: 60, step: 0.1, default: 15.5, tooltip: 'Initial throat angle in degrees' },
        r0: { type: 'range', label: 'Throat Radius', unit: 'mm', min: 1, max: 50, step: 0.1, default: 12.7, tooltip: 'Throat radius (typically matches driver radius)' },
        k: { type: 'range', label: 'Rounding', min: 0.1, max: 10, step: 0.1, default: 2.0, tooltip: 'Controls throat rounding/smoothness' },
        m: { type: 'range', label: 'Apex Shift', min: 0, max: 1, step: 0.01, default: 0.85, tooltip: 'Shifts apex position along horn axis' },
        b: { type: 'expression', label: 'Bending', default: '0.2', tooltip: 'Controls profile curvature (bending)' },
        r: { type: 'range', label: 'Apex Radius', min: 0.01, max: 2, step: 0.01, default: 0.4, tooltip: 'Radius of apex region' },
        q: { type: 'range', label: 'Shape Factor', min: 0.5, max: 10, step: 0.1, default: 3.4, tooltip: 'Controls overall horn shape profile' },
        tmax: { type: 'range', label: 'Truncation', min: 0.5, max: 1.5, step: 0.01, default: 1.0, tooltip: 'Truncates horn at fraction of computed length' },
    },
    'OSSE': {
        L: { type: 'range', label: 'Axial Length', unit: 'mm', min: 10, max: 500, step: 1, default: 120, tooltip: 'Total axial length of horn' },
        a: { type: 'expression', label: 'Coverage Angle', unit: 'deg', default: '48.5 - 5.6*cos(2*p)^5 - 31*sin(p)^12', tooltip: 'Coverage angle as function of p. Controls directivity pattern.' },
        a0: { type: 'range', label: 'Throat Angle', unit: 'deg', min: 0, max: 60, step: 0.1, default: 15.5, tooltip: 'Initial throat angle in degrees' },
        r0: { type: 'range', label: 'Throat Radius', unit: 'mm', min: 1, max: 50, step: 0.1, default: 12.7, tooltip: 'Throat radius (typically matches driver radius)' },
        k: { type: 'range', label: 'Expansion', min: 0.1, max: 15, step: 0.1, default: 7.0, tooltip: 'Controls expansion rate' },
        s: { type: 'expression', label: 'Flare', default: '0.58 + 0.2*cos(p)^2', tooltip: 'Flare parameter controlling mouth opening' },
        n: { type: 'range', label: 'Curvature', min: 1, max: 10, step: 0.001, default: 4.158, tooltip: 'Controls profile curvature' },
        q: { type: 'range', label: 'Truncation', min: 0.1, max: 2, step: 0.001, default: 0.991, tooltip: 'Truncation factor (< 1 shortens horn)' },
        h: { type: 'range', label: 'Shape Factor', min: 0, max: 10, step: 0.1, default: 0.0, tooltip: 'Additional shape control parameter' },
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
                { value: 1, label: 'Full Model' },
                { value: 2, label: 'Flat Disc' },
                { value: 0, label: 'None (Open)' }
            ],
            default: 1
        },
        rearRadius: { type: 'range', label: 'Rear Radius', unit: 'mm', min: 50, max: 500, step: 1, default: 150 }
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
