import { applySmoothing } from '../../results/smoothing.js';
import { DEFAULT_BACKEND_URL } from '../../config/backendUrl.js';
import {
  buildProfileCsvExportFiles,
  buildStlExportFiles
} from '../../modules/export/useCases.js';
import {
  readSimulationState
} from '../../modules/simulation/state.js';
import { writeSimulationTaskBundleFile } from './workspaceTasks.js';
import {
  SIMULATION_EXPORT_FORMAT_IDS,
  getSelectedExportFormats
} from '../settings/simulationManagementSettings.js';
import { showError, showMessage } from '../feedback.js';
import { getExportBaseName, saveFile } from '../fileOps.js';
import { resolveGenerationExportFileName } from '../workspace/generationArtifacts.js';

const EXPORT_FORMAT_LABELS = Object.freeze({
  png: 'Chart Images (PNG)',
  csv: 'Frequency Data CSV',
  json: 'Full Results JSON',
  txt: 'Summary Text Report',
  polar_csv: 'Polar Directivity CSV',
  impedance_csv: 'Impedance CSV',
  vacs: 'ABEC Spectrum (VACS)',
  stl: 'Waveguide STL',
  fusion_csv: 'Fusion 360 CSV Curves'
});

function resolveApp(panel) {
  return panel?.app || null;
}

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

function readExportState() {
  return readSimulationState();
}

async function writeExportFile(file, { writer = null } = {}) {
  if (typeof writer === 'function') {
    return writer(file);
  }

  await saveFile(file.content, file.fileName, {
    ...file.saveOptions,
    incrementCounter: false
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
    saveOptions
  };
}

function createGenerationDownloadFile(formatId, baseName, content, saveOptions = {}, options = {}) {
  const fileName = resolveGenerationExportFileName(formatId, {
    baseName,
    chartKey: options.chartKey,
    originalFileName: options.originalFileName
  });
  return createDownloadFile(fileName, content, saveOptions);
}

function normalizeGenerationExportFiles(files = [], formatId, baseName) {
  return (files || []).map((file) => ({
    ...file,
    fileName: resolveGenerationExportFileName(formatId, {
      baseName,
      originalFileName: file?.fileName
    })
  }));
}

function localTimestampParts() {
  const now = new Date();
  return {
    now,
    dateStamp: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`,
    timeStamp: `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`
  };
}

async function buildMatplotlibPngFiles(panel, { baseName } = {}) {
  const results = panel.lastResults;
  if (!results) {
    throw new Error('No simulation results available.');
  }

  const splData = results.spl_on_axis || {};
  const frequencies = splData.frequencies || [];
  let spl = splData.spl || [];
  const diData = results.di || {};
  let di = diData.di || [];
  const diFrequencies = diData.frequencies || frequencies;
  const impedanceData = results.impedance || {};
  const impedanceFrequencies = impedanceData.frequencies || frequencies;
  let impedanceReal = impedanceData.real || [];
  let impedanceImag = impedanceData.imaginary || [];
  const directivity = results.directivity || {};

  if (panel.currentSmoothing !== 'none') {
    spl = applySmoothing(frequencies, spl, panel.currentSmoothing);
    di = applySmoothing(diFrequencies, di, panel.currentSmoothing);
    impedanceReal = applySmoothing(impedanceFrequencies, impedanceReal, panel.currentSmoothing);
    impedanceImag = applySmoothing(impedanceFrequencies, impedanceImag, panel.currentSmoothing);
  }

  const payload = {
    frequencies,
    spl,
    di,
    di_frequencies: diFrequencies,
    impedance_frequencies: impedanceFrequencies,
    impedance_real: impedanceReal,
    impedance_imaginary: impedanceImag,
    directivity
  };

  const backendUrl = panel?.solver?.backendUrl || DEFAULT_BACKEND_URL;
  const response = await fetch(`${backendUrl}/api/render-charts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    if (response.status === 503) {
      throw new Error('Matplotlib is not installed on the backend. Install it with: pip install matplotlib');
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
        typeInfo: { description: 'PNG Image', accept: { 'image/png': ['.png'] } }
      },
      { chartKey: key }
    );
  });
}

