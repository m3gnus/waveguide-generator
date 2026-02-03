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
    const scaleValue = Number(params.scale ?? 1);
    const scale = Number.isFinite(scaleValue) ? scaleValue : 1;
    const sym = mapQuadrantsToSym(params.quadrants);
    const symLine = sym ? `; Sym=${sym}` : '';

    return [
        'Control_Solver',
        `  f1=${f1}; f2=${f2}; NumFrequencies=${numFreq}`,
        `  Abscissa=${abscissa}; Dim=3D; MeshFrequency=${meshFrequency}${symLine}`,
        '',
        'MeshFile_Properties',
        `  MeshFileAlias="M1"; Scale=${scale}mm`,
        '',
        'SubDomain_Properties',
        '  SubDomain=1; ElType=Exterior',
        '',
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
    ].join('\n');
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
        '  PlotType=Polar; GraphHeader="PM_SPL"',
        '  BodeType=LeveldB; Range_max=5; Range_min=-45',
        `  PolarRange=${angleRange}`,
        '  BasePlane=zx',
        `  Distance=${distance}m`,
        `  NormalizingAngle=${normAngle}`,
        `  501  Inclination=${inclination}  ID=5001`,
        ''
    ].join('\n');
}
