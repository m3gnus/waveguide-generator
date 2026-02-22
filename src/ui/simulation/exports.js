import { applySmoothing } from '../../results/smoothing.js';
import { chooseExportFormat, showError, showMessage } from '../feedback.js';

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

  action();
  return true;
}

function resolveApp(panel) {
  return panel?.app || window?.__waveguideApp || null;
}

export function exportAsWaveguideSTL(panel) {
  const app = resolveApp(panel);
  if (!app || typeof app.exportSTL !== 'function') {
    showError('STL export is unavailable right now.');
    return;
  }
  app.exportSTL();
}

export function exportAsFusionCurvesCSV(panel) {
  const app = resolveApp(panel);
  if (!app || typeof app.exportProfileCSV !== 'function') {
    showError('CSV profile export is unavailable right now.');
    return;
  }
  app.exportProfileCSV();
}

export async function exportResults(panel) {
  if (!panel.lastResults) {
    showError('No simulation results available to export.');
    return;
  }

  const exportType = await chooseExportFormat();
  if (!exportType) {
    return;
  }

  applyExportSelection(panel, exportType);
}

/**
 * Export all result charts as PNG images via Matplotlib backend rendering.
 */
export async function exportAsMatplotlibPNG(panel) {
  const results = panel.lastResults;
  if (!results) {
    showError('No simulation results available.');
    return;
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
    const { applySmoothing } = await import('../../results/smoothing.js');
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
    directivity,
  };

  try {
    const response = await fetch('http://localhost:8000/api/render-charts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const detail = await response.text().catch(() => '');
      if (response.status === 503) {
        showError('Matplotlib is not installed on the backend. Install it with: pip install matplotlib');
      } else {
        showError(`Chart rendering failed: ${detail || response.status}`);
      }
      return;
    }

    const data = await response.json();
    const charts = data.charts || {};
    const chartKeys = Object.keys(charts);

    if (chartKeys.length === 0) {
      showError('No charts were rendered by the backend.');
      return;
    }

    // Download each chart as a separate PNG
    for (const key of chartKeys) {
      const b64 = charts[key];
      if (!b64) continue;
      const byteString = atob(b64.split(',')[1]);
      const ab = new ArrayBuffer(byteString.length);
      const ia = new Uint8Array(ab);
      for (let i = 0; i < byteString.length; i++) {
        ia[i] = byteString.charCodeAt(i);
      }
      const blob = new Blob([ab], { type: 'image/png' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `bem_${key}_${Date.now()}.png`;
      link.click();
      URL.revokeObjectURL(url);
    }

    showMessage(`Exported ${chartKeys.length} chart(s) as PNG.`, { type: 'info', duration: 3000 });
  } catch (err) {
    showError('Chart export failed. Ensure the backend server is running with Matplotlib installed.');
  }
}

/**
 * Export results as CSV
 */
export function exportAsCSV(panel) {
  const results = panel.lastResults;
  const splData = results.spl_on_axis || {};
  const frequencies = splData.frequencies || [];
  const splValues = splData.spl || [];
  const diData = results.di || {};
  const impedanceData = results.impedance || {};

  // Apply current smoothing
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

  // Build CSV content
  let csv = 'Frequency (Hz),SPL (dB),DI (dB),Impedance Real (Ω),Impedance Imag (Ω)\n';

  for (let i = 0; i < frequencies.length; i++) {
    csv += `${frequencies[i]},${smoothedSPL[i] || ''},${smoothedDI[i] || ''},${smoothedImpReal[i] || ''},${smoothedImpImag[i] || ''}\n`;
  }

  // Add smoothing info as comment
  if (panel.currentSmoothing !== 'none') {
    csv = `# Smoothing: ${panel.currentSmoothing}\n` + csv;
  }

  // Download
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `bem_results_${Date.now()}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

/**
 * Export results as JSON
 */
export function exportAsJSON(panel) {
  // Use local time format to match system clock
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  const hour = String(now.getHours()).padStart(2, '0');
  const minute = String(now.getMinutes()).padStart(2, '0');
  const second = String(now.getSeconds()).padStart(2, '0');
  
  const exportData = {
    timestamp: `${year}-${month}-${day} ${hour}:${minute}:${second}`,
    smoothing: panel.currentSmoothing,
    results: panel.lastResults
  };

  const json = JSON.stringify(exportData, null, 2);
  const blob = new Blob([json], { type: 'application/json' });
  const url = URL.createObjectURL(blob);

  const link = document.createElement('a');
  link.href = url;
  link.download = `bem_results_${Date.now()}.json`;
  link.click();

  URL.revokeObjectURL(url);
}

/**
 * Export results as text report
 */
export function exportAsText(panel) {
  // Use local time format to match system clock
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  const hour = String(now.getHours()).padStart(2, '0');
  const minute = String(now.getMinutes()).padStart(2, '0');
  const second = String(now.getSeconds()).padStart(2, '0');

  const results = panel.lastResults;
  const splData = results.spl_on_axis || {};
  const frequencies = splData.frequencies || [];
  const splValues = splData.spl || [];
  const diData = results.di || {};
  const impedanceData = results.impedance || {};

  let report = 'BEM SIMULATION RESULTS\n';
  report += '=====================\n\n';
  report += `Generated: ${year}-${month}-${day} ${hour}:${minute}:${second}\n`;
  report += `Smoothing: ${panel.currentSmoothing}\n`;
  report += `Frequency range: ${Math.min(...frequencies).toFixed(0)} - ${Math.max(...frequencies).toFixed(0)} Hz\n`;
  report += `Number of points: ${frequencies.length}\n\n`;

  // Summary statistics
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

  for (let i = 0; i < frequencies.length; i++) {
    report += `${frequencies[i].toString().padEnd(8)}  `;
    report += `${(splValues[i] || 0).toFixed(2).padEnd(7)}  `;
    report += `${((diData.di && diData.di[i]) || 0).toFixed(2).padEnd(6)}  `;
    report += `${((impedanceData.real && impedanceData.real[i]) || 0).toFixed(2).padEnd(9)}  `;
    report += `${((impedanceData.imaginary && impedanceData.imaginary[i]) || 0).toFixed(2)}\n`;
  }

  // Download
  const blob = new Blob([report], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `bem_report_${Date.now()}.txt`;
  link.click();
  URL.revokeObjectURL(url);
}

/**
 * Export polar directivity data as CSV
 * Format: Frequency_Hz, Plane, Theta_deg, SPL_norm_dB
 */
export function exportAsPolarCSV(panel) {
  const results = panel.lastResults;
  if (!results) {
    showError('No simulation results available.');
    return;
  }

  const frequencies = results.spl_on_axis?.frequencies || [];
  const directivity = results.directivity || {};

  let csv = 'Frequency_Hz,Plane,Theta_deg,SPL_norm_dB\n';

  for (const plane of ['horizontal', 'vertical', 'diagonal']) {
    const patterns = directivity[plane] || [];
    for (let fi = 0; fi < patterns.length; fi++) {
      const freq = frequencies[Math.min(fi, frequencies.length - 1)];
      for (const [angle, db] of patterns[fi]) {
        csv += `${freq},${plane},${angle},${db.toFixed(2)}\n`;
      }
    }
  }

  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `polar_directivity_${Date.now()}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

/**
 * Export impedance data as CSV
 * Format: Freq_Hz, Z_Real, Z_Imag (matches reference impedance_curve.csv)
 */
/**
 * Export results in VACS Data Text format (ABEC Spectrum).
 * Compatible with VituixCAD and other ABEC-aware tools.
 * Produces two data blocks:
 *   1. Radiation impedance (complex)
 *   2. Horizontal polar directivity (complex pressure, magnitude-only from normalized dB)
 */
export function exportAsVACSSpectrum(panel) {
  const results = panel.lastResults;
  if (!results) {
    showError('No simulation results available.');
    return;
  }

  const now = new Date();
  const dateStr = `${now.getMonth() + 1}/${now.getDate()}/${now.getFullYear()} `
    + now.toLocaleTimeString('en-US');

  const frequencies = results.spl_on_axis?.frequencies || [];
  const impedanceData = results.impedance || {};
  const impFreqs = impedanceData.frequencies || frequencies;
  const impReal = impedanceData.real || [];
  const impImag = impedanceData.imaginary || [];
  const directivity = results.directivity || {};
  const hPatterns = directivity.horizontal || [];

  // --- File header ---
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

  // --- Block 1: Radiation impedance ---
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

    for (let i = 0; i < impFreqs.length; i++) {
      const f = impFreqs[i];
      const re = impReal[i] ?? 0;
      const im = impImag[i] ?? 0;
      if (f == null || !Number.isFinite(f)) continue;
      out += `${f}   ${re} ${im}\n`;
    }
    out += 'Data_End\n';
    out += ' \n';
  }

  // --- Block 2: Horizontal polar directivity ---
  if (hPatterns.length > 0) {
    // Extract angle list from first valid pattern
    let angles = null;
    for (const pat of hPatterns) {
      if (pat && Array.isArray(pat) && pat.length > 0 && pat[0][1] != null) {
        angles = pat.map(p => p[0]);
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

      for (let fi = 0; fi < hPatterns.length; fi++) {
        const freq = frequencies[Math.min(fi, frequencies.length - 1)];
        if (freq == null || !Number.isFinite(freq)) continue;

        const pattern = hPatterns[fi];
        if (!pattern || !Array.isArray(pattern)) continue;

        let row = `${freq}`;
        for (const [, db] of pattern) {
          // Convert normalized dB to complex magnitude (phase=0 since we lack phase data)
          if (db == null || !Number.isFinite(db)) {
            row += '   0 0';
          } else {
            const mag = Math.pow(10, db / 20);
            row += `   ${mag} 0`;
          }
        }
        out += row + '\n';
      }
      out += 'Data_End\n';
      out += ' \n';
    }
  }

  // Download
  const blob = new Blob([out], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `Spectrum_WG_${Date.now()}.txt`;
  link.click();
  URL.revokeObjectURL(url);
}

export function exportAsImpedanceCSV(panel) {
  const results = panel.lastResults;
  if (!results) {
    showError('No simulation results available.');
    return;
  }

  const freqs = results.impedance?.frequencies || [];
  const real = results.impedance?.real || [];
  const imag = results.impedance?.imaginary || [];

  let csv = 'Freq_Hz,Z_Real,Z_Imag\n';
  for (let i = 0; i < freqs.length; i++) {
    csv += `${freqs[i]},${real[i] ?? ''},${imag[i] ?? ''}\n`;
  }

  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `impedance_curve_${Date.now()}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}
