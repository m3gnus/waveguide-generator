import { spawn } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';
import { resolveBackendPython } from './backend-python.js';
import { resolveServerUrls } from './server-urls.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.join(__dirname, '..');
const serverDir = path.join(rootDir, 'server');
const backendPythonResolution = resolveBackendPython(rootDir);
const backendPython = backendPythonResolution.python;
const serverUrls = resolveServerUrls(process.env);

console.log('╔══════════════════════════════════════════════════════════════╗');
console.log('║  WG - Waveguide Generator                     ║');
console.log('╚══════════════════════════════════════════════════════════════╝');
console.log('');
console.log('🚀 Starting both frontend and backend servers...');
console.log('');

// Start frontend server
const frontend = spawn('node', ['scripts/dev-server.js'], {
  cwd: rootDir,
  stdio: 'inherit'
});

// Start backend server
const backend = spawn(backendPython, ['app.py'], {
  cwd: serverDir,
  stdio: 'inherit',
  env: {
    ...process.env,
    WG_BACKEND_PYTHON_SOURCE: backendPythonResolution.source
  }
});

// Handle shutdown
const cleanup = () => {
  console.log('\n\n🛑 Shutting down servers...');
  frontend.kill();
  backend.kill();
  process.exit(0);
};

process.on('SIGINT', cleanup);
process.on('SIGTERM', cleanup);

// Handle errors
frontend.on('error', (err) => {
  console.error('❌ Frontend server error:', err);
});

backend.on('error', (err) => {
  console.error('❌ Backend server error:', err);
  console.error('');
  console.error('💡 Backend failed to start. This might be because:');
  console.error(`   - Python command is not available: ${backendPython}`);
  console.error('   - Backend dependencies are not installed for that interpreter');
  console.error('   - Or: python3 -m venv .venv && ./.venv/bin/pip install -r server/requirements.txt');
  console.error('');
  console.error('Frontend remains available, but backend-dependent features are blocked until the backend starts.');
  console.error('Blocked features include simulation solve, HornLab mesher builds, and backend chart rendering.');
});

frontend.on('exit', (code) => {
  if (code !== 0 && code !== null) {
    console.error(`❌ Frontend server exited with code ${code}`);
  }
  cleanup();
});

backend.on('exit', (code) => {
  if (code !== 0 && code !== null) {
    console.error(`⚠️  Backend server exited with code ${code}`);
    console.error('   Frontend is still running, but backend-dependent features are blocked until restart.');
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
  const command = process.platform === 'win32' ? 'cmd'
    : process.platform === 'darwin' ? 'open'
    : 'xdg-open';
  const args = process.platform === 'win32'
    ? ['/c', 'start', '', serverUrls.frontend]
    : [serverUrls.frontend];
  const browser = spawn(command, args, { detached: true, stdio: 'ignore' });
  browser.on('error', (err) => {
    console.warn('Could not open browser automatically:', err.message);
  });
  browser.unref();
}, 3000);
