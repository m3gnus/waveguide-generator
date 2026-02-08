import { spawn } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.join(__dirname, '..');

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
const backend = spawn('python3', ['app.py'], {
  cwd: path.join(rootDir, 'server'),
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
  console.error('   - Python dependencies are not installed');
  console.error('   - Run: cd server && pip3 install -r requirements.txt');
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
console.log('');
console.log('Press Ctrl+C to stop both servers');
console.log('');
