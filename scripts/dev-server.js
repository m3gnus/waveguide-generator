import { createReadStream } from 'fs';
import { realpath, stat } from 'fs/promises';
import http from 'http';
import path from 'path';
import { pathToFileURL } from 'url';

const PORT = Number(process.env.PORT) || 3000;
const ROOT_DIR = process.cwd();

export function resolveDevServerHost(env = process.env) {
  return String(env?.HOST || '127.0.0.1').trim() || '127.0.0.1';
}

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
  '.wasm': 'application/wasm',
});

function isPathInsideRoot(rootDir, candidatePath) {
  const relative = path.relative(rootDir, candidatePath);
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
}

export function isAllowedStaticPath(relativePath) {
  const normalized = String(relativePath || '')
    .replaceAll('\\', '/')
    .replace(/^\/+/, '');
  const segments = normalized.split('/');
  if (segments.some((segment) => segment === '.' || segment === '..')) {
    return false;
  }
  return (
    normalized === 'index.html' ||
    normalized.startsWith('src/') ||
    normalized.startsWith('node_modules/three/build/') ||
    normalized.startsWith('node_modules/three/examples/jsm/')
  );
}

async function resolveStaticFile(rootDir, pathname) {
  let decodedPath = '/';
  try {
    decodedPath = decodeURIComponent(pathname || '/');
  } catch {
    return null;
  }
  const relativePath = decodedPath === '/' ? 'index.html' : decodedPath.slice(1);
  if (!isAllowedStaticPath(relativePath)) {
    return null;
  }
  const candidatePath = path.resolve(rootDir, relativePath);

  if (!isPathInsideRoot(rootDir, candidatePath)) {
    return null;
  }

  try {
    const [canonicalRoot, canonicalCandidate] = await Promise.all([
      realpath(rootDir),
      realpath(candidatePath),
    ]);
    if (!isPathInsideRoot(canonicalRoot, canonicalCandidate)) {
      return null;
    }
    const info = await stat(canonicalCandidate);
    return info.isFile() ? canonicalCandidate : null;
  } catch {
    return null;
  }
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

export function createDevServer({ rootDir = ROOT_DIR } = {}) {
  return http.createServer(async (req, res) => {
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

    const staticFile = await resolveStaticFile(rootDir, pathname);
    if (staticFile) {
      sendFile(res, staticFile, method);
      return;
    }

    res.statusCode = 404;
    res.setHeader('Content-Type', 'text/plain; charset=utf-8');
    res.end('Not found');
  });
}

const launchedDirectly = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;

if (launchedDirectly) {
  const host = resolveDevServerHost();
  const server = createDevServer();
  server.listen(PORT, host, () => {
    const displayHost = host === '127.0.0.1' ? 'localhost' : host;
    console.log(`\n🚀 WG - Waveguide Generator running at http://${displayHost}:${PORT}`);
    console.log('\nAvailable endpoints:');
    console.log(`http://${displayHost}:${PORT}/`);
  });
}
