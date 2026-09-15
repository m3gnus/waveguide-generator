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
  className?: string;
}

/** The immutable identities behind one backend-owned CAD solve. */
export function CadSolveInputs({
  operationId,
  operation: suppliedOperation,
  resolvedEngine,
  engineSource,
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
    </dl>
    {(operationError || setupError) && <span className="cad-solve-inputs-error">
      Could not read all bound inputs: {operationError ?? setupError}
    </span>}
  </details>;
}
