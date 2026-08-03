import { useSyncExternalStore } from 'react';
import { useQuery } from '@tanstack/react-query';
import { previewSocket } from '../api/previewSocket';
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

export function StatusBar() {
  const { data, isError } = useQuery({ queryKey: ['capabilities'], queryFn: getCapabilities, retry: 1, staleTime: 30_000 });
  const preview = useSyncExternalStore(previewSocket.subscribe, previewSocket.getSnapshot, previewSocket.getSnapshot);
  const engine = data?.engines.find((item) => item.available) ?? data?.engines[0];
  const engineLabel = engine ? `${engine.name.toUpperCase()} · ${engine.available ? engine.version ?? 'READY' : 'OFFLINE'}` : isError ? 'ENGINE OFFLINE' : 'ENGINE…';
  return <footer className="statusbar">
    <div className="status-item"><span className="engine-badge"><Icon name="chip"/>{engineLabel}</span></div>
    <div className="status-item">local engine · <b>{data?.engines.filter((item) => item.available).length ?? 0}</b> available</div>
    <div className="status-item"><b>48 312</b> el · <b>24 190</b> nodes · max edge <b>2.41 mm</b></div>
    <div className="status-item">λ/6 ok to <b>23.7 kHz</b></div>
    <span className="spacer"/>
    <div className="status-item right">200 Hz – 20 kHz · 320 f · 1/24 oct</div>
    <div className="status-item right"><Icon name="folder"/>~/HornLab/designs/tritonia</div>
    <div className="status-item right"><i className={`connection-dot ${preview.connection}`}/>{preview.connection === 'connected' ? 'local · connected' : `local · ${preview.connection}`}</div>
  </footer>;
}