function buildCsvFile(panel, { baseName } = {}) {
  const results = panel.lastResults;
  if (!results) {
    throw new Error('No simulation results available.');
  }

  const splData = results.spl_on_axis || {};
  const frequencies = splData.frequencies || [];
  const splValues = splData.spl || [];
  const diData = results.di || {};
  const impedanceData = results.impedance || {};

  let smoothedSPL = splValues;
  let smoothedDI = diData.di || [];
  let smoothedImpReal = impedanceData.real || [];
  let smoothedImpImag = impedanceData.imaginary || [];

  if (panel.currentSmoothing !== 'none') {
    smoothedSPL = applySmoothing(frequencies, splValues, panel.currentSmoothing);
    smoothedDI = applySmoothing(frequencies, smoothedDI, panel.currentSmoothing);
    smoothedImpReal = applySmoothing(frequencies, smoothedImpReal, panel.currentSmoothing);
    smoothedImpImag = applySmoothing(frequencies, smoothedImpImag, panel.currentSmoothing);
  }

  let csv = 'Frequency (Hz),SPL (dB),DI (dB),Impedance Real (Ω),Impedance Imag (Ω)\n';
  for (let i = 0; i < frequencies.length; i += 1) {
    csv += `${frequencies[i]},${smoothedSPL[i] || ''},${smoothedDI[i] || ''},${smoothedImpReal[i] || ''},${smoothedImpImag[i] || ''}\n`;
  }

  if (panel.currentSmoothing !== 'none') {
    csv = `# Smoothing: ${panel.currentSmoothing}\n${csv}`;
  }

  return createGenerationDownloadFile('csv', baseName, csv, {
    contentType: 'text/csv',
    typeInfo: { description: 'CSV File', accept: { 'text/csv': ['.csv'] } }
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
    results: panel.lastResults
  };

  return createGenerationDownloadFile('json', baseName, JSON.stringify(exportData, null, 2), {
    contentType: 'application/json',
    typeInfo: { description: 'JSON File', accept: { 'application/json': ['.json'] } }
  });
}

function buildTextFile(panel, { baseName } = {}) {
  const results = panel.lastResults;
  if (!results) {
    throw new Error('No simulation results available.');
  }

  const { dateStamp, timeStamp } = localTimestampParts();
  const frequencies = results.spl_on_axis?.frequencies || [];
  const splValues = results.spl_on_axis?.spl || [];
  const diData = results.di || {};
  const impedanceData = results.impedance || {};

  let report = 'BEM SIMULATION RESULTS\n';
  report += '=====================\n\n';
  report += `Generated: ${dateStamp} ${timeStamp}\n`;
  report += `Smoothing: ${panel.currentSmoothing}\n`;
  report += `Frequency range: ${Math.min(...frequencies).toFixed(0)} - ${Math.max(...frequencies).toFixed(0)} Hz\n`;
  report += `Number of points: ${frequencies.length}\n\n`;

  if (splValues.length > 0) {
    const avgSPL = splValues.reduce((a, b) => a + b, 0) / splValues.length;
    const minSPL = Math.min(...splValues);
    const maxSPL = Math.max(...splValues);

    report += 'FREQUENCY RESPONSE SUMMARY\n';
    report += '--------------------------\n';
    report += `Average SPL: ${avgSPL.toFixed(2)} dB\n`;
    report += `SPL Range: ${minSPL.toFixed(2)} to ${maxSPL.toFixed(2)} dB\n`;
    report += `Variation: ${(maxSPL - minSPL).toFixed(2)} dB\n\n`;
  }

  if (diData.di && diData.di.length > 0) {
    const avgDI = diData.di.reduce((a, b) => a + b, 0) / diData.di.length;
    const minDI = Math.min(...diData.di);
    const maxDI = Math.max(...diData.di);

    report += 'DIRECTIVITY INDEX SUMMARY\n';
    report += '-------------------------\n';
    report += `Average DI: ${avgDI.toFixed(2)} dB\n`;
    report += `DI Range: ${minDI.toFixed(2)} to ${maxDI.toFixed(2)} dB\n\n`;
  }

  if (impedanceData.real && impedanceData.real.length > 0) {
    const avgZ = impedanceData.real.reduce((a, b) => a + b, 0) / impedanceData.real.length;
    report += 'IMPEDANCE SUMMARY\n';
    report += '-----------------\n';
    report += `Average Real Part: ${avgZ.toFixed(2)} Ω\n\n`;
  }

  report += '\n\nDETAILED DATA\n';
  report += '=============\n\n';
  report += 'Freq(Hz)  SPL(dB)  DI(dB)  Z_Real(Ω)  Z_Imag(Ω)\n';
  report += '--------  -------  ------  ---------  ---------\n';

  for (let i = 0; i < frequencies.length; i += 1) {
    report += `${frequencies[i].toString().padEnd(8)}  `;
    report += `${(splValues[i] || 0).toFixed(2).padEnd(7)}  `;
    report += `${((diData.di && diData.di[i]) || 0).toFixed(2).padEnd(6)}  `;
    report += `${((impedanceData.real && impedanceData.real[i]) || 0).toFixed(2).padEnd(9)}  `;
    report += `${((impedanceData.imaginary && impedanceData.imaginary[i]) || 0).toFixed(2)}\n`;
  }

  return createGenerationDownloadFile('txt', baseName, report, {
    contentType: 'text/plain',
    typeInfo: { description: 'Text Report', accept: { 'text/plain': ['.txt'] } }
  });
}

function buildPolarCsvFile(panel, { baseName } = {}) {
  const results = panel.lastResults;
  if (!results) {
    throw new Error('No simulation results available.');
  }

  const frequencies = results.spl_on_axis?.frequencies || [];
  const directivity = results.directivity || {};
  let csv = 'Frequency_Hz,Plane,Theta_deg,SPL_norm_dB\n';

  for (const plane of ['horizontal', 'vertical', 'diagonal']) {
    const patterns = directivity[plane] || [];
    for (let fi = 0; fi < patterns.length; fi += 1) {
      const freq = frequencies[Math.min(fi, frequencies.length - 1)];
      for (const [angle, db] of patterns[fi]) {
        csv += `${freq},${plane},${angle},${db.toFixed(2)}\n`;
      }
    }
  }

  return createGenerationDownloadFile('polar_csv', baseName, csv, {
    contentType: 'text/csv',
    typeInfo: { description: 'CSV File', accept: { 'text/csv': ['.csv'] } }
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
  const impedanceData = results.impedance || {};
  const impFreqs = impedanceData.frequencies || frequencies;
  const impReal = impedanceData.real || [];
  const impImag = impedanceData.imaginary || [];
  const hPatterns = results.directivity?.horizontal || [];

  let out = '';
  out += '// ************************************************************\n';
  out += '//\n';
  out += `// Waveguide Generator   Spectrum Data  ${dateStr}\n`;
  out += '//\n';
  out += '// ************************************************************\n';
  out += ' \n';
  out += 'SourceDesc=VACS_Data_Text\n';
  out += 'Version=1.0.0\n';
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
    out += 'Data_Legend="Radiation Impedance, Normalized"\n';
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

  if (hPatterns.length > 0) {
    let angles = null;
    for (const pat of hPatterns) {
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
      out += 'Data_Legend="Polar, Pressure, Horizontal (far-field)"\n';
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
      out += 'Param_Identifier="WG_Polar_H"\n';
      out += 'Graph_Group="Waveguide Generator"\n';
      out += 'Data\n';

      for (let fi = 0; fi < hPatterns.length; fi += 1) {
        const freq = frequencies[Math.min(fi, frequencies.length - 1)];
        if (freq == null || !Number.isFinite(freq)) {
          continue;
        }

        const pattern = hPatterns[fi];
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
    typeInfo: { description: 'Spectrum Text', accept: { 'text/plain': ['.txt'] } }
  });
}

function buildImpedanceCsvFile(panel, { baseName } = {}) {
  const results = panel.lastResults;
  if (!results) {
    throw new Error('No simulation results available.');
  }

  const freqs = results.impedance?.frequencies || [];
  const real = results.impedance?.real || [];
  const imag = results.impedance?.imaginary || [];
  let csv = 'Freq_Hz,Z_Real,Z_Imag\n';

  for (let i = 0; i < freqs.length; i += 1) {
    csv += `${freqs[i]},${real[i] ?? ''},${imag[i] ?? ''}\n`;
  }

  return createGenerationDownloadFile('impedance_csv', baseName, csv, {
    contentType: 'text/csv',
    typeInfo: { description: 'CSV File', accept: { 'text/csv': ['.csv'] } }
  });
}

async function runExportFormat(panel, formatId, options = {}) {
  const baseName = options.baseName || resolveExportBaseName(options.job);

  switch (formatId) {
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
      const app = resolveApp(panel);
      const vertices = app?.hornMesh?.geometry?.attributes?.position?.array;
      const files = buildProfileCsvExportFiles(vertices, {
        state: readExportState(),
        baseName
      });
      if (!files) {
        throw new Error('Fusion CSV export requires an active viewport mesh.');
      }
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
  const exportedSummary = exportedCount > 0
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
  return async (file) => {
    const result = await writeSimulationTaskBundleFile(job, file, {
      dirName: baseName,
      fallbackWrite: async (nextFile) => {
        await saveFile(nextFile.content, nextFile.fileName, {
          ...nextFile.saveOptions,
          workspaceSubdir: baseName,
          incrementCounter: false
        });
      }
    });
    return result.fileName;
  };
}

export async function exportResults(panel, { job = null, auto = false, selectedFormats = null } = {}) {
  if (!panel.lastResults) {
    showError('No simulation results available to export.');
    return null;
  }

  const normalizedFormats = normalizeSelectedFormats(selectedFormats ?? getSelectedExportFormats());
  if (normalizedFormats.length === 0) {
    showError('Select at least one task export format in Settings.');
    return {
      exportedFiles: [],
      failures: [],
      selectedFormats: []
    };
  }

  const exportedFiles = [];
  const failures = [];
  const baseName = resolveExportBaseName(job);
  const writer = createTaskExportWriter(job, baseName);

  for (const formatId of normalizedFormats) {
    try {
      const savedFiles = await runExportFormat(panel, formatId, {
        job,
        writer,
        baseName
      });
      exportedFiles.push(...savedFiles.map((fileName) => `${formatId}:${fileName}`));
    } catch (error) {
      failures.push({
        formatId,
        message: error instanceof Error ? error.message : String(error)
      });
    }
  }

  const message = formatBundleMessage({
    exportedFiles,
    failures,
    selectedFormats: normalizedFormats,
    auto
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
    selectedFormats: normalizedFormats
  };
}

export function applyExportSelection(panel, exportType, handlers = null) {
  const actionMap = handlers || {
    '1': () => exportAsMatplotlibPNG(panel),
    '2': () => exportAsCSV(panel),
    '3': () => exportAsJSON(panel),
    '4': () => exportAsText(panel),
    '5': () => exportAsPolarCSV(panel),
    '6': () => exportAsImpedanceCSV(panel),
    '7': () => exportAsVACSSpectrum(panel),
    '8': () => exportAsWaveguideSTL(panel),
    '9': () => exportAsFusionCurvesCSV(panel)
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
  return writeExportFiles(
    await buildMatplotlibPngFiles(panel, { baseName: options.baseName || resolveExportBaseName(options.job) }),
    options
  );
}

export async function exportAsCSV(panel, options = {}) {
  return writeExportFiles(
    [buildCsvFile(panel, { baseName: options.baseName || resolveExportBaseName(options.job) })],
    options
  );
}

export async function exportAsJSON(panel, options = {}) {
  return writeExportFiles(
    [buildJsonFile(panel, { baseName: options.baseName || resolveExportBaseName(options.job) })],
    options
  );
}

export async function exportAsText(panel, options = {}) {
  return writeExportFiles(
    [buildTextFile(panel, { baseName: options.baseName || resolveExportBaseName(options.job) })],
    options
  );
}

export async function exportAsPolarCSV(panel, options = {}) {
  return writeExportFiles(
    [buildPolarCsvFile(panel, { baseName: options.baseName || resolveExportBaseName(options.job) })],
    options
  );
}

export async function exportAsVACSSpectrum(panel, options = {}) {
  return writeExportFiles(
    [buildVacSpectrumFile(panel, { baseName: options.baseName || resolveExportBaseName(options.job) })],
    options
  );
}

export async function exportAsImpedanceCSV(panel, options = {}) {
  return writeExportFiles(
    [buildImpedanceCsvFile(panel, { baseName: options.baseName || resolveExportBaseName(options.job) })],
    options
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
    options
  );
}

export async function exportAsFusionCurvesCSV(panel, options = {}) {
  const app = resolveApp(panel);
  const vertices = app?.hornMesh?.geometry?.attributes?.position?.array;
  const baseName = options.baseName || resolveExportBaseName(options.job);
  const files = buildProfileCsvExportFiles(vertices, {
    state: readExportState(),
    baseName
  });
  if (!files) {
    throw new Error('Fusion CSV export requires an active viewport mesh.');
  }
  return writeExportFiles(
    normalizeGenerationExportFiles(files, 'fusion_csv', baseName),
    options
  );
}
