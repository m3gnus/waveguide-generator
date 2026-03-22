import { DEFAULT_BACKEND_URL } from '../../config/backendUrl.js';

/**
 * Download the simulation mesh artifact (.msh) for a job.
 * Fire-and-forget — does not block simulation progress.
 * @param {string} jobId
 * @param {string} [backendUrl] - Backend base URL (defaults to DEFAULT_BACKEND_URL)
 */
export async function downloadMeshArtifact(jobId, backendUrl = DEFAULT_BACKEND_URL) {
  // Brief delay to allow backend to finish mesh generation and store artifact
  await new Promise(r => setTimeout(r, 2000));

  const resp = await fetch(`${backendUrl}/api/mesh-artifact/${jobId}`);
  if (!resp.ok) {
    throw new Error(`Mesh artifact not available (${resp.status})`);
  }
  const mshText = await resp.text();
  const blob = new Blob([mshText], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `simulation_mesh_${jobId}.msh`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
