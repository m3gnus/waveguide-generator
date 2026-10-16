import { useEffect, useState } from 'react';
import type { JobItem } from '../api/jobsSocket';
import { listProjectDocuments, projectDocumentUrl, type CadProjectDocument } from '../api/cadProjects';

/** Optional CAD attachments decorate the shared run; their failure cannot hide it. */
export function RunModelDetails({ job }: { job: JobItem }) {
  const lineage = job.cad_source?.lineage_id;
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
  if (!hash || !lineage) return <p className="cad-model-missing" title="This run predates model capture, so no Fusion file was kept.">Model state not recorded · no Fusion file</p>;
  if (!current) return <p role="status">Loading model attachment…</p>;
  if (current.error) return <p className="cad-model-missing" role="status">Could not read the archived Fusion file: {current.error}</p>;
  if (!current.document?.filename) return <p className="cad-model-missing" title={current.document
    ? 'The record of this model survived but its Fusion file did not.'
    : 'No Fusion file was archived for this model. Capture was off when it arrived, or the file has been removed.'}>no Fusion file</p>;
  return <p><a className="cad-model-download" href={projectDocumentUrl(lineage, hash)}
    title="Download the Fusion model this run was solved from. Open it in Fusion and send it to WG to keep working.">Fusion file</a>
    {current.document.capturedAt && <span> · Model captured {current.document.capturedAt}</span>}</p>;
}
