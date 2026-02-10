import { spawn, exec } from 'child_process';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.join(__dirname, '..');
const serverDir = path.join(rootDir, 'server');
const venvPythonUnix = path.join(rootDir, '.venv', 'bin', 'python');
const venvPythonWindows = path.join(rootDir, '.venv', 'Scripts', 'python.exe');
const backendPython = process.env.PYTHON_BIN
  || (fs.existsSync(venvPythonUnix) ? venvPythonUnix : null)
  || (fs.existsSync(venvPythonWindows) ? venvPythonWindows : null)
  || 'python3';

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
  stdio: 'inherit'
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
  console.error('   - Backend dependencies are not installed in .venv');
  console.error('   - Run: python3 -m venv .venv && ./.venv/bin/pip install -r server/requirements.txt');
  console.error('');
  console.error('The frontend will still work, but simulations will use mock data.');
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
    console.error('   Frontend is still running - simulations will use mock data');
  }
});

console.log('');
console.log('📡 Servers starting...');
console.log('   Frontend: http://localhost:3000');
console.log('   Backend:  http://localhost:8000');
console.log(`   Python:   ${backendPython}`);
console.log('');
console.log('Press Ctrl+C to stop both servers');
console.log('');

// Open browser after servers have had time to start
setTimeout(() => {
  const url = 'http://localhost:3000';
  const cmd = process.platform === 'win32' ? `start ${url}`
            : process.platform === 'darwin' ? `open ${url}`
            : `xdg-open ${url}`;
  exec(cmd);
}, 3000);
