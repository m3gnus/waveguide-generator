import { useEffect, useState } from 'react';
import {
  getCadOperation,
  getSetupRevision,
  type CadOperationDetail,
  type CadOperationSummary,
  type SetupRevisionDetail,
} from '../api/cadOperations';

interface LoadState<T> {
  key: string;
  value: T | null;
  error: string | null;
}

const EMPTY_LOAD = { key: '', value: null, error: null };

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

/** What a run made with WG's default settings says about them: the
 * backend's words once it has solved, "Using ..." before that or after a
 * failure, so nothing claims a solve that has not happened. */
export function defaultSettingsNote(message: string | null, solved: boolean): string {
  return solved && message ? message : USING_DEFAULT_SETTINGS;
}

/** What a run whose axis WG chose itself says about it: one sentence for the
 * card and the run details. "Solved" only once the run completed. */
export function automaticAxisNote(axis: string, jobStatus: string | undefined): string {
  if (jobStatus === 'complete') return `Solved along the automatic axis ${axis}`;
  // A run that failed or was cancelled solved nothing: say only which axis it used.
  if (jobStatus === 'error' || jobStatus === 'cancelled') return `Along the automatic axis ${axis}`;
  return `Solving along the automatic axis ${axis}`;
}

export const USING_DEFAULT_SETTINGS = "Using WG's default settings \u2014 change them in WG.";

function setupEngine(detail: SetupRevisionDetail | null): string | null {
  const setup = detail?.setup as { options?: { engine?: unknown } } | undefined;
  const engine = setup?.options?.engine;
  return typeof engine === 'string' && engine.trim() ? engine : null;
}

export interface CadSolveInputsProps {
  operationId: string;
  /** Present on the live-operation surface; job history resolves it by id. */
  operation?: CadOperationSummary;
  /** A job's persisted engine is the resolved engine that actually ran. */
  resolvedEngine?: string | null;
  /** Pending operations bind the setup request; historical jobs name what ran. */
  engineSource: 'setup-revision' | 'job';
  /** The run's own status, when this is a run's detail: "Solved with WG's
   * default settings" is said only of a run that completed. */
  jobStatus?: string;
  className?: string;
}

