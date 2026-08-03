import { describe, expect, it } from 'vitest';
import { formatApiDetail, getCapabilities } from './actions';

describe('API validation errors', () => {
  it('formats structured FastAPI detail arrays with locations', async () => {
    const detail = [{ loc: ['body', 'design', 'simulation', 'f1'], msg: 'must be finite', type: 'value_error' }];
    expect(formatApiDetail(detail)).toBe('body.design.simulation.f1: must be finite');
    const fetcher = async () => new Response(JSON.stringify({ detail }), {
      status: 422, headers: { 'Content-Type': 'application/json' },
    });
    await expect(getCapabilities(fetcher as typeof fetch)).rejects.toThrow('body.design.simulation.f1: must be finite');
  });
});
