import { applySmoothing } from '../../results/smoothing.js';
import { chooseExportFormat, showError, showMessage } from '../feedback.js';

export function applyExportSelection(panel, exportType, handlers = null) {
  const actionMap = handlers || {
    '1': () => exportAsImage(),
    '2': () => exportAsCSV(panel),
    '3': () => exportAsJSON(panel),
    '4': () => exportAsText(panel)
  };

  const action = actionMap[exportType];
  if (!action) {
    showError('Invalid export selection.');
    return false;
  }

  action();
  return true;
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
 * Export results as PNG image
 */
export function exportAsImage() {
  const resultsCharts = document.getElementById('results-charts');
  if (!resultsCharts) {
    showError('No charts to export.');
    return;
  }

  // Use html2canvas or similar library would be ideal, but for now use SVG export
  const svgs = resultsCharts.querySelectorAll('svg');
  if (svgs.length === 0) {
    showError('No charts available to export.');
    return;
  }

  // Create a canvas to combine all SVGs
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');

  // Set canvas size (approximate)
  canvas.width = 1200;
  canvas.height = 400 * svgs.length;

  // White background
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  showMessage('PNG export is not available yet. Exporting the first chart as SVG instead.', {
    type: 'info',
    duration: 3600
  });

  // Export first SVG as example
  const svgData = new XMLSerializer().serializeToString(svgs[0]);
  const blob = new Blob([svgData], { type: 'image/svg+xml' });
  const url = URL.createObjectURL(blob);

  const link = document.createElement('a');
  link.href = url;
  link.download = `bem_results_${Date.now()}.svg`;
  link.click();

  URL.revokeObjectURL(url);
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

  for (let i = 0; i < Math.min(frequencies.length, 50); i++) {
    report += `${frequencies[i].toString().padEnd(8)}  `;
    report += `${(splValues[i] || 0).toFixed(2).padEnd(7)}  `;
    report += `${((diData.di && diData.di[i]) || 0).toFixed(2).padEnd(6)}  `;
    report += `${((impedanceData.real && impedanceData.real[i]) || 0).toFixed(2).padEnd(9)}  `;
    report += `${((impedanceData.imaginary && impedanceData.imaginary[i]) || 0).toFixed(2)}\n`;
  }

  if (frequencies.length > 50) {
    report += `\n... (${frequencies.length - 50} more rows) ...\n`;
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
