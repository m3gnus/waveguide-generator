export const GENERATION_PROJECT_MANIFEST_FILE_NAME = 'waveguide.project.v1.json';
export const GENERATION_PROJECT_MANIFEST_VERSION = 1;
export const GENERATION_SCRIPT_SNAPSHOT_FILE_NAME = 'script.snapshot.mwg';

const EXPORT_FILE_SUFFIX_BY_FORMAT = Object.freeze({
  csv: 'results.csv',
  json: 'results.json',
  txt: 'report.txt',
  polar_csv: 'polar.csv',
  impedance_csv: 'impedance.csv',
  vacs: 'spectrum.txt'
});

function normalizeBaseName(value, fallback = 'generation') {
  const text = String(value ?? '').trim();
  return text || fallback;
}

function normalizeToken(value, fallback = 'artifact') {
  const text = String(value ?? '').trim().toLowerCase();
  if (!text) return fallback;
  const normalized = text
    .replace(/[^a-z0-9._-]+/g, '_')
    .replace(/^_+|_+$/g, '');
  return normalized || fallback;
}

function parseChartKeyFromFileName(fileName, baseName) {
  const name = String(fileName || '').trim();
  if (!name) return '';
  const suffix = '.png';
  if (!name.endsWith(suffix)) return '';
  const stem = name.slice(0, -suffix.length);
  const prefix = `${baseName}_`;
  if (stem.startsWith(prefix)) {
    return stem.slice(prefix.length);
  }
  return '';
}

function parseFusionVariant(fileName, baseName) {
  const name = String(fileName || '').trim();
  const profiles = `${baseName}_profiles.csv`;
  const slices = `${baseName}_slices.csv`;
  if (name === slices) return 'slices';
  if (name === profiles) return 'profiles';
  return '';
}

export function resolveGenerationExportFileName(
  formatId,
  {
    baseName,
    chartKey = null,
    originalFileName = null
  } = {}
) {
  const normalizedBaseName = normalizeBaseName(baseName);
  const normalizedFormatId = String(formatId || '').trim();

  if (normalizedFormatId === 'png') {
    const key = normalizeToken(
      chartKey || parseChartKeyFromFileName(originalFileName, normalizedBaseName),
      'chart'
    );
    return `${normalizedBaseName}_${key}.png`;
  }

  if (normalizedFormatId === 'stl') {
    return `${normalizedBaseName}.stl`;
  }

  if (normalizedFormatId === 'fusion_csv') {
    const variant = parseFusionVariant(originalFileName, normalizedBaseName);
    const suffix = variant === 'slices' ? 'slices' : 'profiles';
    return `${normalizedBaseName}_${suffix}.csv`;
  }

  const suffix = EXPORT_FILE_SUFFIX_BY_FORMAT[normalizedFormatId];
  if (suffix) {
    return `${normalizedBaseName}_${suffix}`;
  }

  const fallbackName = String(originalFileName || '').trim();
  return fallbackName || `${normalizedBaseName}_${normalizeToken(normalizedFormatId, 'artifact')}.dat`;
}

export function resolveGenerationScriptSnapshotFileName() {
  return GENERATION_SCRIPT_SNAPSHOT_FILE_NAME;
}

export function parseExportedFileRecord(value) {
  const text = String(value || '').trim();
  if (!text) {
    return null;
  }

  const separatorIndex = text.indexOf(':');
  if (separatorIndex <= 0 || separatorIndex >= text.length - 1) {
    return null;
  }

  const formatId = text.slice(0, separatorIndex).trim();
  const fileName = text.slice(separatorIndex + 1).trim();
  if (!formatId || !fileName) {
    return null;
  }

  return { formatId, fileName };
}

export function buildGenerationProjectManifest({
  directoryName,
  job,
  exportedFiles = [],
  scriptSnapshotFileName = null,
  updatedAt = null
} = {}) {
  const normalizedDirectoryName = normalizeBaseName(directoryName, '');
  const normalizedUpdatedAt = String(updatedAt || '').trim() || new Date().toISOString();
  const parsedExports = [];
  const seenExports = new Set();

  for (const rawValue of exportedFiles || []) {
    const parsed = parseExportedFileRecord(rawValue);
    if (!parsed) {
      continue;
    }
    const key = `${parsed.formatId}:${parsed.fileName}`;
    if (seenExports.has(key)) {
      continue;
    }
    seenExports.add(key);
    parsedExports.push({
      formatId: parsed.formatId,
      fileName: parsed.fileName
    });
  }

  return {
    version: GENERATION_PROJECT_MANIFEST_VERSION,
    updatedAt: normalizedUpdatedAt,
    generation: {
      folder: normalizedDirectoryName,
      id: String(job?.id || '').trim(),
      label: String(job?.label || '').trim() || normalizedDirectoryName,
      status: String(job?.status || 'queued').trim(),
      createdAt: job?.createdAt ?? null,
      completedAt: job?.completedAt ?? null
    },
    naming: {
      generationFolderContract: '<outputName>_<counter> (fallback: <jobId>)',
      scriptSnapshotFile: resolveGenerationScriptSnapshotFileName()
    },
    artifacts: {
      scriptSnapshot: scriptSnapshotFileName
        ? {
          fileName: scriptSnapshotFileName,
          format: 'mwg'
        }
        : null,
      selectedExports: parsedExports
    }
  };
}
