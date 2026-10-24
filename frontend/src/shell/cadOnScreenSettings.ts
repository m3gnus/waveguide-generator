import { type CadOperationSummary } from '../api/cadOperations';

/** What the Solve card says while the backend prepares a request for the model on screen. */
export function inFlightWords(operation: CadOperationSummary): string {
  return operation.operationId.startsWith('manual-solve:')
    ? 'Preparing your solve…'
    : 'Preparing the request from Fusion…';
}
