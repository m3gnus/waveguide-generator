import { MoveRunsButton } from './MoveRunsDialog';
import { useEffect, useState } from 'react';
import { runDocumentName, runSourceLabel } from '../jobs/runIdentity';
import { runSourceForJob } from '../jobs/exportNaming';
import { useDocumentStore } from '../stores/document';
import { modelForRun } from '../model/currentModel';
import type { JobItem } from '../api/jobsSocket';
import { listProjectDocuments, openProjectDocument, projectDocumentUrl, type CadProjectDocument } from '../api/cadProjects';

/** Optional CAD attachments decorate the shared run; their failure cannot hide it. */
export function RunModelDetails({ job }: { job: JobItem }) {
  const runModel = modelForRun(job);
  const currentName = useDocumentStore(state => job.design_uid && state.designUid === job.design_uid ? state.designName : null);
  const lineage = job.cad_source?.lineage_id ?? job.cad_state?.snapshot?.project_lineage_id;
  const hash = job.cad_source?.return_state_hash;
  const [state, setState] = useState<{ key: string; document: CadProjectDocument | null; error: string | null; launchDisabled?: boolean; fileManager?: string } | null>(null);
  const [action, setAction] = useState<{ key: string; pending: boolean; message: string; error: boolean } | null>(null);
  const key = `${lineage}:${hash}`;
  useEffect(() => {
    let current = true;
    if (!lineage || !hash) return;
    void listProjectDocuments(lineage).then(listing => {
      const items = Array.isArray(listing?.items) ? listing.items : [];
      if (current) setState({ key, document: items.find(item => item.returnStateHash === hash) ?? null, error: null,
        launchDisabled: listing.launchDisabled, fileManager: listing.fileManager });
    }, error => {
      if (current) setState({ key, document: null, error: String(error) });
    });
    return () => { current = false; };
  }, [lineage, hash, key]);
  const current = state?.key === key ? state : null;
  const currentAction = action?.key === key ? action : null;
  const openDocument = async () => {
    if (!lineage || !hash) return;
    setAction({ key, pending: true, message: '', error: false });
    try {
      const result = await openProjectDocument(lineage, hash);
      setAction(previous => previous?.key === key
        ? { key, pending: false, message: result.message, error: false } : previous);
    } catch (error) {
      setAction(previous => previous?.key === key
        ? { key, pending: false, message: error instanceof Error ? error.message : String(error), error: true } : previous);
    }
  };
  let attachment;
  if (!hash || !lineage) attachment = <p className="cad-model-missing" title="This run has no recorded model state to identify a Fusion file.">Model state not recorded · no Fusion file</p>;
  else if (!current) attachment = <p role="status">Loading model attachment…</p>;
  else if (current.error) attachment = <p className="cad-model-missing" role="status">Could not read the archived Fusion file: {current.error}</p>;
  else if (!current.document?.filename) attachment = <p className="cad-model-missing" title={current.document
    ? 'The record of this model survived but its Fusion file did not.'
    : 'No archived Fusion file is available. It may not have been captured, or may have been removed.'}>no Fusion file</p>;
  else attachment = <>
    <p><a className="cad-model-download" href={projectDocumentUrl(lineage, hash)}
      title="Download the Fusion model this run was solved from.">Fusion file</a>
      {' · '}<button type="button" disabled={currentAction?.pending} onClick={() => { void openDocument(); }}>
        {currentAction?.pending ? 'Opening…' : current.launchDisabled
          ? `Show in ${current.fileManager || 'file manager'}` : 'Open in Fusion'}
      </button>
      {current.document.capturedAt && <span> · Model captured {current.document.capturedAt}</span>}</p>
    <p>{current.launchDisabled ? 'Opening Fusion is disabled on this computer.'
      : 'Fusion imports this file as a new document; it does not reopen the cloud document.'}</p>
    {currentAction?.message && <p role={currentAction.error ? 'alert' : 'status'}>{currentAction.message}</p>}
  </>;
  const document = runDocumentName(job) || current?.document?.documentName || 'Not recorded';
  const modelHash = job.cad_source?.solve_model_sha256 || job.cad_source?.transformed_geometry_hash
    || hash || job.cad_source?.manifest_sha256 || job.cad_state?.snapshot?.manifest_sha256;
  const moveKey = job.archive_key || (lineage ? `lineage:${lineage}` : runModel.modelId?.startsWith('mesh-file:') ? `model:${runModel.modelId}`
    : job.design_uid ? `design:${job.design_uid}` : `name:${(job.design_name || job.label || document).normalize('NFKC').trim()}`);
  const moveName = currentName || job.design_name || runDocumentName(job) || job.label || document;
  return <section className="run-model-details" aria-label="Run model details">
    <dl>
      {job.archive_folder && <><dt>Folder:</dt><dd>{job.archive_folder}/
        {currentName && job.design_name && currentName !== job.design_name && <> (design now <em>{currentName}</em>)</>}
      </dd></>}
      <dt>Document</dt><dd>{document}</dd>
      <dt>Source</dt><dd>{runSourceLabel(job)}</dd>
      <dt>Model</dt><dd>{runModel.lineage || runModel.key || 'Not recorded'}</dd>
      <dt>Model hash</dt><dd>{modelHash || 'Not recorded'}</dd>
    </dl>
    {runSourceForJob(job) !== 'mesh-file' && attachment}
    <MoveRunsButton target={{ key: moveKey, name: moveName }}>
      {!job.archive_key ? `From an earlier version · Move into ${moveName}…` : 'Move earlier runs…'}
    </MoveRunsButton>
  </section>;

}
