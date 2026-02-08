/**
 * ABEC project file generators.
 */

const DEFAULT_POLAR_RANGE = '0,180,37';

const abscissaMap = {
    1: 'log',
    2: 'lin'
};

const symmetryMap = {
    '14': 'x',
    '12': 'y',
    '1': 'xy'
};

import { evalParam } from '../geometry/common.js';

function normalizeAbscissa(value) {
    if (value === undefined || value === null) return 'log';
    const numeric = Number(value);
    if (Number.isFinite(numeric) && abscissaMap[numeric]) {
        return abscissaMap[numeric];
    }
    const text = String(value).trim().toLowerCase();
    if (!text) return 'log';
    if (text.startsWith('lin')) return 'lin';
    if (text.startsWith('log')) return 'log';
    return 'log';
}

function mapQuadrantsToSym(quadrants) {
    const key = String(quadrants || '').trim();
    return symmetryMap[key] || '';
}

/**
 * Generate the ABEC project (.abec) file content.
 * @param {Object} options
 * @param {string} options.solvingFileName
 * @param {string} options.observationFileName
 * @param {string} options.meshFileName
 * @returns {string}
 */
export function generateAbecProjectFile({ solvingFileName, observationFileName, meshFileName }) {
    return [
        '[Project]',
        'Scriptname_InfoFile=',
        '[Solving]',
        `Scriptname_Solving=${solvingFileName}`,
        '[DirectSound]',
        'Scriptname_DirectSound=',
        '[LEScript]',
        'Scriptname_LEScript=',
        '[Observation]',
        `C0=${observationFileName}`,
        '[MeshFiles]',
        `C0=${meshFileName},M1`,
        ''
    ].join('\n');
}

/**
 * Generate the ABEC solving configuration file content.
 * @param {Object} params
 * @returns {string}
 */
export function generateAbecSolvingFile(params) {
    const f1 = params.abecF1 ?? 100;
    const f2 = params.abecF2 ?? 20000;
    const numFreq = params.abecNumFreq ?? 40;
    const meshFrequency = params.abecMeshFrequency ?? 1000;
    const abscissa = normalizeAbscissa(params.abecAbscissa);
    const scale = 1;
    const circSymProfile = Number(params.abecSimProfile ?? -1);
    const dim = circSymProfile >= 0 ? 'CircSym' : '3D';
    const sym = dim === '3D' ? mapQuadrantsToSym(params.quadrants) : '';
    const symLine = sym ? `; Sym=${sym}` : '';
    const hasInterface = Boolean(
        params &&
        params.encDepth > 0 &&
        params.interfaceOffset !== undefined &&
        params.interfaceOffset !== null &&
        String(params.interfaceOffset).trim() !== ''
    );
    const simType = Number(params.abecSimType ?? 2);

    const output = [
        'Control_Solver',
        `  f1=${f1}; f2=${f2}; NumFrequencies=${numFreq}`,
        `  Abscissa=${abscissa}; Dim=${dim}; MeshFrequency=${meshFrequency}${symLine}`,
        '',
        'MeshFile_Properties',
        `  MeshFileAlias="M1"; Scale=${scale}mm`,
        ''
    ];

    if (hasInterface) {
        output.push(
            'SubDomain_Properties',
            '  SubDomain=1; ElType=Interior',
            '',
            'SubDomain_Properties',
            '  SubDomain=2; ElType=Exterior',
            ''
        );
    } else {
        output.push(
            'SubDomain_Properties',
            '  SubDomain=1; ElType=Exterior',
            ''
        );
    }

    output.push(
        'Elements "SD1G0"',
        '  Subdomain=1; MeshFileAlias="M1"',
        '  101 Mesh Include SD1G0',
        '',
        'Elements "SD1D1001"',
        '  Subdomain=1; MeshFileAlias="M1"',
        '  102 Mesh Include SD1D1001',
        '',
        'Driving "S1001"  // horn driver',
        '  RefElements="SD1D1001"; DrvGroup=1001;',
        ''
    );

    if (hasInterface) {
        output.push(
            'Elements "SD2G0"',
            '  Subdomain=2; MeshFileAlias="M1"',
            '  103 Mesh Include SD2G0',
            '',
            'Elements "I1-2"',
            '  SubDomain=1,2; MeshFileAlias="M1"',
            '  104 Mesh Include I1-2',
            ''
        );
    }

    if (simType === 1) {
        const L = evalParam(params.L ?? 0, 0);
        const extLen = Math.max(0, evalParam(params.throatExtLength ?? 0, 0));
        const slotLen = Math.max(0, evalParam(params.slotLength ?? 0, 0));
        const offset = Number.isFinite(L) ? (L + extLen + slotLen) : 0;
        output.push(
            'Infinite_Baffle',
            `  Subdomain=1; Position=z offset=${offset.toFixed(3)}mm`,
            ''
        );
    }

    return output.join('\n');
}

