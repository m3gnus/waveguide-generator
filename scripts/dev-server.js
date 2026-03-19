import { createReadStream } from 'fs';
import { access, stat } from 'fs/promises';
import http from 'http';
import path from 'path';

const PORT = Number(process.env.PORT) || 3000;
const ROOT_DIR = process.cwd();
const INDEX_FILE = path.join(ROOT_DIR, 'index.html');

const MIME_TYPES = Object.freeze({
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.ico': 'image/x-icon',
  '.txt': 'text/plain; charset=utf-8',
  '.wasm': 'application/wasm'
});

function isPathInsideRoot(candidatePath) {
  const relative = path.relative(ROOT_DIR, candidatePath);
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
}

async function resolveStaticFile(pathname) {
  let decodedPath = '/';
  try {
    decodedPath = decodeURIComponent(pathname || '/');
  } catch {
    return null;
  }
  const relativePath = decodedPath === '/' ? 'index.html' : decodedPath.slice(1);
  const candidatePath = path.resolve(ROOT_DIR, relativePath);

  if (!isPathInsideRoot(candidatePath)) {
    return null;
  }

  try {
    const info = await stat(candidatePath);
    if (info.isFile()) {
      return candidatePath;
    }
    if (info.isDirectory()) {
      const directoryIndex = path.join(candidatePath, 'index.html');
      await access(directoryIndex);
      return directoryIndex;
    }
  } catch {
    return null;
  }

  return null;
}

function sendFile(res, filePath, method) {
  const extension = path.extname(filePath).toLowerCase();
  const contentType = MIME_TYPES[extension] || 'application/octet-stream';

  res.statusCode = 200;
  res.setHeader('Content-Type', contentType);
  if (method === 'HEAD') {
    res.end();
    return;
  }

  const stream = createReadStream(filePath);
  stream.on('error', () => {
    if (!res.headersSent) {
      res.statusCode = 500;
      res.setHeader('Content-Type', 'text/plain; charset=utf-8');
    }
    res.end('Internal server error');
  });
  stream.pipe(res);
}

const server = http.createServer(async (req, res) => {
  const method = String(req.method || 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD') {
    res.statusCode = 405;
    res.setHeader('Content-Type', 'text/plain; charset=utf-8');
    res.end('Method not allowed');
    return;
  }

  let pathname = '/';
  try {
    pathname = new URL(req.url || '/', 'http://localhost').pathname;
  } catch {
    pathname = '/';
  }

  if (pathname === '/favicon.ico') {
    res.statusCode = 404;
    res.end();
    return;
  }

  const staticFile = await resolveStaticFile(pathname);
  if (staticFile) {
    sendFile(res, staticFile, method);
    return;
  }

  // SPA fallback to root index for unknown routes.
  sendFile(res, INDEX_FILE, method);
});

server.listen(PORT, () => {
  console.log(`\n🚀 WG - Waveguide Generator running at http://localhost:${PORT}`);
  console.log('\nAvailable endpoints:');
  console.log(`http://localhost:${PORT}/`);
});
