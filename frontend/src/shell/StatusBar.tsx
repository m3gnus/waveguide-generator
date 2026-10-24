import { cadModelStatusLabel, useCadModelState } from '../stores/cadModelState';
import { resolveEngineLabel } from './solveProgress';
import { useSyncExternalStore } from 'react';
import { previewSocket } from '../api/previewSocket';
import { compactFrequency } from '../design/lambdaLimit';
import type { EngineCapability } from '../jobs/actions';
import { useCapabilities } from '../jobs/useCapabilities';
import { useSolveControlBackend } from '../jobs/useSolveControlBackend';
import { usePreferences, type Preferences } from '../prefs/preferences';
import { useMeshImportStore } from '../stores/meshImport';
import { useImportedModelStore } from '../stores/importedModel';
import { useCadReturnStore } from '../stores/cadReturn';
import { useDesignStore, type DesignDocument } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { designFileExtension } from '../stores/designName';
import { parseFrequencyList, type FrequencyMode } from '../stores/solveOptions';
import { useScopedSolveOptions } from '../stores/solveOptionsContext';
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
  resolvedEngine: string | null | undefined,
): string {
  if (!resolvedEngine) return 'Engine · —';
  const engine = engines.find((item) => item.name === resolvedEngine);
  return `${resolvedEngine.toUpperCase()} · ${engine?.version ?? 'READY'}`;
}

export function documentLabel(filename: string, family?: string): string {
  // The stored filename was derived when the name was set; the extension
  // follows the family on screen now (LOOKUP saves as .mwg, never .cfg).
  const named = family ? filename.replace(/\.(cfg|mwg)$/i, `.${designFileExtension(family)}`) : filename;
  return named.trim() || 'untitled design';
}

export function StatusBar() {
  const { engines } = useCapabilities();
  const preview = useSyncExternalStore(previewSocket.subscribe, previewSocket.getSnapshot, previewSocket.getSnapshot);
  const design = useDesignStore((state) => state.design);
  const mode = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot, workspaceModeStore.getSnapshot).mode;
  const file = useMeshImportStore();
  const openedSetup = useImportedModelStore((state) => state.input === 'file' ? state.setup : null);
  const fileRegistered = useImportedModelStore((state) => state.input === 'file' && state.prepared !== null);
  const fileActive = mode === 'cad' && workspaceModeStore.getSnapshot().importedInput === 'file';
  const cadReturn = useCadReturnStore();
  const cad = useCadModelState();
  const requestedEngine = useScopedSolveOptions(state => state.engine);
  const filename = useDocumentStore((state) => state.filename);
  const frequencyMode = useScopedSolveOptions((state) => state.frequencyMode);
  const frequencyListText = useScopedSolveOptions((state) => state.frequencyListText);
  const fileSweep = useScopedSolveOptions((state) => `${state.sweepStartHz}/${state.sweepEndHz}/${state.sweepCount}`);
  const preferences = usePreferences();
  const backend = useSolveControlBackend();
  const engineLabel = backend.plan?.engine ? engineStatusLabel(engines, backend.plan.engine) : resolveEngineLabel(requestedEngine);
  const previewError = preview.error ? previewErrorMessage(preview.error) : null;
  const meshMetrics = previewMeshMetrics(preview.frame);
  const [fileStart, fileEnd, fileCount] = fileSweep.split('/').map(Number);
  const listedFrequencies = frequencyMode === 'list' ? parseFrequencyList(frequencyListText).frequencies ?? undefined : undefined;
  const replayFrequencies = openedSetup?.options.frequencies_hz ?? listedFrequencies;
  const summary = fileActive
    ? `next mesh solve ${compactFrequency(replayFrequencies?.[0] ?? fileStart)} – ${compactFrequency(replayFrequencies?.at(-1) ?? fileEnd)} · ${replayFrequencies?.length ?? fileCount} f`
    : mode === 'cad'
    ? cadSolveSummary({ start: cadReturn.frequencyStartHz, end: cadReturn.frequencyEndHz, count: cadReturn.frequencyCount }, { frequencyMode, frequencyListText, smoothing: preferences.smoothing })
    : solveSummary(design, { frequencyMode, frequencyListText, smoothing: preferences.smoothing });
  const document = fileActive ? file.filename || 'no mesh file' : mode === 'cad'
    ? cad.selected ? cad.label : 'no CAD model'
    : documentLabel(filename, design.formula);
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
    <div className="status-item status-drop-2">{fileActive ? <>Mesh <b>{file.summary?.triangle_count.toLocaleString() ?? 'not imported'}</b> tri</> : mode === 'cad'
      ? cadModelStatusLabel(cad)
      : <>preview {meshMetrics ? <><b>{meshMetrics.triangles.toLocaleString()}</b> tri · <b>{meshMetrics.vertices.toLocaleString()}</b> vert</> : <b>no frame</b>}</>}</div>
    {mode === 'parametric' && previewError && <div className="status-item preview-error-status" title={previewError}>{previewError}</div>}
    <span className="spacer"/>
    <div className="status-item right">{summary}</div>
    <div className="status-item right status-drop-1"><Icon name="folder"/>{document}</div>
    {(fileActive || mode === 'parametric') && <div className="status-item right">{fileActive ? (fileRegistered ? 'Mesh · ready' : 'Mesh · not prepared') : <><i className={`connection-dot ${preview.connection}`}/>{preview.connection === 'connected' ? 'local · connected' : `local · ${preview.connection}`}</>}</div>}
  </footer>;
}
