import { vi } from 'vitest';
import fixtures from '../../../shared/profile-inspection/mesher-profiles.json';

/* Test stand-in for the Custom profile curve service (server/preview/freeform.py) and the profile export. */

type Wire = Record<string, unknown>;
type WirePoint = { t: number | { value: number }; r: number | { value: number }; join?: string; angle_deg?: number | null; angle_out_deg?: number | null; handle_in?: [number, number] | null; handle_out?: [number, number] | null };
const scalar = (value: number | { value: number }) => typeof value === 'number' ? value : value.value;
const deviation = (curve: number) => ({
  extents: { before: [0, 120], after: [0, 120], extents_equal: true, overlap: [0, 120], comparison_domain: 'full' },
  method: { estimate: 'sampled estimate, not a certified maximum', uniform_samples: 16385, grid: 'uniform', peak_refinement: 'bounded' },
  sample_count: 16390, curve_deviation_mm: curve, curve_at_z_mm: 40, evaluator_deviation_mm: curve, evaluator_at_z_mm: 40,
});

/** A small stand-in for the curve service and the profile export, keyed like the real server. */
export function stubProfileServer(options: { refuse?: string } = {}) {
  const csv = fixtures[0].csv;
  const plane = (design: Wire, key: 'profile_h' | 'profile_v') => {
    const length = scalar(design.length as number);
    const points = (design[design.shared_profile && key === 'profile_v' ? 'profile_h' : key] as { points: WirePoint[] }).points;
    return {
      anchors: points.map((point) => ({
        t: scalar(point.t), z: scalar(point.t) * length, r: scalar(point.r), join: point.join ?? 'smooth',
        explicitIn: point.handle_in != null, explicitOut: point.handle_out != null, handleIn: point.handle_in ?? null, handleOut: point.handle_out ?? null,
        angleInDeg: point.angle_deg ?? 20, angleOutDeg: point.angle_out_deg ?? point.angle_deg ?? 20,
      })),
      spans: points.slice(1).map((point, index) => ({ straight: point.join === 'corner' && points[index].join === 'corner', bezier: [] })),
      samples: [], inflections: [],
    };
  };
  const fetcher = vi.fn<typeof fetch>(async (url, init) => {
    const path = String(url);
    if (path.startsWith('/api/export/profiles')) return new Response(csv);
    const body = JSON.parse(String(init?.body)) as Wire;
    const key = body.content_key;
    if (path.endsWith('/curves')) {
      if (options.refuse) return new Response(JSON.stringify({ content_key: key, detail: options.refuse }), { status: 422 });
      const design = body.design as Wire;
      return new Response(JSON.stringify({ content_key: key, H: plane(design, 'profile_h'), V: plane(design, 'profile_v'), warnings: [] }));
    }
    if (path.endsWith('/compare')) return new Response(JSON.stringify({ content_key: key, H: deviation(.125), V: deviation(0) }));
    if (path.endsWith('/insert')) {
      const design = structuredClone(body.design) as Wire;
      const field = body.plane === 'V' ? 'profile_v' : 'profile_h';
      const points = (design[field] as { points: WirePoint[] }).points;
      const length = scalar(design.length as number);
      const t = (body.z_mm as number) / length;
      const index = points.findIndex((point) => scalar(point.t) > t);
      const exact = body.mode === 'exact';
      points.splice(index, 0, { t, r: 50, join: 'smooth', ...(exact ? { handle_in: [-5, -2], handle_out: [5, 2] } : {}) });
      if (!exact) points.forEach((point) => { delete point.handle_in; delete point.handle_out; });
      return new Response(JSON.stringify({ content_key: key, design, deviation: deviation(0) }));
    }
    throw new Error(`unexpected request ${path}`);
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
