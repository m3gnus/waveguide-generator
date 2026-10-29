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
import { compileAssembly, type AssemblyCompileResult, type CompiledAssembly } from '../api/assembly';
import type { ImportedModelSummary } from '../api/importedModels';
import { currentDocumentLoad, isCurrentDocumentLoad, serializeDesign, subscribeRevision, useDesignStore, type DesignDocument } from './design';
import { designContentKey } from './designContentKey';
import { useDocumentStore } from './document';

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
}

const IDLE: AssemblyBuildState = { status: 'idle', requested: null, published: null, refusal: null };

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

/** Compile the assembly of the design on screen; resolves to what this request learned. */
export async function requestAssemblyBuild(fetcher: typeof fetch = fetch): Promise<AssemblyCompileResult | null> {
  const { design, designRevision } = useDesignStore.getState();
  if (!design.assembly) {
    useAssemblyBuildStore.setState({ ...IDLE });
    return null;
  }
  const { designUid, designName } = useDocumentStore.getState();
  const load = currentDocumentLoad();
  const requested = { revision: designRevision, contentKey: assemblyContentKey(design), load };
  useAssemblyBuildStore.setState({ status: 'building', requested, refusal: null });
  const owns = () => {
    const current = useAssemblyBuildStore.getState().requested;
    return isCurrentDocumentLoad(load) && current?.revision === requested.revision && current.load === load;
  };
  try {
    const result = await compileAssembly({
      design: serializeDesign(design), design_uid: designUid ?? '', design_name: designName, design_revision: designRevision,
    }, fetcher);
    if (!owns()) return { status: 'superseded' };
    if (result.status === 'published') {
      useAssemblyBuildStore.setState({ status: 'published', refusal: null, published: {
        revision: designRevision, contentKey: requested.contentKey, record: result.prepared, model: result.model,
      } });
    }
    return result;
  } catch (error) {
    if (owns()) useAssemblyBuildStore.setState({ status: 'refused', refusal: error instanceof Error ? error.message : String(error) });
    throw error;
  }
}

/** New and Open start a document of their own: nothing built for the last one applies. */
export function resetAssemblyBuild(): void {
  useAssemblyBuildStore.setState({ ...IDLE });
}

subscribeRevision(event => { if (event.reason === 'load') resetAssemblyBuild(); });
