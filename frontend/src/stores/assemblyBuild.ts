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
import type { ImportedModelSummary } from '../api/importedModels';
import { currentDocumentLoad, isCurrentDocumentLoad, serializeDesign, subscribeRevision, useDesignStore, type DesignDocument } from './design';
import { designContentKey } from './designContentKey';
import { useDocumentStore } from './document';
import type { ImportedMeshScene } from '../viewport/importedMesh';
import { assemblyMeshScene } from '../viewport/solverMeshScene';

export interface PublishedAssembly {
  revision: number;
  contentKey: string | null;
  record: CompiledAssembly;
  model: ImportedModelSummary;
}

export interface AssemblyBuildState {
  status: 'idle' | 'building' | 'published' | 'refused';
  requested: { revision: number; contentKey: string | null; load: number } | null;
  published: PublishedAssembly | null;
  refusal: string | null;
  /** The original failure, for the developer disclosure only. */
  failureDetail: string | null;
}

const IDLE: AssemblyBuildState = { status: 'idle', requested: null, published: null, refusal: null, failureDetail: null };
/** This page's builds: the server orders revisions within one page and document only. */
const PAGE_SESSION = globalThis.crypto?.randomUUID?.() ?? `page-${Date.now()}-${Math.random()}`;

export const useAssemblyBuildStore = create<AssemblyBuildState>(() => ({ ...IDLE }));

/** What a build is keyed by: the portable design, without directivity or solve settings. */
export function assemblyContentKey(design: DesignDocument): string | null {
  return designContentKey(design, '', { polar: { config: null }, solve: null });
}

/** Whether the last valid compile is the design on screen. */
export function assemblyBuildCurrent(state: Pick<AssemblyBuildState, 'published'>, design: DesignDocument): boolean {
  const key = assemblyContentKey(design);
  return !!state.published && key !== null && state.published.contentKey === key;
}

/** The preview's label for the last valid compile, or null when it is current. */
export function assemblyPreviewLabel(state: Pick<AssemblyBuildState, 'published' | 'status'>, design: DesignDocument): string | null {
  if (!state.published || assemblyBuildCurrent(state, design)) return null;
  return state.status === 'building'
    ? 'Showing the last built assembly while this revision builds.'
    : 'Showing the last built assembly; it is from an earlier revision.';
}

/** A run solved from a compiled assembly is stale once the design or its compile moved on. */
export function assemblyRunStale(preparedId: string, state: Pick<AssemblyBuildState, 'published'>, design: DesignDocument): boolean {
  return state.published?.record.ingest_id !== preparedId || !assemblyBuildCurrent(state, design);
}

const scenes = new WeakMap<PublishedAssembly, ImportedMeshScene>();

/** The viewport scene of a published compile, parsed once per build. It is the
 * design's solve artifact, so it is drawn the way its solver mesh is. */
export function publishedAssemblyScene(published: PublishedAssembly): ImportedMeshScene {
  let scene = scenes.get(published);
  if (!scene) {
    scene = assemblyMeshScene(published.model.display_name || 'Assembly', published.record, 'solver');
    scenes.set(published, scene);
  }
  return scene;
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
export function buildCurrentAssembly(fetcher: typeof fetch = fetch): Promise<AssemblyCompileResult | null> {
  const state = useAssemblyBuildStore.getState();
  const { design } = useDesignStore.getState();
  if (state.published && state.status !== 'building' && assemblyBuildCurrent(state, design)) {
    return Promise.resolve({ status: 'published', reused: true, model: state.published.model, prepared: state.published.record });
  }
  return requestAssemblyBuild(fetcher);
}

async function compileRequested(design: DesignDocument, requested: NonNullable<AssemblyBuildState['requested']>,
  fetcher: typeof fetch): Promise<AssemblyCompileResult | null> {
  const { designUid, designName } = useDocumentStore.getState();
  const { revision: designRevision, load } = requested;
  useAssemblyBuildStore.setState({ status: 'building', requested, refusal: null, failureDetail: null });
  const owns = () => {
    const current = useAssemblyBuildStore.getState().requested;
    return isCurrentDocumentLoad(load) && current?.revision === requested.revision && current.load === load;
  };
  try {
    const result = await compileAssembly({
      design: serializeDesign(design), design_uid: designUid ?? '', design_name: designName, design_revision: designRevision,
      build_session: `${PAGE_SESSION}:${load}`,
    }, fetcher);
    if (!owns()) return { status: 'superseded' };
    if (result.status === 'published') {
      useAssemblyBuildStore.setState({ status: 'published', refusal: null, published: {
        revision: designRevision, contentKey: requested.contentKey, record: result.prepared, model: result.model,
      } });
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

/** What a failed build tells the user. */
export function assemblyBuildFailure(error: unknown): string {
  return assemblyProblemText(error, 'WG could not build this assembly and has recorded the problem.');
}

/** New and Open start a document of their own: nothing built for the last one applies. */
export function resetAssemblyBuild(): void {
  useAssemblyBuildStore.setState({ ...IDLE });
}

subscribeRevision(event => { if (event.reason === 'load') resetAssemblyBuild(); });
