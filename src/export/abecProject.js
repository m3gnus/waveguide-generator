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

function parseNumeric(value, fallback = null) {
    if (value === undefined || value === null) return fallback;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : fallback;
}

function parsePolarBlockEntry(name, block, fallbackIndex = 0) {
    const key = String(name || '').split(':')[1] || `SPL_${fallbackIndex + 1}`;
    const items = block && typeof block === 'object' ? (block._items || {}) : {};

    const angleRange = String(items.MapAngleRange || DEFAULT_POLAR_RANGE).trim();
    const distance = parseNumeric(items.Distance, 2);
    const normAngle = parseNumeric(items.NormAngle, null);
    const inclination = parseNumeric(items.Inclination, 0);
    const offset = parseNumeric(items.Offset, null);

    return {
        graphHeader: `PM_${key}`,
        angleRange,
        distance,
        normAngle,
        inclination,
        offset
    };
}

export function extractPolarBlocks(blocks) {
    if (!blocks || typeof blocks !== 'object') return [];
    const entries = Object.entries(blocks)
        .filter(([name]) => name.startsWith('ABEC.Polars:'))
        .sort((a, b) => {
            const keyA = String(a[0]).split(':')[1] || '';
            const keyB = String(b[0]).split(':')[1] || '';
            return keyA.localeCompare(keyB, undefined, { sensitivity: 'base', numeric: true });
        });
    return entries.map(([name, block], idx) => parsePolarBlockEntry(name, block, idx));
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
export function generateAbecSolvingFile(params, options = {}) {
    const f1 = params.abecF1 ?? 100;
    const f2 = params.abecF2 ?? 20000;
    const numFreq = params.abecNumFreq ?? 40;
    const meshFrequency = params.abecMeshFrequency ?? 1000;
    const abscissa = normalizeAbscissa(params.abecAbscissa);
    const scale = params.scale ?? 1;
    const circSymProfile = Number(params.abecSimProfile ?? -1);
    const dim = circSymProfile >= 0 ? 'CircSym' : '3D';
    const sym = dim === '3D' ? mapQuadrantsToSym(params.quadrants) : '';
    const symLine = sym ? `; Sym=${sym}` : '';
    const hasInterface = options.interfaceEnabled !== undefined
        ? Boolean(options.interfaceEnabled)
        : Boolean(
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
        const inferredOffset = Number(options.infiniteBaffleOffset);
        const L = evalParam(params.L ?? 0, 0);
        const extLen = Math.max(0, evalParam(params.throatExtLength ?? 0, 0));
        const slotLen = Math.max(0, evalParam(params.slotLength ?? 0, 0));
        const fallbackOffset = Number.isFinite(L) ? (L + extLen + slotLen) : 0;
        const offset = Number.isFinite(inferredOffset) ? inferredOffset : fallbackOffset;
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
    inclination = 0,
    polarBlocks = null,
    allowDefaultPolars = true
} = {}) {
    const parsedPolarBlocks = extractPolarBlocks(polarBlocks);
    if (parsedPolarBlocks.length > 0) {
        const lines = [
            'Driving_Values',
            '  DrvType=Acceleration; Value=1.0',
            '  401  DrvGroup=1001  Weight=1 Delay=0ms  // 0.00 dB',
            '',
            'Radiation_Impedance',
            '  BodeType=Complex; GraphHeader="RadImp"',
            '  Range_min=0; Range_max=2; RadImpType=Normalized',
            '  402   1001 1001   ID=8001',
            ''
        ];

        parsedPolarBlocks.forEach((block, idx) => {
            lines.push(
                'BE_Spectrum',
                `  PlotType=Polar; GraphHeader="${block.graphHeader}"`,
                '  BodeType=LeveldB; Range_max=5; Range_min=-45',
                `  PolarRange=${block.angleRange}`,
                '  BasePlane=zx',
                `  Distance=${block.distance}m`
            );
            if (block.offset !== null) {
                lines.push(`  Offset=${block.offset}mm`);
            }
            if (block.normAngle !== null) {
                lines.push(`  NormalizingAngle=${block.normAngle}`);
            }
            lines.push(`  ${501 + idx}  Inclination=${block.inclination}  ID=${5001 + idx}`, '', '');
        });

        return lines.join('\n');
    }

    if (polarBlocks && !allowDefaultPolars) {
        return [
            'Driving_Values',
            '  DrvType=Acceleration; Value=1.0',
            '  401  DrvGroup=1001  Weight=1 Delay=0ms  // 0.00 dB',
            '',
            'Radiation_Impedance',
            '  BodeType=Complex; GraphHeader=\"RadImp\"',
            '  Range_min=0; Range_max=2; RadImpType=Normalized',
            '  402   1001 1001   ID=8001',
            ''
        ].join('\n');
    }

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

