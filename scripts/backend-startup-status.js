import fs from 'fs';
import path from 'path';

export const BACKEND_STARTUP_STATUS_RELATIVE_PATH = path.join('.waveguide', 'backend-startup.json');

const MAX_CAPTURED_LINES = 24;
const MAX_DETAIL_LENGTH = 4000;

function normalizeLine(value) {
  return String(value ?? '')
    .replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, '')
    .trim();
}

export function createBackendOutputCapture() {
  const lines = [];
  let partial = '';

  return {
    append(chunk) {
      const text = partial + String(chunk ?? '');
      const parts = text.split(/\r?\n/);
      partial = parts.pop() || '';
      for (const part of parts) {
        const line = normalizeLine(part);
        if (line) lines.push(line);
      }
      if (lines.length > MAX_CAPTURED_LINES) {
        lines.splice(0, lines.length - MAX_CAPTURED_LINES);
      }
    },
    details() {
      const allLines = partial ? [...lines, normalizeLine(partial)].filter(Boolean) : lines;
      return allLines.join('\n').slice(-MAX_DETAIL_LENGTH);
    },
  };
}

export function summarizeBackendFailure({ error = null, exitCode = null, details = '' } = {}) {
  const errorMessage = String(error?.message || '').trim();
  const combined = `${errorMessage}\n${details}`;

  if (error?.code === 'ENOENT' || /not recognized|command not found|no such file/i.test(combined)) {
    return {
      reason: 'The configured Python interpreter could not be started.',
      guidance: 'Re-run the installer so WG can create and record its Python environment.',
    };
  }

  if (/address already in use|EADDRINUSE|WinError 10048/i.test(combined)) {
    return {
      reason: 'Backend port 8000 is already in use by another process.',
      guidance: 'Close the other WG/backend process, then restart the app.',
    };
  }

  if (/ModuleNotFoundError|No module named|cannot import name/i.test(combined)) {
    const missingModule = combined.match(/No module named ['\"]([^'\"]+)['\"]/i)?.[1];
    return {
      reason: missingModule
        ? `The backend Python environment is missing the ${missingModule} module.`
        : 'The backend Python environment is missing a required module.',
      guidance: 'Re-run the installer to repair the backend dependencies.',
    };
  }

  const meaningfulLines = String(details || '')
    .split(/\r?\n/)
    .map(normalizeLine)
    .filter(
      (line) =>
        line &&
        !/^INFO:/.test(line) &&
        !/^Traceback \(most recent call last\):$/.test(line) &&
        !/^File /.test(line)
    );
  const lastLine = meaningfulLines.at(-1);
  return {
    reason:
      lastLine ||
      errorMessage ||
      `The backend process exited${exitCode == null ? '' : ` with code ${exitCode}`}.`,
    guidance: 'Check the launcher output or re-run the installer for the full diagnostic.',
  };
}

export function writeBackendStartupStatus(rootDir, status, { fsImpl = fs } = {}) {
  const statusPath = path.join(rootDir, BACKEND_STARTUP_STATUS_RELATIVE_PATH);
  const statusDir = path.dirname(statusPath);
  const tempPath = `${statusPath}.tmp`;
  try {
    fsImpl.mkdirSync(statusDir, { recursive: true });
    fsImpl.writeFileSync(
      tempPath,
      `${JSON.stringify({ ...status, updatedAt: new Date().toISOString() }, null, 2)}\n`,
      'utf8'
    );
    fsImpl.renameSync(tempPath, statusPath);
    return true;
  } catch {
    // Diagnostics are best-effort. A read-only project folder must not become
    // the reason the backend itself is never attempted.
    return false;
  }
}
