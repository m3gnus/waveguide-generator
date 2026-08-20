import { useSyncExternalStore } from 'react';
import { previewSocket } from '../api/previewSocket';
import { compactFrequency } from '../design/lambdaLimit';
import { resolveEngine, type EngineCapability } from '../jobs/actions';
import { useCapabilities } from '../jobs/useCapabilities';
import { usePreferences, type Preferences } from '../prefs/preferences';
import { useCadReturnStore } from '../stores/cadReturn';
import { useDesignStore, type DesignDocument } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { parseFrequencyList, useSolveOptionsStore, type FrequencyMode } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { previewErrorMessage, previewMeshMetrics } from '../viewport/presentation';
import { Icon } from './icons';

interface SolveSummaryOptions {
  frequencyMode: FrequencyMode;
  frequencyListText: string;
  smoothing: Preferences['smoothing'];
}

/**
 * The sweep the NEXT solve will use.
 *
 * Named, because the Summary card reports the sweep the run currently on screen
 * already used, and the two are routinely different — the rail read
 * `400 Hz – 16 kHz · 20 f` while the card read `50 Hz – 20.0 kHz · 40 f`, with
 * nothing on either saying which was which.
 */
export function solveSummary(design: DesignDocument, options: Partial<SolveSummaryOptions> = {}): string {
  const frequencyMode = options.frequencyMode ?? 'range';
  let frequencies = `next solve ${compactFrequency(design.simulation.f1)} – ${compactFrequency(design.simulation.f2)} · ${design.simulation.num_frequencies} f`;
  if (frequencyMode === 'list') {
    const parsed = parseFrequencyList(options.frequencyListText ?? '');
    if (parsed.frequencies) {
      const first = compactFrequency(parsed.frequencies[0]);
      const last = compactFrequency(parsed.frequencies.at(-1)!);
      frequencies = `next solve ${first}${parsed.frequencies.length > 1 ? ` – ${last}` : ''} · ${parsed.frequencies.length} f`;
      // A broken list is a fault, not a plan: it does not get the "next solve"
      // framing, because there is no next solve until it is fixed.
    } else frequencies = 'invalid frequency list';
  }
  return `${frequencies} · smoothing ${options.smoothing ?? 'none'}`;
}

export function cadSolveSummary(
  range: { start: number; end: number; count: number },
  options: Partial<SolveSummaryOptions> = {},
): string {
  const frequencyMode = options.frequencyMode ?? 'range';
  let frequencies = `next CAD solve ${compactFrequency(range.start)} – ${compactFrequency(range.end)} · ${range.count} f`;
  if (frequencyMode === 'list') {
    const parsed = parseFrequencyList(options.frequencyListText ?? '');
    if (parsed.frequencies) {
      const first = compactFrequency(parsed.frequencies[0]);
      const last = compactFrequency(parsed.frequencies.at(-1)!);
      frequencies = `next CAD solve ${first}${parsed.frequencies.length > 1 ? ` – ${last}` : ''} · ${parsed.frequencies.length} f`;
    } else frequencies = 'invalid CAD frequency list';
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
  const mode = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot, workspaceModeStore.getSnapshot).mode;
  const cadReturn = useCadReturnStore();
  const filename = useDocumentStore((state) => state.filename);
  const selectedEngine = useSolveOptionsStore((state) => state.engine);
  const solverMode = useSolveOptionsStore((state) => state.solverMode);
  const frequencyMode = useSolveOptionsStore((state) => state.frequencyMode);
  const frequencyListText = useSolveOptionsStore((state) => state.frequencyListText);
  const preferences = usePreferences();
  const engineLabel = engineStatusLabel(engines, mode === 'cad' ? 'metal' : selectedEngine, mode === 'cad' ? 'full_3d' : solverMode, isLoading);
  const previewError = preview.error ? previewErrorMessage(preview.error) : null;
  const meshMetrics = previewMeshMetrics(preview.frame);
  const cadTriangles = cadReturn.ingestRecord?.mesh?.stats.triangle_count;
  const summary = mode === 'cad'
    ? cadSolveSummary({ start: cadReturn.frequencyStartHz, end: cadReturn.frequencyEndHz, count: cadReturn.frequencyCount }, { frequencyMode, frequencyListText, smoothing: preferences.smoothing })
    : solveSummary(design, { frequencyMode, frequencyListText, smoothing: preferences.smoothing });
  const document = mode === 'cad'
    ? cadReturn.selectedBundle?.documentName ?? cadReturn.selectedBundle?.name ?? 'no CAD return'
    : documentLabel(filename);
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
    <div className="status-item status-drop-2">{mode === 'cad'
      ? <>CAD mesh {cadTriangles === undefined ? <b>not ingested</b> : <><b>{cadTriangles.toLocaleString()}</b> solved tri</>}</>
      : <>preview {meshMetrics ? <><b>{meshMetrics.triangles.toLocaleString()}</b> tri · <b>{meshMetrics.vertices.toLocaleString()}</b> vert</> : <b>no frame</b>}</>}</div>
    {mode === 'parametric' && previewError && <div className="status-item preview-error-status" title={previewError}>{previewError}</div>}
    <span className="spacer"/>
    <div className="status-item right">{summary}</div>
    <div className="status-item right status-drop-1"><Icon name="folder"/>{document}</div>
    <div className="status-item right"><i className={`connection-dot ${mode === 'cad' ? cadReturn.ingestRecord ? 'connected' : 'disconnected' : preview.connection}`}/>{mode === 'cad' ? cadReturn.ingestRecord ? 'CAD return · ingested' : 'CAD return · empty' : preview.connection === 'connected' ? 'local · connected' : `local · ${preview.connection}`}</div>
  </footer>;
}
