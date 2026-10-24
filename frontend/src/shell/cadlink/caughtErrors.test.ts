import { afterEach, describe, expect, it, vi } from 'vitest';
import { CadLinkApiError } from '../../api/cadlink';
import * as diagnostics from '../../api/diagnostics';
import { resetClientErrorReporting } from '../clientErrors';
import { reportCadAutomationError } from './caughtErrors';

describe('reportCadAutomationError', () => {
  afterEach(() => { resetClientErrorReporting(); vi.restoreAllMocks(); });

  it('leaves a refusal the server answered to the server', () => {
    const report = vi.spyOn(diagnostics, 'reportClientError').mockResolvedValue(true);
    expect(reportCadAutomationError('prepare', new CadLinkApiError('Choose a frame', [], 422, 'frame'))).toBe(false);
    expect(report).not.toHaveBeenCalled();
  });

  it('reports server failures, transport failures and interface throws with the operation', () => {
    const report = vi.spyOn(diagnostics, 'reportClientError').mockResolvedValue(true);
    expect(reportCadAutomationError('prepare', new CadLinkApiError('Internal', [], 500))).toBe(true);
    expect(reportCadAutomationError('solver-mesh-display', new TypeError('bad mesh'), { ingest_id: 'i-1' })).toBe(true);
    expect(report).toHaveBeenCalledWith(expect.objectContaining({
      operation: 'cad-solver-mesh-display', context: { ingest_id: 'i-1' }, message: 'cad-solver-mesh-display: bad mesh',
    }));
  });
});
