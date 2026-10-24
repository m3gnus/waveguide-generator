import { CadLinkApiError } from '../../api/cadlink';
import { reportCaughtError } from '../clientErrors';

/**
 * A caught CAD automation error, for the developers; what the user sees is unchanged.
 *
 * A structured refusal the server answered (a 4xx) is the server's to record and
 * is a fact about the model, not a defect: it is not reported again. Everything
 * else -- a 5xx, a transport failure, a mesh that would not parse, a throw in
 * the interface's own code -- goes to the client diagnostics collector with its
 * stack and the operation it interrupted.
 */
export function reportCadAutomationError(
  operation: string,
  error: unknown,
  context?: Record<string, string | number | boolean | null>,
): boolean {
  if (error instanceof CadLinkApiError && error.status !== null && error.status >= 400 && error.status < 500) return false;
  return reportCaughtError(`cad-${operation}`, error, context);
}
