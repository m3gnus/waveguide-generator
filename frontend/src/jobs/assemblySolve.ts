/**
 * The press of a design assembly's Solve: the one model Solve
 * (`POST /api/models/{id}/solve`) on the compiled record of the design on
 * screen, with the setup the shared Simulation sections show. The jobs
 * coordinator submits it, under the same lock and attention as every Solve.
 */
import { assemblyContextRefused, fetchAssemblyContext } from '../api/assembly';
import { placementProblems } from '../sketch/adapters/frontView';
import { assemblyBuildCurrent, requestAssemblyBuild, useAssemblyBuildStore } from '../stores/assemblyBuild';
import { assemblyImportedSetup } from '../stores/assemblySimulation';
import { serializeDesign, useDesignStore } from '../stores/design';
import { useSolveOptionsStore } from '../stores/solveOptions';
import type { ImportedSolveSubmission, ModelSolvePress } from './actions';
import { resolveAssemblySetup } from './importedSubmission';

export interface AssemblySolvePress { modelId: string; press: ModelSolvePress; notice: string | null }

/** Build the design's assembly if it is not current, then the press for its record. */
export async function prepareAssemblySolve(fetcher: typeof fetch = fetch,
  newPressId: () => string = () => globalThis.crypto.randomUUID()): Promise<AssemblySolvePress> {
  const { design, designRevision } = useDesignStore.getState();
  if (!design.assembly) throw new Error('This design has no Sources and baffle assembly.');
  // Everything below answers for what was on screen at this press: the check,
  // the build and the settings all belong to one revision, or none goes out.
  const optionsAtPress = useSolveOptionsStore.getState().options() as ImportedSolveSubmission['options'];
  const sameRevision = () => useDesignStore.getState().designRevision === designRevision;
  const changed = () => new Error('The design changed while it was being prepared. Press Solve again.');
  if (!assemblyBuildCurrent(useAssemblyBuildStore.getState(), design)) {
    // A woofer that does not fit never reaches the build, wherever Solve was pressed.
    // A check that cannot run leaves that to the build, which certifies the fit itself.
    const context = await fetchAssemblyContext(serializeDesign(design), fetcher).catch((reason: unknown) => {
      if (assemblyContextRefused(reason)) throw reason;
      return null;
    });
    const [problem] = context ? [...placementProblems(design.assembly, { context, assembly: design.assembly }).values()].flat() : [];
    if (problem) throw new Error(`${problem} Open Sources and baffle to fix it.`);
    if (!sameRevision()) throw changed();
    const built = await requestAssemblyBuild(fetcher);
    if (built?.status !== 'published') throw new Error('A newer revision of this design is being built. Solve again when it is ready.');
  }
  const published = useAssemblyBuildStore.getState().published;
  if (!published || !sameRevision() || !assemblyBuildCurrent(useAssemblyBuildStore.getState(), design)) throw changed();
  // Channels are the compiled record's excitations, so this reads the build above.
  const { setup, notice } = resolveAssemblySetup(assemblyImportedSetup(), design.assembly, optionsAtPress, design.simulation);
  return {
    modelId: published.model.model_id, notice,
    press: {
      prepared_id: published.record.ingest_id,
      // The run keeps the design it was pressed from, so it is this design's run.
      design: serializeDesign(design),
      client_request_id: `assembly-${newPressId()}`,
      setup: { schema_version: 1, ...setup } as unknown as ModelSolvePress['setup'],
    },
  };
}
