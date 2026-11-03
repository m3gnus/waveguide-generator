import { stagedActivity } from '../shell/stagedActivity';
/**
 * The design assembly's background build: one captured revision at a time.
 *
 * A request captures the design revision and content on screen. The server
 * publishes a build only while its revision is still the newest one asked for
 * that design; here, too, an answer for an older revision, or for a document
 * that has since been replaced, is dropped. The last valid compile is kept
 * while a newer one builds or is refused, so a preview can show it with a
 * label, and every consumer can tell whether it is the design on screen.
 */
import { create } from 'zustand';
import { assemblyProblemText, compileAssembly, type AssemblyCompileResult, type CompiledAssembly } from '../api/assembly';
import { currentDocumentLoad, isCurrentDocumentLoad, serializeDesign, subscribeRevision, useDesignStore, type DesignDocument } from './design';
import { designContentKey } from './designContentKey';
import { useDocumentStore } from './document';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { adoptDesignBuild, designPreparedRecord, resetDesignModel, useDesignModelStore, type ImportedModelState } from './importedModel';
import { assemblyMeshScene } from '../viewport/solverMeshScene';

export interface AssemblyBuildState {
  status: 'idle' | 'building' | 'published' | 'refused';
  requested: { revision: number; contentKey: string | null; load: number } | null;
  refusal: string | null;
  /** The original failure, for the developer disclosure only. */
  failureDetail: string | null;
}

const IDLE: AssemblyBuildState = { status: 'idle', requested: null, refusal: null, failureDetail: null };
/** This page's builds: the server orders revisions within one page and document only. */
const PAGE_SESSION = globalThis.crypto?.randomUUID?.() ?? `page-${Date.now()}-${Math.random()}`;

export const useAssemblyBuildStore = create<AssemblyBuildState>(() => ({ ...IDLE }));

/** What a build is keyed by: the portable design, without directivity or solve settings. */
export function assemblyContentKey(design: DesignDocument): string | null {
  return designContentKey(design, '', { polar: { config: null }, solve: null });
}

/** Whether the last valid compile is the design on screen. */
export function designModelCurrent(design: DesignDocument, slot: ImportedModelState = useDesignModelStore.getState()): boolean {
  const key = assemblyContentKey(design);
  return slot.phase === 'ready' && key !== null && slot.authoring?.contentKey === key
    && slot.authoring.load === currentDocumentLoad();
}

/** Select the geometry producer once for every design Mesh preparation. */
export function designMeshProducer(design: DesignDocument): 'horn' | 'wg-build' {
  return design.assembly ? 'wg-build' : 'horn';
}

/** The preview's label for the last valid compile, or null when it is current. */
export function assemblyPreviewLabel(build: Pick<AssemblyBuildState, 'status'>, slot: ImportedModelState, design: DesignDocument): string | null {
  if (!slot.prepared || designModelCurrent(design, slot)) return null;
  return build.status === 'building'
    ? 'Showing the last built assembly while this revision builds.'
    : 'Showing the last built assembly; it is from an earlier revision.';
}

/** The build in flight, so a second ask for the same revision waits for it instead of compiling again. */
let inFlight: { requested: NonNullable<AssemblyBuildState['requested']>; answer: Promise<AssemblyCompileResult | null> } | null = null;

/** Compile the assembly of the design on screen; resolves to what this request learned. */
export function requestAssemblyBuild(fetcher: typeof fetch = fetch): Promise<AssemblyCompileResult | null> {
  const { design, designRevision } = useDesignStore.getState();
  if (!design.assembly) {
    useAssemblyBuildStore.setState({ ...IDLE });
    return Promise.resolve(null);
  }
  const load = currentDocumentLoad();
  const asked = useAssemblyBuildStore.getState().requested;
  if (inFlight && inFlight.requested === asked && asked?.revision === designRevision && asked.load === load) return inFlight.answer;
  const requested = { revision: designRevision, contentKey: assemblyContentKey(design), load };
  const answer = compileRequested(design, requested, fetcher);
  inFlight = { requested, answer };
  const settle = () => { if (inFlight?.answer === answer) inFlight = null; };
  answer.then(settle, settle);
  return answer;
}

