export function calculateStats(values) {
  if (!values || values.length === 0) return null;
  const validValues = values.filter((v) => typeof v === 'number' && isFinite(v));
  if (validValues.length === 0) return null;

  const sum = validValues.reduce((a, b) => a + b, 0);
  const mean = sum / validValues.length;
  const variance = validValues.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / validValues.length;

  return {
    min: Math.min(...validValues),
    max: Math.max(...validValues),
    mean,
    stdDev: Math.sqrt(variance),
    count: validValues.length
  };
}

export function findValueAtFrequency(frequencies, values, targetFreq) {
  if (!frequencies.length || !values.length) return null;

  // Find closest frequency
  let closestIndex = 0;
  let closestDiff = Math.abs(frequencies[0] - targetFreq);

  for (let i = 1; i < frequencies.length; i++) {
    const diff = Math.abs(frequencies[i] - targetFreq);
    if (diff < closestDiff) {
      closestDiff = diff;
      closestIndex = i;
    }
  }

  // Only return if reasonably close (within 20%)
  if (closestDiff / targetFreq > 0.2) return null;

  return values[closestIndex];
}
