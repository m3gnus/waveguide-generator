import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import type { EChartsOption } from 'echarts';
import { inspectProfileSamples } from '../api/designIo';
import type { ProfileSamples } from '../api/profileSamples';
import { EChart, useChartTokens, type ChartTokens } from '../results/EChart';
import { axes } from '../results/chartAxes';
import { useDesignStore, type DesignDocument } from '../stores/design';
import { SelectRow } from './SelectRow';
import { parameterPanelValidity } from './parameterPanelValidity';

type Plane = 'both' | 'H' | 'V';

export function equalProfiles(samples: ProfileSamples): boolean {
  return samples.H.length === samples.V.length && samples.H.every((point, index) =>
    Math.abs(point.z - samples.V[index].z) < 1e-5 && Math.abs(point.r - samples.V[index].r) < 1e-5);
}

/** Fixed physical extents and pixel-sized grid: ECharts must not stretch either axis. */
export function profileChartOption(samples: ProfileSamples, plane: Plane, width: number, height: number, tokens: ChartTokens): EChartsOption {
  const points = [...samples.H, ...samples.V];
  const zMin = Math.min(0, ...points.map((point) => point.z));
  const zMax = Math.max(zMin + 1, ...points.map((point) => point.z));
  const rMax = Math.max(1, ...points.map((point) => point.r));
  const pixelsPerMm = Math.min(Math.max(1, width - 80) / (zMax - zMin), Math.max(1, height - 75) / rMax);
  const equal = equalProfiles(samples);
  const planes: ('H' | 'V')[] = plane === 'both' ? equal ? ['H'] : ['H', 'V'] : [plane];
  return {
    animation: false,
    grid: { left: 55, top: 30, width: (zMax - zMin) * pixelsPerMm, height: rMax * pixelsPerMm, containLabel: false },
    // Top right, clear of both axis names: the r label sits top left and the z label below the axis.
    legend: { top: 0, right: 0, textStyle: { color: tokens.foreground } },
    tooltip: { trigger: 'axis', valueFormatter: (value) => `${Number(value).toFixed(3)} mm` },
    xAxis: { type: 'value', name: 'z (mm)', nameLocation: 'middle', nameGap: 25, min: zMin, max: zMax, ...axes(tokens), nameTextStyle: { color: tokens.foreground } },
    yAxis: { type: 'value', name: 'r (mm)', min: 0, max: rMax, ...axes(tokens), nameTextStyle: { color: tokens.foreground } },
    series: planes.map((axis) => ({
      type: 'line', name: equal && plane === 'both' ? 'H = V' : axis,
      data: samples[axis].map(({ z, r }) => [z, r]),
      showSymbol: false, smooth: false, lineStyle: { color: tokens.series[axis === 'H' ? 0 : 1], width: 2 },
      itemStyle: { color: tokens.series[axis === 'H' ? 0 : 1] },
    })),
  };
}

interface Props {
  design: DesignDocument;
  identity: number | string;
  revision: number;
  invalidDraft?: boolean;
  /** Also guards the interval between a document mutation and React rendering it. */
  isCurrent?: () => boolean;
}

export function ProfileInspection({ design, identity, revision, invalidDraft = false, isCurrent }: Props) {
  const key = JSON.stringify([identity, revision]);
  const current = useRef(key);
  current.current = key;
  const [result, setResult] = useState<{ key: string; identity: Props['identity']; samples: ProfileSamples }>();
  const [failure, setFailure] = useState<{ key: string; message: string }>();
  const [plane, setPlane] = useState<Plane>('both');
  const host = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(320);
  const tokens = useChartTokens();
  useEffect(() => {
    if (!host.current || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(host.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const accepted = () => !controller.signal.aborted && current.current === key && (isCurrent?.() ?? true);
    const timer = setTimeout(() => {
      void inspectProfileSamples(design, revision, controller.signal).then((samples) => {
        if (accepted()) { setResult({ key, identity, samples }); setFailure(undefined); }
      }).catch((reason: unknown) => {
        if (accepted()) setFailure({ key, message: reason instanceof Error ? reason.message : String(reason) });
      });
    }, 300);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [design, identity, revision, key, isCurrent]);
  const visible = result?.identity === identity ? result : undefined;
  const error = failure?.key === key ? failure.message : undefined;
  const stale = visible?.key !== key || invalidDraft || Boolean(error);
  const option = useMemo(() => visible ? profileChartOption(visible.samples, plane, width, 260, tokens) : null, [visible, plane, width, tokens]);
  return <div ref={host} className="profile-inspection" role="group" aria-label="Generated bore profile">
    {design.scale !== 1 && <p>Plot in scaled mm (Scale = {design.scale}).</p>}
    <SelectRow text="Choose which generated profile planes to inspect."><label htmlFor="freeform-profile-planes">Show</label><select id="freeform-profile-planes" aria-label="Profile planes" value={plane} onChange={(event) => setPlane(event.target.value as Plane)}>
      <option value="both">H and V</option><option value="H">Horizontal (H)</option><option value="V">Vertical (V)</option>
    </select></SelectRow>
    <div role="status" aria-live="polite">{invalidDraft ? `Invalid input${visible ? ' — last valid profile' : ''}. Correct the marked values.`
      : error ? `Invalid: ${error}${visible ? ' — last valid profile' : ''}`
        : visible?.key !== key ? `Updating…${visible ? ' — last valid profile' : ''}`
          : ''}</div>
    {option && <div className="profile-inspection-chart" data-stale={stale || undefined} style={{ height: 260, opacity: stale ? .45 : 1 }}>
      <EChart option={option} label={`Generated bore profile${stale ? ' — last valid' : ''}`} live />
    </div>}
  </div>;
}

export function FreeformProfileInspection() {
  const design = useDesignStore((state) => state.design);
  const revision = useDesignStore((state) => state.designRevision);
  const identity = useDesignStore((state) => state.loadGeneration);
  const invalidDraft = useSyncExternalStore(parameterPanelValidity.subscribe, parameterPanelValidity.getSnapshot, parameterPanelValidity.getSnapshot);
  const isCurrent = useCallback(() => {
    const state = useDesignStore.getState();
    return state.loadGeneration === identity && state.designRevision === revision;
  }, [identity, revision]);
  return <ProfileInspection design={design} identity={identity} revision={revision} invalidDraft={invalidDraft} isCurrent={isCurrent} />;
}