/** The build of the design on screen: the last one when it still matches, else a new or running one. */
export function prepareDesignModel(fetcher: typeof fetch = fetch): Promise<AssemblyCompileResult | null> {
  const state = useAssemblyBuildStore.getState();
  const { design } = useDesignStore.getState();
  const slot = useDesignModelStore.getState();
  const record = designPreparedRecord();
  if (record && slot.model && state.status !== 'building' && designModelCurrent(design, slot)) {
    installAssemblyScene(slot.model.display_name || 'Assembly', record);
    return Promise.resolve({ status: 'published', reused: true, model: slot.model, prepared: record });
  }
  return requestAssemblyBuild(fetcher);
}

async function compileRequested(design: DesignDocument, requested: NonNullable<AssemblyBuildState['requested']>,
  fetcher: typeof fetch): Promise<AssemblyCompileResult | null> {
  const { designUid, designName } = useDocumentStore.getState();
  const { revision: designRevision, load } = requested;
  const owns = () => {
    const current = useAssemblyBuildStore.getState().requested;
    return isCurrentDocumentLoad(load) && current?.revision === requested.revision && current.load === load;
  };
  try {
    const before = useAssemblyBuildStore.getState();
    const result = await stagedActivity({ kind: 'assembly-build', label: 'Building the mesh' }, fetcher, undefined, (fetcher, signal) => {
      useAssemblyBuildStore.setState({ status: 'building', requested, refusal: null, failureDetail: null });
      return compileAssembly({
        design: serializeDesign(design), design_uid: designUid ?? '', design_name: designName, design_revision: designRevision,
        build_session: `${PAGE_SESSION}:${load}`,
      }, fetcher, signal);
    }).catch(error => {
      if (error?.name !== 'AbortError') throw error;
      if (owns()) useAssemblyBuildStore.setState(before);
      return { status: 'superseded' as const };
    });
    if (!owns()) return { status: 'superseded' };
    if (result.status === 'published') {
      adoptDesignBuild({ record: result.prepared, model: result.model }, {
        designUid: designUid ?? null, revision: designRevision, load, contentKey: requested.contentKey,
      });
      installAssemblyScene(result.model.display_name || 'Assembly', result.prepared);
      useAssemblyBuildStore.setState({ status: 'published', refusal: null });
    }
    return result;
  } catch (error) {
    if (owns()) {
      useAssemblyBuildStore.setState({ status: 'refused', failureDetail: error instanceof Error ? error.message : String(error),
        refusal: assemblyBuildFailure(error) });
    }
    throw error;
  }
}

function installAssemblyScene(name: string, record: CompiledAssembly): void {
  importedMeshStore.ensureSolver({ producer: 'wg-build', key: record.ingest_id }, () => ({
    ...assemblyMeshScene(name, record, 'solver'), modelKey: `prepared:${record.ingest_id}`,
  }));
}

/** What a failed build tells the user. */
export function assemblyBuildFailure(error: unknown): string {
  return assemblyProblemText(error, 'WG could not build this assembly and has recorded the problem.');
}

/** New and Open start a document of their own: nothing built for the last one applies. */
export function resetAssemblyBuild(): void {
  useAssemblyBuildStore.setState({ ...IDLE });
}

subscribeRevision(event => {
  if (event.reason !== 'load') return;
  const ingestId = useDesignModelStore.getState().prepared?.ingest_id;
  resetAssemblyBuild();
  resetDesignModel();
  if (ingestId && importedMeshStore.getSnapshot().solver?.ingestId === ingestId) {
    importedMeshStore.clear('solver', importedMeshStore.captureGeneration());
  }
});
