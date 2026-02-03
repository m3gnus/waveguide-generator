export function renderFrequencyResponseChart(frequencies, splValues) {
  if (!frequencies.length || !splValues.length) {
    return '<p style="color: var(--text-color);">No frequency response data available</p>';
  }

  const width = 300;
  const height = 180;
  const padding = { left: 45, right: 20, top: 20, bottom: 30 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  // Calculate scales
  const minFreq = Math.min(...frequencies);
  const maxFreq = Math.max(...frequencies);
  const minSpl = Math.min(...splValues) - 5;
  const maxSpl = Math.max(...splValues) + 5;

  // Use log scale for frequency
  const logMinFreq = Math.log10(minFreq);
  const logMaxFreq = Math.log10(maxFreq);

  // Generate path points
  const points = frequencies
    .map((freq, i) => {
      const logFreq = Math.log10(freq);
      const x = padding.left + ((logFreq - logMinFreq) / (logMaxFreq - logMinFreq)) * chartWidth;
      const y = padding.top + (1 - (splValues[i] - minSpl) / (maxSpl - minSpl)) * chartHeight;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  // Generate grid lines
  const gridLines = [];
  const freqTicks = [100, 1000, 10000];
  freqTicks.forEach((freq) => {
    if (freq >= minFreq && freq <= maxFreq) {
      const logFreq = Math.log10(freq);
      const x = padding.left + ((logFreq - logMinFreq) / (logMaxFreq - logMinFreq)) * chartWidth;
      gridLines.push(
        `<line x1="${x}" y1="${padding.top}" x2="${x}" y2="${height - padding.bottom}" stroke="var(--border-color)" stroke-width="0.5" stroke-dasharray="2,2"/>`
      );
      gridLines.push(
        `<text x="${x}" y="${height - 10}" text-anchor="middle" fill="var(--text-color)" font-size="10">${freq >= 1000 ? freq / 1000 + 'k' : freq}</text>`
      );
    }
  });

  return `
            <svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">
                <!-- Axes -->
                <line x1="${padding.left}" y1="${height - padding.bottom}" x2="${width - padding.right}" y2="${height - padding.bottom}" stroke="var(--border-color)" stroke-width="1"/>
                <line x1="${padding.left}" y1="${padding.top}" x2="${padding.left}" y2="${height - padding.bottom}" stroke="var(--border-color)" stroke-width="1"/>

                <!-- Grid lines -->
                ${gridLines.join('\n')}

                <!-- Y-axis labels -->
                <text x="10" y="${padding.top + 5}" fill="var(--text-color)" font-size="9">${maxSpl.toFixed(0)}</text>
                <text x="10" y="${height - padding.bottom}" fill="var(--text-color)" font-size="9">${minSpl.toFixed(0)}</text>

                <!-- Data line -->
                <polyline points="${points}" fill="none" stroke="var(--accent-color)" stroke-width="2"/>

                <!-- Axis labels -->
                <text x="${width / 2}" y="${height - 2}" text-anchor="middle" fill="var(--text-color)" font-size="10">Frequency (Hz)</text>
                <text x="8" y="${height / 2}" text-anchor="middle" fill="var(--text-color)" font-size="10" transform="rotate(-90, 8, ${height / 2})">SPL (dB)</text>
            </svg>
        `;
}

export function renderDirectivityIndexChart(frequencies, diValues) {
  if (!frequencies.length || !diValues.length) {
    return '<p style="color: var(--text-color);">No directivity data available</p>';
  }

  const width = 300;
  const height = 180;
  const padding = { left: 45, right: 20, top: 20, bottom: 30 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  const minFreq = Math.min(...frequencies);
  const maxFreq = Math.max(...frequencies);
  const minDi = Math.min(0, Math.min(...diValues) - 2);
  const maxDi = Math.max(...diValues) + 2;

  const logMinFreq = Math.log10(minFreq);
  const logMaxFreq = Math.log10(maxFreq);

  const points = frequencies
    .map((freq, i) => {
      const logFreq = Math.log10(freq);
      const x = padding.left + ((logFreq - logMinFreq) / (logMaxFreq - logMinFreq)) * chartWidth;
      const y = padding.top + (1 - (diValues[i] - minDi) / (maxDi - minDi)) * chartHeight;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  return `
            <svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">
                <line x1="${padding.left}" y1="${height - padding.bottom}" x2="${width - padding.right}" y2="${height - padding.bottom}" stroke="var(--border-color)" stroke-width="1"/>
                <line x1="${padding.left}" y1="${padding.top}" x2="${padding.left}" y2="${height - padding.bottom}" stroke="var(--border-color)" stroke-width="1"/>

                <text x="10" y="${padding.top + 5}" fill="var(--text-color)" font-size="9">${maxDi.toFixed(0)}</text>
                <text x="10" y="${height - padding.bottom}" fill="var(--text-color)" font-size="9">${minDi.toFixed(0)}</text>

                <polyline points="${points}" fill="none" stroke="#4CAF50" stroke-width="2"/>

                <text x="${width / 2}" y="${height - 2}" text-anchor="middle" fill="var(--text-color)" font-size="10">Frequency (Hz)</text>
                <text x="8" y="${height / 2}" text-anchor="middle" fill="var(--text-color)" font-size="10" transform="rotate(-90, 8, ${height / 2})">DI (dB)</text>
            </svg>
        `;
}

export function renderImpedanceChart(frequencies, realValues, imagValues) {
  if (!frequencies.length || !realValues.length) {
    return '<p style="color: var(--text-color);">No impedance data available</p>';
  }

  const width = 300;
  const height = 180;
  const padding = { left: 45, right: 20, top: 20, bottom: 30 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  const minFreq = Math.min(...frequencies);
  const maxFreq = Math.max(...frequencies);
  const allValues = [...realValues, ...imagValues];
  const minZ = Math.min(...allValues) - 50;
  const maxZ = Math.max(...allValues) + 50;

  const logMinFreq = Math.log10(minFreq);
  const logMaxFreq = Math.log10(maxFreq);

  const realPoints = frequencies
    .map((freq, i) => {
      const logFreq = Math.log10(freq);
      const x = padding.left + ((logFreq - logMinFreq) / (logMaxFreq - logMinFreq)) * chartWidth;
      const y = padding.top + (1 - (realValues[i] - minZ) / (maxZ - minZ)) * chartHeight;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  const imagPoints = frequencies
    .map((freq, i) => {
      const logFreq = Math.log10(freq);
      const x = padding.left + ((logFreq - logMinFreq) / (logMaxFreq - logMinFreq)) * chartWidth;
      const y = padding.top + (1 - (imagValues[i] - minZ) / (maxZ - minZ)) * chartHeight;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  return `
            <svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">
                <line x1="${padding.left}" y1="${height - padding.bottom}" x2="${width - padding.right}" y2="${height - padding.bottom}" stroke="var(--border-color)" stroke-width="1"/>
                <line x1="${padding.left}" y1="${padding.top}" x2="${padding.left}" y2="${height - padding.bottom}" stroke="var(--border-color)" stroke-width="1"/>

                <text x="10" y="${padding.top + 5}" fill="var(--text-color)" font-size="9">${maxZ.toFixed(0)}</text>
                <text x="10" y="${height - padding.bottom}" fill="var(--text-color)" font-size="9">${minZ.toFixed(0)}</text>

                <!-- Real part (blue) -->
                <polyline points="${realPoints}" fill="none" stroke="#2196F3" stroke-width="2"/>
                <!-- Imaginary part (orange) -->
                <polyline points="${imagPoints}" fill="none" stroke="#FF9800" stroke-width="2"/>

                <!-- Legend -->
                <line x1="${width - 80}" y1="12" x2="${width - 65}" y2="12" stroke="#2196F3" stroke-width="2"/>
                <text x="${width - 62}" y="15" fill="var(--text-color)" font-size="8">Re(Z)</text>
                <line x1="${width - 80}" y1="24" x2="${width - 65}" y2="24" stroke="#FF9800" stroke-width="2"/>
                <text x="${width - 62}" y="27" fill="var(--text-color)" font-size="8">Im(Z)</text>

                <text x="${width / 2}" y="${height - 2}" text-anchor="middle" fill="var(--text-color)" font-size="10">Frequency (Hz)</text>
                <text x="8" y="${height / 2}" text-anchor="middle" fill="var(--text-color)" font-size="10" transform="rotate(-90, 8, ${height / 2})">Z (Ω)</text>
            </svg>
        `;
}

export function renderPolarDirectivityHeatmap(frequencies, directivityData) {
  if (!frequencies.length || !directivityData.horizontal) {
    return '<p style="color: var(--text-color);">No directivity map data available</p>';
  }

  const width = 600;
  const height = 400;
  const padding = { left: 60, right: 80, top: 40, bottom: 50 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  // Get horizontal directivity patterns (array of [angle, spl_db] pairs for each frequency)
  const patterns = directivityData.horizontal;
  if (!patterns || patterns.length === 0) {
    return '<p style="color: var(--text-color);">No polar directivity data available</p>';
  }

  // Extract angle range from first pattern
  const firstPattern = patterns[0];
  const angles = firstPattern.map((point) => point[0]);
  const minAngle = Math.min(...angles);
  const maxAngle = Math.max(...angles);

  // Use log scale for frequency
  const minFreq = Math.min(...frequencies);
  const maxFreq = Math.max(...frequencies);
  const logMinFreq = Math.log10(minFreq);
  const logMaxFreq = Math.log10(maxFreq);

  // Color scale: red (3dB) -> orange -> yellow -> green -> cyan -> blue (-30dB)
  const getColor = (dbValue) => {
    // Normalize to 0-1 range (3dB to -30dB)
    const normalized = Math.max(0, Math.min(1, (3 - dbValue) / 33));

    // Color gradient: red -> orange -> yellow -> green -> cyan -> blue
    if (normalized < 0.16) {
      // Red to orange
      const t = normalized / 0.16;
      return `rgb(${255}, ${Math.round(69 + t * 96)}, 0)`;
    }
    if (normalized < 0.33) {
      // Orange to yellow
      const t = (normalized - 0.16) / 0.17;
      return `rgb(${255}, ${Math.round(165 + t * 90)}, 0)`;
    }
    if (normalized < 0.5) {
      // Yellow to green
      const t = (normalized - 0.33) / 0.17;
      return `rgb(${Math.round(255 - t * 80)}, 255, 0)`;
    }
    if (normalized < 0.67) {
      // Green to cyan
      const t = (normalized - 0.5) / 0.17;
      return `rgb(0, 255, ${Math.round(t * 255)})`;
    }
    if (normalized < 0.83) {
      // Cyan to blue
      const t = (normalized - 0.67) / 0.16;
      return `rgb(0, ${Math.round(255 - t * 255)}, 255)`;
    }
    // Blue to dark blue
    const t = (normalized - 0.83) / 0.17;
    return `rgb(0, 0, ${Math.round(255 - t * 155)})`;
  };

  // Generate heatmap rectangles
  const rects = [];
  const numFreqBands = patterns.length;
  const numAngleBands = angles.length - 1;

  for (let fi = 0; fi < numFreqBands; fi++) {
    const pattern = patterns[fi];
    const freq = frequencies[Math.floor((fi * frequencies.length) / numFreqBands)];
    const logFreq = Math.log10(freq);

    for (let ai = 0; ai < numAngleBands; ai++) {
      const angle1 = pattern[ai][0];
      const angle2 = pattern[ai + 1][0];
      const splDb = (pattern[ai][1] + pattern[ai + 1][1]) / 2;

      // Calculate rectangle position
      const x1 = padding.left + ((logFreq - logMinFreq) / (logMaxFreq - logMinFreq)) * chartWidth;
      const y1 = padding.top + ((angle1 - minAngle) / (maxAngle - minAngle)) * chartHeight;
      const x2 =
        fi < numFreqBands - 1
          ? padding.left +
            ((Math.log10(
              frequencies[Math.floor(((fi + 1) * frequencies.length) / numFreqBands)]
            ) -
              logMinFreq) /
              (logMaxFreq - logMinFreq)) *
              chartWidth
          : width - padding.right;
      const y2 = padding.top + ((angle2 - minAngle) / (maxAngle - minAngle)) * chartHeight;

      const color = getColor(splDb);
      rects.push(
        `<rect x="${x1}" y="${y1}" width="${x2 - x1}" height="${y2 - y1}" fill="${color}" stroke="none"/>`
      );
    }
  }

  // Generate frequency tick marks (log scale)
  const freqTicks = [100, 200, 500, 1000, 2000, 5000, 10000, 20000];
  const freqTickMarks = freqTicks
    .filter((f) => f >= minFreq && f <= maxFreq)
    .map((freq) => {
      const logFreq = Math.log10(freq);
      const x = padding.left + ((logFreq - logMinFreq) / (logMaxFreq - logMinFreq)) * chartWidth;
      const label = freq >= 1000 ? `${freq / 1000}k` : freq;
      return `
                    <line x1="${x}" y1="${height - padding.bottom}" x2="${x}" y2="${height - padding.bottom + 5}" stroke="var(--text-color)" stroke-width="1"/>
                    <text x="${x}" y="${height - padding.bottom + 18}" text-anchor="middle" fill="var(--text-color)" font-size="10">${label}</text>
                `;
    })
    .join('');

  // Generate angle tick marks
  const angleTicks = [-180, -120, -60, 0, 60, 120, 180].filter(
    (a) => a >= minAngle && a <= maxAngle
  );
  const angleTickMarks = angleTicks
    .map((angle) => {
      const y = padding.top + ((angle - minAngle) / (maxAngle - minAngle)) * chartHeight;
      return `
                <line x1="${padding.left - 5}" y1="${y}" x2="${padding.left}" y2="${y}" stroke="var(--text-color)" stroke-width="1"/>
                <text x="${padding.left - 10}" y="${y + 3}" text-anchor="end" fill="var(--text-color)" font-size="10">${angle}°</text>
            `;
    })
    .join('');

  // Generate color scale legend
  const legendX = width - padding.right + 20;
  const legendWidth = 20;
  const legendHeight = chartHeight;
  const legendSteps = 20;
  const legendRects = [];
  for (let i = 0; i < legendSteps; i++) {
    const dbValue = 3 - (i / legendSteps) * 33;
    const y = padding.top + (i / legendSteps) * legendHeight;
    const h = legendHeight / legendSteps;
    const color = getColor(dbValue);
    legendRects.push(
      `<rect x="${legendX}" y="${y}" width="${legendWidth}" height="${h}" fill="${color}" stroke="none"/>`
    );
  }

  // Legend labels
  const legendLabels = [3, 0, -6, -12, -18, -24, -30];
  const legendLabelMarks = legendLabels
    .map((db) => {
      const normalized = (3 - db) / 33;
      const y = padding.top + normalized * legendHeight;
      return `
                <line x1="${legendX}" y1="${y}" x2="${legendX - 3}" y2="${y}" stroke="var(--text-color)" stroke-width="1"/>
                <text x="${legendX + legendWidth + 5}" y="${y + 3}" fill="var(--text-color)" font-size="9">${db}</text>
            `;
    })
    .join('');

  return `
            <svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" style="background: #1a1a1a;">
                <!-- Heatmap -->
                ${rects.join('\n')}

                <!-- Border -->
                <rect x="${padding.left}" y="${padding.top}" width="${chartWidth}" height="${chartHeight}" fill="none" stroke="var(--border-color)" stroke-width="1"/>

                <!-- Axes -->
                ${freqTickMarks}
                ${angleTickMarks}

                <!-- Color scale legend -->
                ${legendRects.join('\n')}
                <rect x="${legendX}" y="${padding.top}" width="${legendWidth}" height="${legendHeight}" fill="none" stroke="var(--border-color)" stroke-width="1"/>
                ${legendLabelMarks}

                <!-- Axis labels -->
                <text x="${padding.left + chartWidth / 2}" y="${height - 5}" text-anchor="middle" fill="var(--text-color)" font-size="12" font-weight="600">Frequency [kHz]</text>
                <text x="15" y="${padding.top + chartHeight / 2}" text-anchor="middle" fill="var(--text-color)" font-size="12" font-weight="600" transform="rotate(-90, 15, ${padding.top + chartHeight / 2})">Angle [°]</text>
                <text x="${legendX + legendWidth + 35}" y="${padding.top + legendHeight / 2}" text-anchor="middle" fill="var(--text-color)" font-size="10" font-weight="600" transform="rotate(90, ${legendX + legendWidth + 35}, ${padding.top + legendHeight / 2})">dB rel 0°</text>

                <!-- Title -->
                <text x="${width / 2}" y="20" text-anchor="middle" fill="var(--text-color)" font-size="14" font-weight="600">Vertical Directivity</text>
            </svg>
        `;
}
