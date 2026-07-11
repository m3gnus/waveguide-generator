function normalizeHost(value, fallback) {
  return String(value ?? '').trim() || fallback;
}

function normalizePort(value, fallback) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= 65535 ? parsed : fallback;
}

export function resolveFrontendPort(env = process.env) {
  return normalizePort(env?.PORT, 3000);
}

export function resolveServerUrls(env = process.env) {
  const frontendHost = normalizeHost(env?.HOST, 'localhost');
  const backendHost = normalizeHost(env?.MWG_BACKEND_HOST, 'localhost');
  const frontendPort = resolveFrontendPort(env);
  const backendPort = normalizePort(env?.MWG_BACKEND_PORT, 8000);

  return {
    frontend: `http://${frontendHost}:${frontendPort}`,
    backend: `http://${backendHost}:${backendPort}`,
  };
}
