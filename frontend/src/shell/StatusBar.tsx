import { useSyncExternalStore } from 'react';
import { useQuery } from '@tanstack/react-query';
import { previewSocket } from '../api/previewSocket';
import { useDesignStore, type DesignDocument } from '../stores/design';
import { previewErrorMessage } from '../viewport/presentation';
import { Icon } from './icons';

interface Engine {
  name: string;
  available: boolean;
  reason: string | null;
  version: string | null;
}

interface Capabilities { engines: Engine[] }

async function getCapabilities(): Promise<Capabilities> {
  const response = await fetch('/api/capabilities');
  if (!response.ok) throw new Error(`Capabilities request failed: ${response.status}`);
  return response.json() as Promise<Capabilities>;
}

function compactFrequency(value: number): string {
  return value >= 1_000 ? `${Number((value / 1_000).toPrecision(3))} kHz` : `${value} Hz`;
}

export function solveSummary(design: DesignDocument): string {
  return `${compactFrequency(design.simulation.f1)} – ${compactFrequency(design.simulation.f2)} · ${design.simulation.num_frequencies} f · smoothing not configured`;
}

export function StatusBar() {
  const { data, isError } = useQuery({ queryKey: ['capabilities'], queryFn: getCapabilities, retry: 1, staleTime: 30_000 });
  const preview = useSyncExternalStore(previewSocket.subscribe, previewSocket.getSnapshot, previewSocket.getSnapshot);
  const design = useDesignStore((state) => state.design);
  const engine = data?.engines.find((item) => item.available) ?? data?.engines[0];
  const engineLabel = engine ? `${engine.name.toUpperCase()} · ${engine.available ? engine.version ?? 'READY' : 'OFFLINE'}` : isError ? 'ENGINE OFFLINE' : 'ENGINE…';
  const previewError = preview.error ? previewErrorMessage(preview.error) : null;
  return <footer className="statusbar">
    <div className="status-item"><span className="engine-badge"><Icon name="chip"/>{engineLabel}</span></div>
    <div className="status-item">local engine · <b>{data?.engines.filter((item) => item.available).length ?? 0}</b> available</div>
    <div className="status-item">preview mesh metrics <b>unavailable</b></div>
    <div className="status-item">λ/6 limit <b>not calculated</b></div>
    {previewError && <div className="status-item preview-error-status" title={previewError}>{previewError}</div>}
    <span className="spacer"/>
    <div className="status-item right">{solveSummary(design)}</div>
    <div className="status-item right"><Icon name="folder"/>design path not selected</div>
    <div className="status-item right"><i className={`connection-dot ${preview.connection}`}/>{preview.connection === 'connected' ? 'local · connected' : `local · ${preview.connection}`}</div>
  </footer>;
}