/** The immutable identities behind one backend-owned CAD solve. */
export function CadSolveInputs({
  operationId,
  operation: suppliedOperation,
  resolvedEngine,
  engineSource,
  jobStatus,
  className,
}: CadSolveInputsProps) {
  const [operationLoad, setOperationLoad] = useState<LoadState<CadOperationDetail>>(EMPTY_LOAD);
  useEffect(() => {
    if (suppliedOperation) return undefined;
    let current = true;
    setOperationLoad({ key: operationId, value: null, error: null });
    void getCadOperation(operationId).then(
      (value) => { if (current) setOperationLoad({ key: operationId, value, error: null }); },
      (reason: unknown) => { if (current) setOperationLoad({ key: operationId, value: null, error: message(reason) }); },
    );
    return () => { current = false; };
  }, [operationId, suppliedOperation]);

  const loadedOperation = operationLoad.key === operationId ? operationLoad.value : null;
  const operation = suppliedOperation ?? loadedOperation;
  const setupRevisionId = operation?.setupRevisionId ?? null;
  const persistedEngine = engineSource === 'job'
    && typeof resolvedEngine === 'string' && resolvedEngine.trim()
    ? resolvedEngine
    : null;
  const [setupLoad, setSetupLoad] = useState<LoadState<SetupRevisionDetail>>(EMPTY_LOAD);
  useEffect(() => {
    if (engineSource !== 'setup-revision' || !setupRevisionId) return undefined;
    let current = true;
    setSetupLoad({ key: setupRevisionId, value: null, error: null });
    void getSetupRevision(setupRevisionId).then(
      (value) => { if (current) setSetupLoad({ key: setupRevisionId, value, error: null }); },
      (reason: unknown) => { if (current) setSetupLoad({ key: setupRevisionId, value: null, error: message(reason) }); },
    );
    return () => { current = false; };
  }, [engineSource, setupRevisionId]);

  const setup = setupRevisionId && setupLoad.key === setupRevisionId ? setupLoad.value : null;
  const engine = engineSource === 'job' ? persistedEngine : setupEngine(setup);
  const operationError = !suppliedOperation && operationLoad.key === operationId
    ? operationLoad.error
    : null;
  const setupError = engineSource === 'setup-revision' && setupRevisionId && setupLoad.key === setupRevisionId
    ? setupLoad.error
    : null;
  const loadingOperation = !operation && !operationError;
  const loadingEngine = Boolean(operation && engineSource === 'setup-revision'
    && setupRevisionId && !engine && !setupError);
  const manifest = operation?.snapshot?.manifestSha256 ?? null;

  return <details className={['cad-solve-inputs', className].filter(Boolean).join(' ')}>
    <summary>Solve inputs{engine ? ` · ${engine}` : ''}</summary>
    <dl>
      <div><dt>Operation</dt><dd><code>{operationId}</code></dd></div>
      <div><dt>Snapshot</dt><dd>
        {operation?.snapshot?.documentName && <span>{operation.snapshot.documentName}</span>}
        {manifest ? <code>{manifest}</code> : loadingOperation ? 'reading…' : 'not recorded'}
      </dd></div>
      <div><dt>Preparation</dt><dd><code>{operation?.preparationId ?? (loadingOperation ? 'reading…' : 'not recorded')}</code></dd></div>
      <div><dt>Setup revision</dt><dd><code>{setupRevisionId ?? (loadingOperation ? 'reading…' : 'not recorded')}</code></dd></div>
      <div><dt>Engine</dt><dd><code>{engine ?? (loadingOperation || loadingEngine ? 'reading…' : 'not recorded')}</code></dd></div>
      {/* The protocol's own words for where this operation stands, and when it
          got there. The card above says it in the user's terms; PLAN A6 keeps
          the codes and the timings here, one disclosure away, so the primary
          path never has to spell out an internal state name. */}
      <div><dt>State</dt><dd>{operation?.state ?? (loadingOperation ? 'reading…' : 'not recorded')}</dd></div>
      <div><dt>Stage</dt><dd>{operation ? operation.stage ?? 'not recorded' : loadingOperation ? 'reading…' : 'not recorded'}</dd></div>
      <div><dt>Reason</dt><dd>{operation ? operation.reason ?? 'none' : loadingOperation ? 'reading…' : 'not recorded'}</dd></div>
      <div><dt>Received</dt><dd>{operation?.createdAt ?? (loadingOperation ? 'reading…' : 'not recorded')}</dd></div>
      <div><dt>Last moved</dt><dd>{operation?.updatedAt ?? (loadingOperation ? 'reading…' : 'not recorded')}</dd></div>
    </dl>
    {/* Solved with WG's default settings: said plainly, not as a report. */}
    {operation?.setupDefaults
      && <p className="cad-solve-inputs-defaults" data-setup-defaults="true">
        {defaultSettingsNote(operation.message, jobStatus === 'complete')}
      </p>}
    {operation?.frameAxisAutomatic
      && <p className="cad-solve-inputs-frame" data-frame-axis-automatic={operation.frameAxisAutomatic}>
        {automaticAxisNote(operation.frameAxisAutomatic, jobStatus)}. Change it in the CAD Link panel.
      </p>}
    {/* Verbatim, because it is evidence: whatever the adapter or the
        preparation reported is what a second report has to be compared with. */}
    {operation?.message && !operation.setupDefaults
      && <p className="cad-solve-inputs-reported">Reported · {operation.message}</p>}
    {(operationError || setupError) && <span className="cad-solve-inputs-error">
      Could not read all bound inputs: {operationError ?? setupError}
    </span>}
  </details>;
}
