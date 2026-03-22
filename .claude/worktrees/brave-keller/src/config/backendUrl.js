/**
 * Shared backend URL configuration.
 *
 * All fetch paths that communicate with the Python backend should derive their
 * base URL from this constant so there is a single place to change it.
 * Per-instance overrides (e.g. BemSolver.backendUrl) remain possible for tests.
 */
export const DEFAULT_BACKEND_URL = 'http://localhost:8000';
