import { describe, expect, it, vi } from 'vitest';
import { designForFamily, serializeDesign } from '../stores/design';
import { compareFreeformProfiles, FreeformRequestError, insertFreeformAnchor, queryFreeformCurves, StaleFreeformResponse } from './freeformProfile';
import { compareProfileSamples } from './profileSamples';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

describe('Custom profile curve client', () => {
  it('posts the serialized design with its key and accepts only the matching answer', async () => {
    const design = designForFamily('FREEFORM');
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(json({ content_key: 'k1', H: {}, V: {}, warnings: [] }));
    const controller = new AbortController();
    await expect(queryFreeformCurves(design, 'k1', controller.signal, fetcher)).resolves.toMatchObject({ content_key: 'k1' });
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe('/api/design/freeform/curves');
    expect(init?.signal).toBe(controller.signal);
    expect(JSON.parse(String(init?.body))).toEqual({ design: serializeDesign(design), content_key: 'k1' });

    fetcher.mockResolvedValueOnce(json({ content_key: 'other', H: {}, V: {}, warnings: [] }));
    await expect(queryFreeformCurves(design, 'k2', undefined, fetcher)).rejects.toBeInstanceOf(StaleFreeformResponse);
  });

  it('turns a keyed 422 into an actionable error and drops a refusal for another request', async () => {
    const design = designForFamily('FREEFORM');
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(json({ content_key: 'k1', detail: 'FREEFORM profileV insert z must be strictly inside a span, not on an anchor' }, 422))
      .mockResolvedValueOnce(json({ content_key: 'k1', detail: [{ loc: ['body', 'design', 'FREEFORM', 'profile_h'], msg: 'Value error, the first FREEFORM profile point must have t = 0', type: 'value_error' }] }, 422))
      .mockResolvedValueOnce(json({ content_key: 'old', detail: 'late' }, 422))
      .mockResolvedValueOnce(json({ content_key: null, detail: 'malformed' }, 422));
    const insert = insertFreeformAnchor(design, { plane: 'V', z_mm: 0, mode: 'exact' }, 'k1', undefined, fetcher);
    await expect(insert).rejects.toThrow('profileV insert z must be strictly inside a span');
    await expect(queryFreeformCurves(design, 'k1', undefined, fetcher)).rejects.toThrow('design.FREEFORM.profile_h: Value error, the first FREEFORM profile point must have t = 0');
    await expect(queryFreeformCurves(design, 'k1', undefined, fetcher)).rejects.toBeInstanceOf(StaleFreeformResponse);
    const malformed = queryFreeformCurves(design, 'k1', undefined, fetcher);
    await expect(malformed).rejects.toBeInstanceOf(FreeformRequestError);
    await expect(malformed).rejects.toThrow('malformed');
  });

  it('hydrates the inserted design and sends the edit fields', async () => {
    const design = designForFamily('FREEFORM');
    const returned = structuredClone(serializeDesign(design)) as Record<string, unknown> & { profile_h: { points: unknown[] } };
    returned.profile_h.points.splice(1, 0, { t: .25, r: 40, join: 'smooth', handle_in: [-1, -2], handle_out: [3, 4], angle_deg: null });
    const deviation = { extents: {}, method: {}, sample_count: 3, curve_deviation_mm: 0, curve_at_z_mm: 30, evaluator_deviation_mm: 1e-12, evaluator_at_z_mm: 30 };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(json({ content_key: 'k', design: returned, deviation }));
    const result = await insertFreeformAnchor(design, { plane: 'H', z_mm: 30, mode: 'exact' }, 'k', undefined, fetcher);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toMatchObject({ plane: 'H', z_mm: 30, mode: 'exact', content_key: 'k' });
    expect(result.design.profile_h!.points[1]).toMatchObject({ t: .25, r: 40, handle_in: [-1, -2], handle_out: [3, 4] });
    expect(result.deviation.sample_count).toBe(3);
  });

  it('compares before and after designs', async () => {
    const before = designForFamily('FREEFORM');
    const after = structuredClone(before); after.length = 130;
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(json({ content_key: 'c', H: {}, V: {} }));
    await compareFreeformProfiles(before, after, 'c', undefined, fetcher);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ before: serializeDesign(before), after: serializeDesign(after), content_key: 'c' });
  });
});

describe('generated-profile change', () => {
  const line = (points: [number, number][]) => points.map(([z, r]) => ({ z, r }));
  it('measures the largest radius change over the common range and names its limits', () => {
    const before = { H: line([[0, 10], [50, 20], [100, 30]]), V: line([[0, 10], [100, 30]]) };
    const after = { H: line([[0, 10], [50, 25], [100, 30]]), V: line([[0, 10], [100, 30]]) };
    const change = compareProfileSamples(before, after);
    expect(change.H).toMatchObject({ domain: 'full', maxChangeMm: 5, atZMm: 50, sampleCount: 3 });
    expect(change.V).toMatchObject({ domain: 'full', maxChangeMm: 0 });
  });

  it('reports overlap-only, no-overlap and unmeasurable planes honestly', () => {
    const before = { H: line([[0, 10], [100, 30]]), V: line([[0, 10], [100, 30]]) };
    const after = { H: line([[0, 10], [120, 34]]), V: line([[150, 10], [200, 30]]) };
    const change = compareProfileSamples(before, after);
    expect(change.H.domain).toBe('overlap_only');
    expect(change.H.extents).toEqual({ before: [0, 100], after: [0, 120] });
    expect(change.V).toMatchObject({ domain: 'no_overlap', maxChangeMm: null });
    const folded = compareProfileSamples(before, { H: line([[0, 10], [50, 20], [40, 30]]), V: before.V });
    expect(folded.H.domain).toBe('unavailable');
    expect(folded.H.reason).toMatch(/single-valued/);
    // A mouth-to-throat listing is compared along increasing z.
    expect(compareProfileSamples(before, { H: [...before.H].reverse(), V: before.V }).H).toMatchObject({ domain: 'full', maxChangeMm: 0 });
  });
});
