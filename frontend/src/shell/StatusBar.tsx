import { useSyncExternalStore } from 'react';
import { previewSocket } from '../api/previewSocket';
import { resolveEngine, type EngineCapability } from '../jobs/actions';
import { useCapabilities } from '../jobs/useCapabilities';
import { usePreferences, type Preferences } from '../prefs/preferences';
import { useDesignStore, type DesignDocument } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { parseFrequencyList, useSolveOptionsStore, type FrequencyMode } from '../stores/solveOptions';
import { previewErrorMessage, previewMeshMetrics } from '../viewport/presentation';
import { Icon } from './icons';

function compactFrequency(value: number): string {
  return value >= 1_000 ? `${Number((value / 1_000).toPrecision(3))} kHz` : `${value} Hz`;
}

interface SolveSummaryOptions {
  frequencyMode: FrequencyMode;
  frequencyListText: string;
  smoothing: Preferences['smoothing'];
}

export function solveSummary(design: DesignDocument, options: Partial<SolveSummaryOptions> = {}): string {
  const frequencyMode = options.frequencyMode ?? 'range';
  let frequencies = `${compactFrequency(design.simulation.f1)} – ${compactFrequency(design.simulation.f2)} · ${design.simulation.num_frequencies} f`;
  if (frequencyMode === 'list') {
    const parsed = parseFrequencyList(options.frequencyListText ?? '');
    if (parsed.frequencies) {
      const first = compactFrequency(parsed.frequencies[0]);
      const last = compactFrequency(parsed.frequencies.at(-1)!);
      frequencies = `${first}${parsed.frequencies.length > 1 ? ` – ${last}` : ''} · ${parsed.frequencies.length} f`;
    } else frequencies = 'invalid frequency list';
  }
  return `${frequencies} · smoothing ${options.smoothing ?? 'none'}`;
}

export function engineStatusLabel(
  engines: readonly EngineCapability[],
  selectedEngine: string,
  solverMode: string,
  isLoading = false,
): string {
  let effectiveEngine = selectedEngine.toLowerCase();
  try { effectiveEngine = resolveEngine(selectedEngine, { engines }, solverMode); } catch { /* reported as offline below */ }
  const engine = engines.find((item) => item.name.toLowerCase() === effectiveEngine);
  if (engine) return `${engine.name.toUpperCase()} · ${engine.available ? engine.version ?? 'READY' : 'OFFLINE'}`;
  return isLoading ? `${effectiveEngine.toUpperCase()}…` : `${effectiveEngine.toUpperCase()} · OFFLINE`;
}

export function documentLabel(filename: string): string {
  return filename.trim() || 'untitled design';
}

export function StatusBar() {
  const { engines, isLoading } = useCapabilities();
  const preview = useSyncExternalStore(previewSocket.subscribe, previewSocket.getSnapshot, previewSocket.getSnapshot);
  const design = useDesignStore((state) => state.design);
  const filename = useDocumentStore((state) => state.filename);
  const selectedEngine = useSolveOptionsStore((state) => state.engine);
  const frequencyMode = useSolveOptionsStore((state) => state.frequencyMode);
  const frequencyListText = useSolveOptionsStore((state) => state.frequencyListText);
  const preferences = usePreferences();
  const engineLabel = engineStatusLabel(engines, selectedEngine, design.simulation.solver_mode, isLoading);
  const previewError = preview.error ? previewErrorMessage(preview.error) : null;
  const meshMetrics = previewMeshMetrics(preview.frame);
  return <footer className="statusbar">
    <div className="status-item"><span className="engine-badge"><Icon name="chip"/>{engineLabel}</span></div>
    <div className="status-item status-drop-1">local engine · <b>{engines.filter((item) => item.available).length}</b> available</div>
    {/* Reports the frame actually on screen. This slot previously read
        "preview mesh metrics unavailable" as a hardcoded string, and the slot
        beside it "λ/6 limit not calculated" -- two permanent placeholders in
        the one rail whose whole job is to say what the system currently knows.
        The mesh metrics are recovered here from the decoded frame; the λ/6
        limit slot is gone until something computes it, because a readout that
        can only ever say "not calculated" is worse than no readout. */}
    <div className="status-item status-drop-2">preview {meshMetrics
      ? <><b>{meshMetrics.triangles.toLocaleString()}</b> tri · <b>{meshMetrics.vertices.toLocaleString()}</b> vert</>
      : <b>no frame</b>}</div>
    {previewError && <div className="status-item preview-error-status" title={previewError}>{previewError}</div>}
    <span className="spacer"/>
    <div className="status-item right">{solveSummary(design, { frequencyMode, frequencyListText, smoothing: preferences.smoothing })}</div>
    <div className="status-item right status-drop-1"><Icon name="folder"/>{documentLabel(filename)}</div>
    <div className="status-item right"><i className={`connection-dot ${preview.connection}`}/>{preview.connection === 'connected' ? 'local · connected' : `local · ${preview.connection}`}</div>
  </footer>;
}
