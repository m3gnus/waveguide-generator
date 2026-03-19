import fs from 'fs';
import os from 'os';
import path from 'path';

export const BACKEND_PYTHON_MARKER_RELATIVE_PATH = path.join('.waveguide', 'backend-python.path');

function normalizeText(value) {
  const text = String(value ?? '').trim();
  return text || null;
}

function readPreferredPythonFromMarker(rootDir, { existsSync, readFileSync }) {
  const markerPath = path.join(rootDir, BACKEND_PYTHON_MARKER_RELATIVE_PATH);
  if (!existsSync(markerPath)) {
    return { markerPath, python: null };
  }

  try {
    const raw = readFileSync(markerPath, 'utf8');
    const firstLine = String(raw).split(/\r?\n/, 1)[0];
    const preferred = normalizeText(firstLine);
    if (!preferred) {
      return { markerPath, python: null };
    }
    const resolved = path.isAbsolute(preferred)
      ? preferred
      : path.resolve(rootDir, preferred);
    if (existsSync(resolved)) {
      return { markerPath, python: resolved };
    }
    return { markerPath, python: null };
  } catch {
    return { markerPath, python: null };
  }
}

export function resolveBackendPython(
  rootDir,
  {
    env = process.env,
    existsSync = fs.existsSync,
    readFileSync = fs.readFileSync,
    homeDir = os.homedir()
  } = {}
) {
  const envPythonBin = normalizeText(env?.PYTHON_BIN);
  if (envPythonBin) {
    return { python: envPythonBin, source: 'env:PYTHON_BIN' };
  }

  const envWgBackendPython = normalizeText(env?.WG_BACKEND_PYTHON);
  if (envWgBackendPython) {
    return { python: envWgBackendPython, source: 'env:WG_BACKEND_PYTHON' };
  }

  const markerResult = readPreferredPythonFromMarker(rootDir, { existsSync, readFileSync });
  if (markerResult.python) {
    return {
      python: markerResult.python,
      source: `marker:${markerResult.markerPath}`
    };
  }

  const venvPythonUnix = path.join(rootDir, '.venv', 'bin', 'python');
  if (existsSync(venvPythonUnix)) {
    return { python: venvPythonUnix, source: 'fallback:.venv' };
  }

  const venvPythonWindows = path.join(rootDir, '.venv', 'Scripts', 'python.exe');
  if (existsSync(venvPythonWindows)) {
    return { python: venvPythonWindows, source: 'fallback:.venv' };
  }

  const openclCpuEnvPython = path.join(homeDir, '.waveguide-generator', 'opencl-cpu-env', 'bin', 'python');
  if (existsSync(openclCpuEnvPython)) {
    return { python: openclCpuEnvPython, source: 'fallback:opencl-cpu-env' };
  }

  return { python: 'python3', source: 'fallback:python3' };
}
