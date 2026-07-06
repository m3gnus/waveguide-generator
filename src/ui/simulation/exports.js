import { applySmoothing } from '../../results/smoothing.js';
import { extractFlatDI } from './diHelpers.js';
import { DEFAULT_BACKEND_URL } from '../../config/backendUrl.js';
import {
  buildMwgConfigExportFiles,
  buildProfileCsvExportFiles,
  buildStepExportFiles,
  buildStlExportFiles,
} from '../../modules/export/useCases.js';
import { readSimulationState } from '../../modules/simulation/state.js';
import { writeSimulationTaskBundleFile } from './workspaceTasks.js';
import {
  SIMULATION_EXPORT_FORMAT_IDS,
  getSelectedExportFormats,
} from '../settings/simulationManagementSettings.js';
import { showError, showMessage } from '../feedback.js';
import { getExportBaseName, saveFile } from '../fileOps.js';
import { resolveGenerationExportFileName } from '../workspace/generationArtifacts.js';
import { resolveTaskWorkspaceDirectoryName } from '../workspace/taskManifest.js';
import { getCachedRuntimeHealth } from '../runtimeCapabilities.js';
import { getFeatureBlockedReason } from '../dependencyStatus.js';

const EXPORT_FORMAT_LABELS = Object.freeze({
  mwg_config: 'Parameter Config (.txt)',
  step: 'Waveguide STEP',
  png: 'Chart Images (PNG)',
  csv: 'Frequency Data CSV',
  json: 'Full Results JSON',
  txt: 'Summary Text Report',
  polar_csv: 'Polar Directivity CSV',
  impedance_csv: 'Impedance CSV',
  vacs: 'ABEC Spectrum (VACS)',
  stl: 'Waveguide STL',
  fusion_csv: 'Fusion 360 CSV Curves',
});
const RESULT_EXPORT_FORMAT_IDS = Object.freeze([
  'png',
  'csv',
  'json',
  'txt',
  'polar_csv',
  'impedance_csv',
  'vacs',
]);
const DIRECTIVITY_PLANE_ORDER = ['horizontal', 'vertical', 'diagonal'];
const LEGACY_IMPEDANCE_RHO_C = 1.21 * 343.0;
const LEGACY_IMPEDANCE_THRESHOLD = 20.0;

function normalizeSelectedFormats(formatIds) {
  const seen = new Set();
  const normalized = [];

  for (const raw of formatIds || []) {
    const id = String(raw || '').trim();
    if (!SIMULATION_EXPORT_FORMAT_IDS.includes(id) || seen.has(id)) {
      continue;
    }
    seen.add(id);
    normalized.push(id);
  }

  return normalized;
}

function resolveExportBaseName(job = null) {
  const label = String(job?.label || '').trim();
  return label || getExportBaseName() || 'simulation';
}

function resolveExportDirectoryName(job = null, baseName = null) {
  const fallback = String(baseName || '').trim() || 'simulation';
  if (job?.id || job?.label) {
    return resolveTaskWorkspaceDirectoryName(job, { fallbackId: fallback });
  }
  return resolveTaskWorkspaceDirectoryName({ label: fallback }, { fallbackId: fallback });
}

function readExportState() {
  return readSimulationState();
}

async function writeExportFile(file, options = {}) {
  const { writer = null } = options;
  if (typeof writer === 'function') {
    return writer(file);
  }

  await saveFile(file.content, file.fileName, {
    ...file.saveOptions,
    workspaceSubdir:
      options.workspaceSubdir ?? resolveExportDirectoryName(options.job, options.baseName),
    incrementCounter: false,
  });
  return file.fileName;
}

async function writeExportFiles(files, options = {}) {
  const savedFiles = [];
  for (const file of files || []) {
    savedFiles.push(await writeExportFile(file, options));
  }
  return savedFiles;
}

function createDownloadFile(fileName, content, saveOptions = {}) {
  return {
    fileName,
    content,
    saveOptions,
  };
}

function createGenerationDownloadFile(formatId, baseName, content, saveOptions = {}, options = {}) {
  const fileName = resolveGenerationExportFileName(formatId, {
    baseName,
    chartKey: options.chartKey,
    originalFileName: options.originalFileName,
  });
  return createDownloadFile(fileName, content, saveOptions);
}

