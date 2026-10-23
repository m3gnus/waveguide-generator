import { useEffect, useState } from 'react';
import { runDocumentName, runSourceLabel } from '../jobs/runIdentity';
import { runSourceForJob } from '../jobs/exportNaming';
import { runModelKey } from '../jobs/runFilters';
import type { JobItem } from '../api/jobsSocket';
import { listProjectDocuments, projectDocumentUrl, type CadProjectDocument } from '../api/cadProjects';

/** Optional CAD attachments decorate the shared run; their failure cannot hide it. */
export function RunModelDetails({ job }: { job: JobItem }) {
  const lineage = job.cad_source?.lineage_id ?? job.cad_state?.snapshot?.project_lineage_id;
  const hash = job.cad_source?.return_state_hash;
  const [state, setState] = useState<{ key: string; document: CadProjectDocument | null; error: string | null } | null>(null);
  const key = `${lineage}:${hash}`;
  useEffect(() => {
    let current = true;
    if (!lineage || !hash) return;
    void listProjectDocuments(lineage).then(listing => {
      const items = Array.isArray(listing?.items) ? listing.items : [];
      if (current) setState({ key, document: items.find(item => item.returnStateHash === hash) ?? null, error: null });
    }, error => {
      if (current) setState({ key, document: null, error: String(error) });
    });
    return () => { current = false; };
  }, [lineage, hash, key]);
  const current = state?.key === key ? state : null;
  let attachment;
  if (!hash || !lineage) attachment = <p className="cad-model-missing" title="This run predates model capture, so no Fusion file was kept.">Model state not recorded · no Fusion file</p>;
  else if (!current) attachment = <p role="status">Loading model attachment…</p>;
  else if (current.error) attachment = <p className="cad-model-missing" role="status">Could not read the archived Fusion file: {current.error}</p>;
  else if (!current.document?.filename) attachment = <p className="cad-model-missing" title={current.document
    ? 'The record of this model survived but its Fusion file did not.'
    : 'No Fusion file was archived for this model. Capture was off when it arrived, or the file has been removed.'}>no Fusion file</p>;
  else attachment = <p><a className="cad-model-download" href={projectDocumentUrl(lineage, hash)}
    title="Download the Fusion model this run was solved from. Open it in Fusion and send it to WG to keep working.">Fusion file</a>
    {current.document.capturedAt && <span> · Model captured {current.document.capturedAt}</span>}</p>;
  const document = runDocumentName(job) || current?.document?.documentName || 'Not recorded';
  const modelHash = job.cad_source?.solve_model_sha256 || job.cad_source?.transformed_geometry_hash
    || hash || job.cad_source?.manifest_sha256 || job.cad_state?.snapshot?.manifest_sha256;
  return <section className="run-model-details" aria-label="Run model details">
    <dl>
      <dt>Document</dt><dd>{document}</dd>
      <dt>Source</dt><dd>{runSourceLabel(job)}</dd>
      <dt>Model</dt><dd>{job.cad_source?.model_id || runModelKey(job)}</dd>
      <dt>Model hash</dt><dd>{modelHash || 'Not recorded'}</dd>
    </dl>
    {runSourceForJob(job) !== 'mesh-file' && attachment}
  </section>;

}
