import { spawn } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';
import { resolveBackendPython } from './backend-python.js';
import {
  createBackendOutputCapture,
  summarizeBackendFailure,
  writeBackendStartupStatus,
} from './backend-startup-status.js';
import { resolveServerUrls } from './server-urls.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.join(__dirname, '..');
const serverDir = path.join(rootDir, 'server');
const backendPythonResolution = resolveBackendPython(rootDir);
const backendPython = backendPythonResolution.python;
const serverUrls = resolveServerUrls(process.env);
const backendOutput = createBackendOutputCapture();

writeBackendStartupStatus(rootDir, {
  state: 'starting',
  reason: 'The backend process is starting.',
  python: backendPython,
  pythonSource: backendPythonResolution.source,
});

console.log('╔══════════════════════════════════════════════════════════════╗');
console.log('║  WG - Waveguide Generator                     ║');
console.log('╚══════════════════════════════════════════════════════════════╝');
console.log('');
console.log('🚀 Starting both frontend and backend servers...');
console.log('');

// Start frontend server
const frontend = spawn('node', ['scripts/dev-server.js'], {
  cwd: rootDir,
  stdio: 'inherit',
});

// Start backend server
const backend = spawn(backendPython, ['app.py'], {
  cwd: serverDir,
  stdio: ['ignore', 'pipe', 'pipe'],
  env: {
    ...process.env,
    WG_BACKEND_PYTHON_SOURCE: backendPythonResolution.source,
  },
});

for (const [stream, destination] of [
  [backend.stdout, process.stdout],
  [backend.stderr, process.stderr],
]) {
  stream?.on('data', (chunk) => {
    backendOutput.append(chunk);
    destination.write(chunk);
  });
}

let backendSpawnFailed = false;
let backendHealthTimer = null;
let backendHealthProbeActive = false;

async function probeBackendHealth() {
  if (backendHealthProbeActive) return;
  backendHealthProbeActive = true;
  try {
    const response = await fetch(`${serverUrls.backend}/`, {
      signal: AbortSignal.timeout(2000),
    });
    if (!response.ok) return;
    writeBackendStartupStatus(rootDir, {
      state: 'running',
      reason: 'The backend API is running.',
      python: backendPython,
      pythonSource: backendPythonResolution.source,
    });
    if (backendHealthTimer) clearInterval(backendHealthTimer);
    backendHealthTimer = null;
  } catch {
    // The import-time dependency probes can take a while. Keep polling while
    // the process is alive; an actual spawn/exit failure is reported below.
  } finally {
    backendHealthProbeActive = false;
  }
}

backendHealthTimer = setInterval(probeBackendHealth, 1000);
probeBackendHealth();

// Handle shutdown
const cleanup = () => {
  console.log('\n\n🛑 Shutting down servers...');
  frontend.kill();
  backend.kill();
  if (backendHealthTimer) clearInterval(backendHealthTimer);
  process.exit(0);
};

process.on('SIGINT', cleanup);
process.on('SIGTERM', cleanup);

// Handle errors
frontend.on('error', (err) => {
  console.error('❌ Frontend server error:', err);
});

backend.on('error', (err) => {
  backendSpawnFailed = true;
  const failure = summarizeBackendFailure({ error: err, details: backendOutput.details() });
  writeBackendStartupStatus(rootDir, {
    state: 'error',
    ...failure,
    details: backendOutput.details(),
    python: backendPython,
    pythonSource: backendPythonResolution.source,
  });
  console.error('❌ Backend server error:', err);
  console.error('');
  console.error('💡 Backend failed to start. This might be because:');
  console.error(`   - Python command is not available: ${backendPython}`);
  console.error('   - Backend dependencies are not installed for that interpreter');
  console.error(
    '   - Or: python3 -m venv .venv && ./.venv/bin/pip install -r server/requirements.txt'
  );
  console.error('');
  console.error(
    'Frontend remains available, but backend-dependent features are blocked until the backend starts.'
  );
  console.error(
    'Blocked features include simulation solve, HornLab mesher builds, and backend chart rendering.'
  );
});

frontend.on('exit', (code) => {
  if (code !== 0 && code !== null) {
    console.error(`❌ Frontend server exited with code ${code}`);
  }
  cleanup();
});

backend.on('exit', (code) => {
  if (backendHealthTimer) clearInterval(backendHealthTimer);
  backendHealthTimer = null;
  if (code !== 0 && code !== null) {
    if (!backendSpawnFailed) {
      const failure = summarizeBackendFailure({
        exitCode: code,
        details: backendOutput.details(),
      });
      writeBackendStartupStatus(rootDir, {
        state: 'error',
        ...failure,
        exitCode: code,
        details: backendOutput.details(),
        python: backendPython,
        pythonSource: backendPythonResolution.source,
      });
    }
    console.error(`⚠️  Backend server exited with code ${code}`);
    console.error(
      '   Frontend is still running, but backend-dependent features are blocked until restart.'
    );
  } else if (!backendSpawnFailed) {
    writeBackendStartupStatus(rootDir, {
      state: 'stopped',
      reason: 'The backend process stopped.',
      python: backendPython,
      pythonSource: backendPythonResolution.source,
    });
  }
});

console.log('');
console.log('📡 Servers starting...');
console.log(`   Frontend: ${serverUrls.frontend}`);
console.log(`   Backend:  ${serverUrls.backend}`);
console.log(`   Python:   ${backendPython} (${backendPythonResolution.source})`);
console.log('');
console.log('Press Ctrl+C to stop both servers');
console.log('');

// Open browser after servers have had time to start
setTimeout(() => {
  const command =
    process.platform === 'win32' ? 'cmd' : process.platform === 'darwin' ? 'open' : 'xdg-open';
  const args =
    process.platform === 'win32' ? ['/c', 'start', '', serverUrls.frontend] : [serverUrls.frontend];
  const browser = spawn(command, args, { detached: true, stdio: 'ignore' });
  browser.on('error', (err) => {
    console.warn('Could not open browser automatically:', err.message);
  });
  browser.unref();
}, 3000);