/**
 * Generate the ABEC observation configuration file content.
 * @param {Object} options
 * @param {string} [options.angleRange]
 * @param {number} [options.distance]
 * @param {number} [options.normAngle]
 * @param {number} [options.inclination]
 * @returns {string}
 */
export function generateAbecObservationFile({
    angleRange = DEFAULT_POLAR_RANGE,
    distance = 2,
    normAngle = 5,
    inclination = 0
} = {}) {
    return [
        'Driving_Values',
        '  DrvType=Acceleration; Value=1.0',
        '  401  DrvGroup=1001  Weight=1 Delay=0ms  // 0.00 dB',
        '',
        'Radiation_Impedance',
        '  BodeType=Complex; GraphHeader="RadImp"',
        '  Range_min=0; Range_max=2; RadImpType=Normalized',
        '  402   1001 1001   ID=8001',
        '',
        'BE_Spectrum',
        '  PlotType=Polar; GraphHeader="PM_SPL_H"',
        '  BodeType=LeveldB; Range_max=5; Range_min=-45',
        `  PolarRange=${angleRange}`,
        '  BasePlane=zx',
        `  Distance=${distance}m`,
        `  NormalizingAngle=${normAngle}`,
        '  501  Inclination=0  ID=5001',
        '',
        'BE_Spectrum',
        '  PlotType=Polar; GraphHeader="PM_SPL_V"',
        '  BodeType=LeveldB; Range_max=5; Range_min=-45',
        `  PolarRange=${angleRange}`,
        '  BasePlane=zx',
        `  Distance=${distance}m`,
        `  NormalizingAngle=${normAngle}`,
        `  502  Inclination=${inclination}  ID=5002`,
        ''
    ].join('\n');
}

export function generateAbecCoordsFile(vertices, ringCount) {
    if (!Array.isArray(vertices) || vertices.length === 0 || !Number.isFinite(ringCount) || ringCount <= 0) {
        return '';
    }
    const vertexCount = vertices.length / 3;
    const stationCount = Math.floor(vertexCount / ringCount);
    const lines = [];
    for (let j = 0; j < stationCount; j += 1) {
        const idx = j * ringCount * 3;
        const x = vertices[idx];
        const y = vertices[idx + 1];
        const z = vertices[idx + 2];
        const r = Math.sqrt(x * x + z * z);
        lines.push(`${y.toFixed(6)} ${r.toFixed(6)}`);
    }
    return lines.join('\n');
}

export function generateAbecStaticFile(vertices) {
    if (!Array.isArray(vertices) || vertices.length === 0) {
        return [
            "R_DIM='0 x 0 x 0 mm'",
            "R_DRIVER='N/A'",
            "R_VRMS='2.83 V'",
            "R_DIST='2 m'",
            "LINE_1=''",
            'R_XOFF=0',
            ''
        ].join('\n');
    }

    let minX = Infinity;
    let minY = Infinity;
    let minZ = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    let maxZ = -Infinity;
    for (let i = 0; i < vertices.length; i += 3) {
        const x = vertices[i];
        const y = vertices[i + 1];
        const z = vertices[i + 2];
        minX = Math.min(minX, x);
        minY = Math.min(minY, y);
        minZ = Math.min(minZ, z);
        maxX = Math.max(maxX, x);
        maxY = Math.max(maxY, y);
        maxZ = Math.max(maxZ, z);
    }
    const dx = Math.round(maxX - minX);
    const dy = Math.round(maxY - minY);
    const dz = Math.round(maxZ - minZ);

    return [
        `R_DIM='${dx} x ${dz} x ${dy} mm'`,
        "R_DRIVER='N/A'",
        "R_VRMS='2.83 V'",
        "R_DIST='2 m'",
        "LINE_1=''",
        'R_XOFF=0',
        ''
    ].join('\n');
}
