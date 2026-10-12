import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchFemField } from './femField';
import { femFieldFixture } from '../viewport/test-fixtures/femField';

afterEach(() => vi.restoreAllMocks());

describe('FEM result wire route', () => {
  it('requests one frequency/channel and decodes the F5 format', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(femFieldFixture(1000, 'rear'))));
    const controller = new AbortController();
    const field = await fetchFemField('run/id', 1000, 'rear', controller.signal);
    expect(fetchSpy).toHaveBeenCalledWith('/api/results/run%2Fid/fem-field?frequency=1000&channel=rear', { signal: controller.signal });
    expect(Array.from(field.values.real)).toEqual([1, 2, 3, 4]);
    expect(Array.from(field.values.imag)).toEqual([-1, -2, -3, -4]);
    expect(field.domain.surfaces[0].kind).toBe('rigid');
    expect(field.frequencyHz).toBe(1000);
  });

  it('reports an unavailable/flag-off field', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('', { status: 404 }));
    await expect(fetchFemField('ordinary')).rejects.toThrow('No retained FEM field');
  });

  it('refuses mismatched axes and non-finite decoded values', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    fetchSpy.mockResolvedValue(new Response(JSON.stringify({ ...femFieldFixture(), channel_id: 'absent' })));
    await expect(fetchFemField('one')).rejects.toThrow('selection metadata');
    fetchSpy.mockResolvedValue(new Response(JSON.stringify({ ...femFieldFixture(), pressure_real: btoa('\x00\x00\xc0\x7f'.repeat(4)) })));
    await expect(fetchFemField('one')).rejects.toThrow('finite');
  });
});