function normalizeDirectivityByPlane(directivity) {
  if (!directivity || typeof directivity !== 'object' || Array.isArray(directivity)) {
    return {};
  }
  const entries = Object.entries(directivity).filter(([, patterns]) => Array.isArray(patterns));
  entries.sort(([a], [b]) => {
    const aKey = String(a || '')
      .trim()
      .toLowerCase();
    const bKey = String(b || '')
      .trim()
      .toLowerCase();
    const aIndex = DIRECTIVITY_PLANE_ORDER.indexOf(aKey);
    const bIndex = DIRECTIVITY_PLANE_ORDER.indexOf(bKey);
    if (aIndex !== -1 && bIndex !== -1) return aIndex - bIndex;
    if (aIndex !== -1) return -1;
    if (bIndex !== -1) return 1;
    return aKey.localeCompare(bKey);
  });
  return Object.fromEntries(entries);
}

function formatPolarDbCsvValue(value) {
  if (value == null) {
    return '';
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(2) : '';
}

function resolveVacPlaneLabel(plane) {
  const normalized = String(plane || '')
    .trim()
    .toLowerCase();
  if (normalized === 'horizontal') return 'Horizontal';
  if (normalized === 'vertical') return 'Vertical';
  if (normalized === 'diagonal') return 'Diagonal';
  return normalized
    .split(/[^a-z0-9]+/i)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function resolveVacPlaneSuffix(plane) {
  const normalized = String(plane || '')
    .trim()
    .toLowerCase();
  if (normalized === 'horizontal') return 'H';
  if (normalized === 'vertical') return 'V';
  if (normalized === 'diagonal') return 'D';
  const compact = normalized.replaceAll(/[^a-z0-9]/gi, '').toUpperCase();
  return compact || 'P';
}

function resolveVacReferencePlane(directivityByPlane) {
  if (Array.isArray(directivityByPlane.horizontal) && directivityByPlane.horizontal.length > 0) {
    return 'horizontal';
  }
  for (const plane of Object.keys(directivityByPlane)) {
    const patterns = directivityByPlane[plane];
    if (Array.isArray(patterns) && patterns.length > 0) {
      return plane;
    }
  }
  return null;
}

function normalizeGenerationExportFiles(files = [], formatId, baseName) {
  return (files || []).map((file) => ({
    ...file,
    fileName: resolveGenerationExportFileName(formatId, {
      baseName,
      originalFileName: file?.fileName,
    }),
  }));
}

function localTimestampParts() {
  const now = new Date();
  return {
    now,
    dateStamp: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`,
    timeStamp: `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`,
  };
}

function requireSimulationResults(panel) {
  const results = panel.lastResults;
  if (!results) {
    throw new Error('No simulation results available.');
  }
  return results;
}

function finiteImpedanceNumber(value) {
  if (value == null || value === '') {
    return null;
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function isRhoCNormalizedImpedance(results) {
  const metadata = results?.metadata || {};
  const units = String(metadata?.impedance_units || metadata?.impedance?.units || '')
    .trim()
    .toLowerCase()
    .replaceAll(' ', '');
  const normalization = String(
    metadata?.impedance_normalization || metadata?.impedance?.normalization || ''
  )
    .trim()
    .toLowerCase()
    .replaceAll('-', '_');
  const quantity = String(metadata?.impedance_quantity || metadata?.impedance?.quantity || '')
    .trim()
    .toLowerCase();

  return (
    units === 'z/(rho*c)' ||
    units === 'z/rhoc' ||
    normalization === 'rho_c' ||
    quantity === 'specific_acoustic_impedance'
  );
}

function normalizeLegacyImpedanceSeries(real = [], imaginary = [], options = {}) {
  const realSeries = Array.isArray(real) ? real : [];
  const imaginarySeries = Array.isArray(imaginary) ? imaginary : [];
  if (options.alreadyNormalized) {
    return { real: realSeries, imaginary: imaginarySeries };
  }

  const finiteValues = [...realSeries, ...imaginarySeries]
    .map(finiteImpedanceNumber)
    .filter((value) => value !== null);

  if (
    finiteValues.length === 0 ||
    Math.max(...finiteValues.map((value) => Math.abs(value))) <= LEGACY_IMPEDANCE_THRESHOLD
  ) {
    return { real: realSeries, imaginary: imaginarySeries };
  }

  const normalizeValue = (value) => {
    const numeric = finiteImpedanceNumber(value);
    return numeric === null ? value : Number((numeric / LEGACY_IMPEDANCE_RHO_C).toPrecision(12));
  };

  return {
    real: realSeries.map(normalizeValue),
    imaginary: imaginarySeries.map(normalizeValue),
  };
}

function readNormalizedImpedanceSeries(results, fallbackFrequencies = []) {
  const impedanceData = results?.impedance || {};
  const normalized = normalizeLegacyImpedanceSeries(
    impedanceData.real || [],
    impedanceData.imaginary || [],
    { alreadyNormalized: isRhoCNormalizedImpedance(results) }
  );
  return {
    frequencies: impedanceData.frequencies || fallbackFrequencies,
    real: normalized.real,
    imaginary: normalized.imaginary,
  };
}

function readResultSeries(panel) {
  const results = requireSimulationResults(panel);
  const smoothing = panel.currentSmoothing;
  const splData = results.spl_on_axis || {};
  const frequencies = splData.frequencies || [];
  const phaseDegrees = splData.phase_degrees || [];
  const diData = results.di || {};
  const diFrequencies = diData.frequencies || frequencies;
  const impedanceData = readNormalizedImpedanceSeries(results, frequencies);
  const impedanceFrequencies = impedanceData.frequencies;

  let spl = splData.spl || [];
  let di = extractFlatDI(diData);
  let impedanceReal = impedanceData.real;
  let impedanceImaginary = impedanceData.imaginary;

  if (smoothing !== 'none') {
    spl = applySmoothing(frequencies, spl, smoothing);
    di = applySmoothing(diFrequencies, di, smoothing);
    impedanceReal = applySmoothing(impedanceFrequencies, impedanceReal, smoothing);
    impedanceImaginary = applySmoothing(impedanceFrequencies, impedanceImaginary, smoothing);
  }

  return {
    results,
    frequencies,
    spl,
    phaseDegrees,
    di,
    diFrequencies,
    impedanceFrequencies,
    impedanceReal,
    impedanceImaginary,
    directivity: results.directivity || {},
  };
}

function finiteSeriesValues(values = []) {
  return values.map((value) => Number(value)).filter((value) => Number.isFinite(value));
}

function formatReportCell(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(2) : 'n/a';
}

function resolvePhaseReferenceDistance(results) {
  const metadata = results?.metadata || {};
  const directivityDistance = Number(metadata?.directivity?.effective_distance_m);
  if (Number.isFinite(directivityDistance) && directivityDistance > 0) {
    return directivityDistance;
  }
  const observationDistance = Number(metadata?.observation?.effective_distance_m);
  if (Number.isFinite(observationDistance) && observationDistance > 0) {
    return observationDistance;
  }
  return null;
}

function resolvePhaseTimeConvention(results) {
  const metadata = results?.metadata || {};
  const explicitPhase = String(metadata?.phase_time_convention || '')
    .trim()
    .toLowerCase()
    .replaceAll('_', '-')
    .replaceAll(' ', '');
  if (
    explicitPhase === 'exp(+ikr)' ||
    explicitPhase === 'e(+ikr)' ||
    explicitPhase === '+ikr' ||
    explicitPhase === 'positive' ||
    explicitPhase === 'positive-spatial'
  ) {
    return 'metal';
  }
  if (
    explicitPhase === 'exp(-ikr)' ||
    explicitPhase === 'e(-ikr)' ||
    explicitPhase === '-ikr' ||
    explicitPhase === 'negative' ||
    explicitPhase === 'negative-spatial' ||
    explicitPhase === 'legacy'
  ) {
    return 'bempp';
  }

  const engine = String(metadata?.engine || '')
    .trim()
    .toLowerCase();
  if (engine === 'hornlab-bempp-bem') {
    return 'metal';
  }

  const selected = String(metadata?.device_interface?.selected || '')
    .trim()
    .toLowerCase()
    .replaceAll('_', '-');
  if (selected === 'metal' || selected === 'bempp-cl-numba' || selected === 'bempp-cl-opencl') {
    return 'metal';
  }

  const backend = String(metadata?.solver_backend || '')
    .trim()
    .toLowerCase()
    .replaceAll('_', '-');
  if (backend === 'metal' || backend === 'hornlab-metal' || backend === 'hornlab-metal-bem') {
    return 'metal';
  }
  if (backend === 'bempp' || backend === 'bempp-cl' || backend === 'bemppcl') {
    return 'bempp';
  }
  if (metadata?.metal && typeof metadata.metal === 'object') {
    return 'metal';
  }
  return null;
}

function formatCsvCell(value) {
  return value ?? '';
}

async function buildMatplotlibPngFiles(panel, { baseName } = {}) {
  requireSimulationResults(panel);

  const cachedHealth = getCachedRuntimeHealth();
  if (cachedHealth) {
    const blockedReason = getFeatureBlockedReason(cachedHealth, 'matplotlib');
    if (blockedReason) {
      throw new Error(`Chart rendering blocked: ${blockedReason}`);
    }
  }

  const {
    results,
    frequencies,
    spl,
    phaseDegrees,
    di,
    diFrequencies,
    impedanceFrequencies,
    impedanceReal,
    impedanceImaginary,
    directivity,
  } = readResultSeries(panel);

  const payload = {
    frequencies,
    spl,
    phase_degrees: phaseDegrees,
    phase_reference_distance_m: resolvePhaseReferenceDistance(results),
    phase_time_convention: resolvePhaseTimeConvention(results),
    di,
    di_frequencies: diFrequencies,
    impedance_frequencies: impedanceFrequencies,
    impedance_real: impedanceReal,
    impedance_imaginary: impedanceImaginary,
    impedance_units: 'Z/(rho*c)',
    impedance_normalization: 'rho_c',
    directivity,
  };

  const backendUrl = panel?.solver?.backendUrl || DEFAULT_BACKEND_URL;
  const response = await fetch(`${backendUrl}/api/render-charts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    if (response.status === 503) {
      throw new Error(
        'Matplotlib is not installed on the backend. Install it with: pip install matplotlib'
      );
    }
    throw new Error(`Chart rendering failed: ${detail || response.status}`);
  }

  const data = await response.json();
  const charts = data.charts || {};
  const chartKeys = Object.keys(charts);
  if (chartKeys.length === 0) {
    throw new Error('No charts were rendered by the backend.');
  }

  return chartKeys.map((key) => {
    const b64 = charts[key];
    const byteString = atob(b64.split(',')[1]);
    const ab = new ArrayBuffer(byteString.length);
    const ia = new Uint8Array(ab);
    for (let i = 0; i < byteString.length; i += 1) {
      ia[i] = byteString.charCodeAt(i);
    }

    return createGenerationDownloadFile(
      'png',
      baseName,
      new Blob([ab], { type: 'image/png' }),
      {
        contentType: 'image/png',
        typeInfo: {
          description: 'PNG Image',
          accept: { 'image/png': ['.png'] },
        },
      },
      { chartKey: key }
    );
  });
}

function buildCsvFile(panel, { baseName } = {}) {
  const { frequencies, spl, di, impedanceReal, impedanceImaginary } = readResultSeries(panel);

  let csv =
    'Frequency (Hz),SPL (dB),DI (dB),Impedance Real (Z/(rho*c)),Impedance Imag (Z/(rho*c))\n';
  for (let i = 0; i < frequencies.length; i += 1) {
    csv += [
      frequencies[i],
      formatCsvCell(spl[i]),
      formatCsvCell(di[i]),
      formatCsvCell(impedanceReal[i]),
      formatCsvCell(impedanceImaginary[i]),
    ].join(',');
    csv += '\n';
  }

  if (panel.currentSmoothing !== 'none') {
    csv = `# Smoothing: ${panel.currentSmoothing}\n${csv}`;
  }

  return createGenerationDownloadFile('csv', baseName, csv, {
    contentType: 'text/csv',
    typeInfo: { description: 'CSV File', accept: { 'text/csv': ['.csv'] } },
  });
}

function buildJsonFile(panel, { baseName } = {}) {
  if (!panel.lastResults) {
    throw new Error('No simulation results available.');
  }

  const { dateStamp, timeStamp } = localTimestampParts();
  const exportData = {
    timestamp: `${dateStamp} ${timeStamp}`,
    smoothing: panel.currentSmoothing,
    results: panel.lastResults,
  };

  return createGenerationDownloadFile('json', baseName, JSON.stringify(exportData, null, 2), {
    contentType: 'application/json',
    typeInfo: {
      description: 'JSON File',
      accept: { 'application/json': ['.json'] },
    },
  });
}

function buildTextFile(panel, { baseName } = {}) {
  const { frequencies, spl, di, impedanceReal, impedanceImaginary } = readResultSeries(panel);

  const { dateStamp, timeStamp } = localTimestampParts();

  let report = 'BEM SIMULATION RESULTS\n';
  report += '=====================\n\n';
  report += `Generated: ${dateStamp} ${timeStamp}\n`;
  report += `Smoothing: ${panel.currentSmoothing}\n`;
  if (frequencies.length > 0) {
    report += `Frequency range: ${Math.min(...frequencies).toFixed(0)} - ${Math.max(...frequencies).toFixed(0)} Hz\n`;
  } else {
    report += 'Frequency range: n/a\n';
  }
  report += `Number of points: ${frequencies.length}\n\n`;

  const validSpl = finiteSeriesValues(spl);
  if (validSpl.length > 0) {
    const avgSPL = validSpl.reduce((a, b) => a + b, 0) / validSpl.length;
    const minSPL = Math.min(...validSpl);
    const maxSPL = Math.max(...validSpl);

    report += 'FREQUENCY RESPONSE SUMMARY\n';
    report += '--------------------------\n';
    report += `Average SPL: ${avgSPL.toFixed(2)} dB\n`;
    report += `SPL Range: ${minSPL.toFixed(2)} to ${maxSPL.toFixed(2)} dB\n`;
    report += `Variation: ${(maxSPL - minSPL).toFixed(2)} dB\n\n`;
  }

  if (di.length > 0) {
    const validDI = finiteSeriesValues(di);
    if (validDI.length > 0) {
      const avgDI = validDI.reduce((a, b) => a + b, 0) / validDI.length;
      const minDI = Math.min(...validDI);
      const maxDI = Math.max(...validDI);

      report += 'DIRECTIVITY INDEX SUMMARY\n';
      report += '-------------------------\n';
      report += `Average DI: ${avgDI.toFixed(2)} dB\n`;
      report += `DI Range: ${minDI.toFixed(2)} to ${maxDI.toFixed(2)} dB\n\n`;
    }
  }

  const validImpedanceReal = finiteSeriesValues(impedanceReal);
  if (validImpedanceReal.length > 0) {
    const avgZ = validImpedanceReal.reduce((a, b) => a + b, 0) / validImpedanceReal.length;
    report += 'IMPEDANCE SUMMARY\n';
    report += '-----------------\n';
    report += `Average Real Part Z/(rho*c): ${avgZ.toFixed(2)}\n\n`;
  }

  report += '\n\nDETAILED DATA\n';
  report += '=============\n\n';
  report += 'Freq(Hz)  SPL(dB)  DI(dB)  Z_Re/(rho*c)  Z_Im/(rho*c)\n';
  report += '--------  -------  ------  ------------  ------------\n';

  for (let i = 0; i < frequencies.length; i += 1) {
    report += `${frequencies[i].toString().padEnd(8)}  `;
    report += `${formatReportCell(spl[i]).padEnd(7)}  `;
    report += `${formatReportCell(di[i]).padEnd(6)}  `;
    report += `${formatReportCell(impedanceReal[i]).padEnd(9)}  `;
    report += `${formatReportCell(impedanceImaginary[i])}\n`;
  }

  return createGenerationDownloadFile('txt', baseName, report, {
    contentType: 'text/plain',
    typeInfo: {
      description: 'Text Report',
      accept: { 'text/plain': ['.txt'] },
    },
  });
}

function buildPolarCsvFile(panel, { baseName } = {}) {
  const results = panel.lastResults;
  if (!results) {
    throw new Error('No simulation results available.');
  }

  const frequencies = results.spl_on_axis?.frequencies || [];
  const directivity = normalizeDirectivityByPlane(results.directivity);
  let csv = 'Frequency_Hz,Plane,Theta_deg,SPL_norm_dB\n';

  for (const plane of Object.keys(directivity)) {
    const patterns = directivity[plane];
    for (let fi = 0; fi < patterns.length; fi += 1) {
      const freq = frequencies.length > 0 ? frequencies[Math.min(fi, frequencies.length - 1)] : '';
      const pattern = patterns[fi];
      if (!Array.isArray(pattern)) {
        continue;
      }
      for (const sample of pattern) {
        if (!Array.isArray(sample) || sample.length < 2) {
          continue;
        }
        const angle = Number(sample[0]);
        if (!Number.isFinite(angle)) {
          continue;
        }
        const db = sample[1];
        csv += `${freq},${plane},${angle},${formatPolarDbCsvValue(db)}\n`;
      }
    }
  }

  return createGenerationDownloadFile('polar_csv', baseName, csv, {
    contentType: 'text/csv',
    typeInfo: { description: 'CSV File', accept: { 'text/csv': ['.csv'] } },
  });
}

function buildVacSpectrumFile(panel, { baseName } = {}) {
  const results = panel.lastResults;
  if (!results) {
    throw new Error('No simulation results available.');
  }

  const now = new Date();
  const dateStr = `${now.getMonth() + 1}/${now.getDate()}/${now.getFullYear()} ${now.toLocaleTimeString('en-US')}`;
  const frequencies = results.spl_on_axis?.frequencies || [];
  const impedanceData = readNormalizedImpedanceSeries(results, frequencies);
  const impFreqs = impedanceData.frequencies;
  const impReal = impedanceData.real;
  const impImag = impedanceData.imaginary;
  const directivityByPlane = normalizeDirectivityByPlane(results.directivity);
  const vacPlane = resolveVacReferencePlane(directivityByPlane);
  const vacPatterns = vacPlane ? directivityByPlane[vacPlane] : [];
  const vacPlaneLabel = resolveVacPlaneLabel(vacPlane);
  const vacPlaneSuffix = resolveVacPlaneSuffix(vacPlane);

  let out = '';
  out += '// ************************************************************\n';
  out += '//\n';
  out += `// Waveguide Generator   Spectrum Data  ${dateStr}\n`;
  out += '//\n';
  out += '// ************************************************************\n';
  out += ' \n';
  out += 'SourceDesc=VACS_Data_Text\n';
  out += 'Version=1.1.0\n';
  out += 'Author="Waveguide Generator"\n';
  out += 'SourceDesc="BEM Solver"\n';
  out += 'IsInterface=true\n';
  out += 'Command=NoInheritance\n';
  out += 'StartString_Absc=Abscissa\n';
  out += 'EndString_Absc=Abscissa_End\n';
  out += 'StartString_Data=Data\n';
  out += 'EndString_Data=Data_End\n';
  out += ' \n';

  if (impReal.length > 0) {
    out += '// ------------------------------------------------------------\n';
    out += ' \n';
    out += 'Data_Format=Complex\n';
    out += 'Data_LevelType=Impedance10\n';
    out += 'Data_Domain=Frequency\n';
    out += 'Data_AbscUnit=Hz\n';
    out += 'Data_BaseUnit=\n';
    out += 'Data_IsContPhase=false\n';
    out += 'Data_Legend="Radiation Impedance Z/(rho*c)"\n';
    out += 'Param_Drv=1001\n';
    out += 'Param_Param=1001\n';
    out += 'Graph_Caption="RadImp"\n';
    out += 'Graph_Type=Cartesian\n';
    out += 'Graph_New=true\n';
    out += 'Graph_BodeType=Complex\n';
    out += 'Graph_zAxis_Range="0, 2"\n';
    out += 'Graph_zAxis_Units=\n';
    out += 'Graph_Param_AsYAxis=Param\n';
    out += 'Param_Identifier="WG_RadImp"\n';
    out += 'Graph_Group="Waveguide Generator"\n';
    out += 'Data\n';

    for (let i = 0; i < impFreqs.length; i += 1) {
      const f = impFreqs[i];
      const re = impReal[i] ?? 0;
      const im = impImag[i] ?? 0;
      if (f == null || !Number.isFinite(f)) {
        continue;
      }
      out += `${f}   ${re} ${im}\n`;
    }
    out += 'Data_End\n';
    out += ' \n';
  }

  if (vacPatterns.length > 0) {
    let angles = null;
    for (const pat of vacPatterns) {
      if (pat && Array.isArray(pat) && pat.length > 0 && pat[0][1] != null) {
        angles = pat.map((entry) => entry[0]);
        break;
      }
    }

    if (angles) {
      const paramIndices = angles.map((_, i) => i + 1).join(',');
      out += '// ------------------------------------------------------------\n';
      out += ' \n';
      out += 'Data_Format=Complex\n';
      out += 'Data_LevelType=Peak\n';
      out += 'Data_Domain=Frequency\n';
      out += 'Data_AbscUnit=Hz\n';
      out += 'Data_BaseUnit=\n';
      out += 'Data_IsContPhase=false\n';
      out += `Data_Legend="Polar, Pressure, ${vacPlaneLabel} (far-field)"\n`;
      out += 'Param_Coord_Type=Spherical\n';
      out += 'Param_Coord_x1=1\n';
      out += `Param_Coord_x2=${angles.join(',')}\n`;
      out += 'Param_Coord_x3=0\n';
      out += `Param_Param=${paramIndices}\n`;
      out += 'Graph_Caption="PM_SPL"\n';
      out += 'Graph_Type=Contour\n';
      out += 'Graph_New=true\n';
      out += 'Graph_BodeType=LeveldB\n';
      out += 'Graph_zAxis_Range="-45, 5"\n';
      out += 'Graph_zAxis_Units=\n';
      out += 'Graph_Param_AsYAxis=x2\n';
      out += `Param_Identifier="WG_Polar_${vacPlaneSuffix}"\n`;
      out += 'Graph_Group="Waveguide Generator"\n';
      out += 'Data\n';

      for (let fi = 0; fi < vacPatterns.length; fi += 1) {
        const freq = frequencies[Math.min(fi, frequencies.length - 1)];
        if (freq == null || !Number.isFinite(freq)) {
          continue;
        }

        const pattern = vacPatterns[fi];
        if (!pattern || !Array.isArray(pattern)) {
          continue;
        }

        let row = `${freq}`;
        for (const [, db] of pattern) {
          if (db == null || !Number.isFinite(db)) {
            row += '   0 0';
          } else {
            const mag = Math.pow(10, db / 20);
            row += `   ${mag} 0`;
          }
        }
        out += `${row}\n`;
      }
      out += 'Data_End\n';
      out += ' \n';
    }
  }

  return createGenerationDownloadFile('vacs', baseName, out, {
    contentType: 'text/plain',
    typeInfo: {
      description: 'Spectrum Text',
      accept: { 'text/plain': ['.txt'] },
    },
  });
}

function buildImpedanceCsvFile(panel, { baseName } = {}) {
  const results = panel.lastResults;
  if (!results) {
    throw new Error('No simulation results available.');
  }

  const impedanceData = readNormalizedImpedanceSeries(results);
  const freqs = impedanceData.frequencies;
  const real = impedanceData.real;
  const imag = impedanceData.imaginary;
  let csv = 'Freq_Hz,Z_Real_Z_over_rho_c,Z_Imag_Z_over_rho_c\n';

  for (let i = 0; i < freqs.length; i += 1) {
    csv += `${freqs[i]},${real[i] ?? ''},${imag[i] ?? ''}\n`;
  }

  return createGenerationDownloadFile('impedance_csv', baseName, csv, {
    contentType: 'text/csv',
    typeInfo: { description: 'CSV File', accept: { 'text/csv': ['.csv'] } },
  });
}

async function runExportFormat(panel, formatId, options = {}) {
  const baseName = options.baseName || resolveExportBaseName(options.job);

  switch (formatId) {
    case 'mwg_config':
      return writeExportFiles(
        normalizeGenerationExportFiles(
          buildMwgConfigExportFiles(readExportState(), { baseName }),
          'mwg_config',
          baseName
        ),
        options
      );
    case 'step':
      return writeExportFiles(
        normalizeGenerationExportFiles(
          await buildStepExportFiles(readExportState(), {
            baseName,
            backendUrl: DEFAULT_BACKEND_URL,
          }),
          'step',
          baseName
        ),
        options
      );
    case 'png':
      return writeExportFiles(await buildMatplotlibPngFiles(panel, { baseName }), options);
    case 'csv':
      return writeExportFiles([buildCsvFile(panel, { baseName })], options);
    case 'json':
      return writeExportFiles([buildJsonFile(panel, { baseName })], options);
    case 'txt':
      return writeExportFiles([buildTextFile(panel, { baseName })], options);
    case 'polar_csv':
      return writeExportFiles([buildPolarCsvFile(panel, { baseName })], options);
    case 'impedance_csv':
      return writeExportFiles([buildImpedanceCsvFile(panel, { baseName })], options);
    case 'vacs':
      return writeExportFiles([buildVacSpectrumFile(panel, { baseName })], options);
    case 'stl':
      return writeExportFiles(
        normalizeGenerationExportFiles(
          buildStlExportFiles(readExportState(), { baseName }),
          'stl',
          baseName
        ),
        options
      );
    case 'fusion_csv': {
      const files = buildProfileCsvExportFiles(null, {
        state: readExportState(),
        baseName,
      });
      return writeExportFiles(
        normalizeGenerationExportFiles(files, 'fusion_csv', baseName),
        options
      );
    }
    default:
      throw new Error(`Unsupported export format: ${formatId}`);
  }
}

function formatBundleMessage({ exportedFiles, failures, selectedFormats, auto = false }) {
  const exportedCount = exportedFiles.length;
  const formatCount = selectedFormats.length;
  const exportedSummary =
    exportedCount > 0
      ? `Exported ${exportedCount} file${exportedCount === 1 ? '' : 's'} across ${formatCount} format${formatCount === 1 ? '' : 's'}.`
      : 'No files were exported.';

  if (failures.length === 0) {
    return exportedSummary;
  }

  const failureSummary = failures
    .map(({ formatId, message }) => `${EXPORT_FORMAT_LABELS[formatId] || formatId}: ${message}`)
    .join(' | ');

  return auto
    ? `${exportedSummary} Some formats failed: ${failureSummary}`
    : `${exportedSummary} Failed formats: ${failureSummary}`;
}

function createTaskExportWriter(job, baseName) {
  const workspaceSubdir = resolveExportDirectoryName(job, baseName);
  return async (file) => {
    const result = await writeSimulationTaskBundleFile(job, file, {
      dirName: workspaceSubdir,
      fallbackWrite: async (nextFile) => {
        await saveFile(nextFile.content, nextFile.fileName, {
          ...nextFile.saveOptions,
          workspaceSubdir,
          incrementCounter: false,
        });
      },
    });
    return result.fileName;
  };
}

export async function exportResults(
  panel,
  { job = null, auto = false, selectedFormats = null } = {}
) {
  const normalizedFormats = normalizeSelectedFormats(selectedFormats ?? getSelectedExportFormats());
  if (normalizedFormats.length === 0) {
    showError('Select at least one export format in Export Settings.');
    return {
      exportedFiles: [],
      failures: [],
      selectedFormats: [],
    };
  }

  const exportedFiles = [];
  const failures = [];
  const baseName = resolveExportBaseName(job);
  const writer = createTaskExportWriter(job, baseName);

  for (const formatId of normalizedFormats) {
    if (RESULT_EXPORT_FORMAT_IDS.includes(formatId) && !panel.lastResults) {
      failures.push({
        formatId,
        message: 'No simulation results available.',
      });
      continue;
    }

    try {
      const savedFiles = await runExportFormat(panel, formatId, {
        job,
        writer,
        baseName,
      });
      exportedFiles.push(...savedFiles.map((fileName) => `${formatId}:${fileName}`));
    } catch (error) {
      failures.push({
        formatId,
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }

  const message = formatBundleMessage({
    exportedFiles,
    failures,
    selectedFormats: normalizedFormats,
    auto,
  });

  if (failures.length > 0 && exportedFiles.length > 0) {
    showMessage(message, { type: 'warning', duration: auto ? 4200 : 5200 });
  } else if (failures.length > 0) {
    showError(message);
  } else {
    showMessage(message, { type: 'success', duration: auto ? 2600 : 3200 });
  }

  return {
    exportedFiles,
    failures,
    selectedFormats: normalizedFormats,
  };
}

export function applyExportSelection(panel, exportType, handlers = null) {
  const actionMap = handlers || {
    1: () => exportAsMatplotlibPNG(panel),
    2: () => exportAsCSV(panel),
    3: () => exportAsJSON(panel),
    4: () => exportAsText(panel),
    5: () => exportAsPolarCSV(panel),
    6: () => exportAsImpedanceCSV(panel),
    7: () => exportAsVACSSpectrum(panel),
    8: () => exportAsWaveguideSTL(panel),
    9: () => exportAsFusionCurvesCSV(panel),
  };

  const action = actionMap[exportType];
  if (!action) {
    showError('Invalid export selection.');
    return false;
  }

  void action();
  return true;
}

export async function exportAsMatplotlibPNG(panel, options = {}) {
  const baseName = options.baseName || resolveExportBaseName(options.job);
  return writeExportFiles(
    await buildMatplotlibPngFiles(panel, {
      baseName,
    }),
    { ...options, baseName }
  );
}

export async function exportAsCSV(panel, options = {}) {
  const baseName = options.baseName || resolveExportBaseName(options.job);
  return writeExportFiles(
    [
      buildCsvFile(panel, {
        baseName,
      }),
    ],
    { ...options, baseName }
  );
}

export async function exportAsJSON(panel, options = {}) {
  const baseName = options.baseName || resolveExportBaseName(options.job);
  return writeExportFiles(
    [
      buildJsonFile(panel, {
        baseName,
      }),
    ],
    { ...options, baseName }
  );
}

export async function exportAsText(panel, options = {}) {
  const baseName = options.baseName || resolveExportBaseName(options.job);
  return writeExportFiles(
    [
      buildTextFile(panel, {
        baseName,
      }),
    ],
    { ...options, baseName }
  );
}

export async function exportAsPolarCSV(panel, options = {}) {
  const baseName = options.baseName || resolveExportBaseName(options.job);
  return writeExportFiles(
    [
      buildPolarCsvFile(panel, {
        baseName,
      }),
    ],
    { ...options, baseName }
  );
}

export async function exportAsVACSSpectrum(panel, options = {}) {
  const baseName = options.baseName || resolveExportBaseName(options.job);
  return writeExportFiles(
    [
      buildVacSpectrumFile(panel, {
        baseName,
      }),
    ],
    { ...options, baseName }
  );
}

export async function exportAsImpedanceCSV(panel, options = {}) {
  const baseName = options.baseName || resolveExportBaseName(options.job);
  return writeExportFiles(
    [
      buildImpedanceCsvFile(panel, {
        baseName,
      }),
    ],
    { ...options, baseName }
  );
}

export async function exportAsWaveguideSTL(panel, options = {}) {
  const baseName = options.baseName || resolveExportBaseName(options.job);
  return writeExportFiles(
    normalizeGenerationExportFiles(
      buildStlExportFiles(readExportState(), { baseName }),
      'stl',
      baseName
    ),
    { ...options, baseName }
  );
}

export async function exportAsFusionCurvesCSV(panel, options = {}) {
  const baseName = options.baseName || resolveExportBaseName(options.job);
  const files = buildProfileCsvExportFiles(null, {
    state: readExportState(),
    baseName,
  });
  return writeExportFiles(normalizeGenerationExportFiles(files, 'fusion_csv', baseName), {
    ...options,
    baseName,
  });
}
