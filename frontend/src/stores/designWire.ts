/**
 * Compose the design payload that leaves WG as a `.cfg`.
 *
 * A design file describes a horn *and* the simulation it belongs to. The
 * directivity blocks ATH understands go through `designWireWithAthPolars`;
 * everything else a solve depends on goes into WG's own `WG.Solve` block, so
 * reopening a design restores the sweep, the mesh policy, and the measurement
 * origin it was saved with instead of whatever was last set on this machine.
 */
import { designWireWithAthPolars } from './athPolars';
import type { ConfigBlock } from './design';
import {
  polarConfigFromUi,
  useSolveOptionsStore,
  type FrequencyMode,
  type FrequencySpacing,
  type MeshValidationMode,
  type ObservationOrigin,
  type SymmetryMode,
} from './solveOptions';
import { withWgSolveBlock, type WgSolveSettings } from './wgSolveBlock';

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/** The settings currently on screen, for saving the design being edited. */
export function wgSolveSettingsFromStore(
  state = useSolveOptionsStore.getState(),
): WgSolveSettings {
  return {
    engine: state.engine,
    symmetry: state.symmetry,
    meshValidationMode: state.meshValidationMode,
    verbose: state.verbose,
    frequencySpacing: state.frequencySpacing,
    frequencyMode: state.frequencyMode,
    frequencyListText: state.frequencyListText,
    observationOrigin: state.polar.observationOrigin,
    sphericalSampling: state.polar.sphericalSampling,
  };
}

/**
 * The settings a finished run recorded, for exporting a config from a result.
 *
 * A run's own `solve_options` is the only correct source here: exporting the
 * config for run #42 must describe run #42, not the draft currently on screen.
 */
export function wgSolveSettingsFromSolveOptions(options: unknown): WgSolveSettings | null {
  if (!isRecord(options)) return null;
  const polar = isRecord(options.polar_config) ? options.polar_config : {};
  const frequencies = Array.isArray(options.frequencies_hz)
    ? options.frequencies_hz.filter((value): value is number => Number.isFinite(value))
    : [];
  return {
    engine: typeof options.engine === 'string' ? options.engine : 'auto',
    symmetry: (typeof options.symmetry === 'string' ? options.symmetry : 'auto') as SymmetryMode,
    meshValidationMode: (typeof options.mesh_validation_mode === 'string'
      ? options.mesh_validation_mode
      : 'warn') as MeshValidationMode,
    verbose: options.verbose === true,
    frequencySpacing: (typeof options.frequency_spacing === 'string'
      ? options.frequency_spacing
      : 'log') as FrequencySpacing,
    frequencyMode: (frequencies.length ? 'list' : 'range') as FrequencyMode,
    frequencyListText: frequencies.join(', '),
    observationOrigin: (typeof polar.observation_origin === 'string'
      ? polar.observation_origin
      : 'mouth') as ObservationOrigin,
    sphericalSampling: polar.spherical_sampling === true,
  };
}

/**
 * Everything a saved `.cfg` carries that does not live in the design document.
 *
 * The design store's `designRevision` is a *geometry* revision: it drives the
 * preview rebuild, so directivity and solver edits deliberately do not bump it.
 * Those edits are still document changes -- they are written into the file and
 * sent to CAD -- which left the unsaved indicator and the CAD freshness check
 * blind to them. This signature is the thing those two compare instead, so a
 * measurement distance typed after a save reads as unsaved work without
 * pretending the mesh needs rebuilding.
 */
export function documentSettingsSignature(
  state = useSolveOptionsStore.getState(),
): string {
  return JSON.stringify({
    polar: polarConfigFromUi(state.polar),
    solve: wgSolveSettingsFromStore(state),
  });
}

/** Overlay both the ATH polar blocks and WG's solver block on a design wire. */
export function designWireWithSolveSettings(
  design: Record<string, unknown>,
  polarValue: unknown,
  solveSettings: WgSolveSettings | null,
): Record<string, unknown> {
  const withPolars = designWireWithAthPolars(design, polarValue);
  if (!solveSettings) return withPolars;
  const existing = isRecord(withPolars.extra_blocks)
    ? withPolars.extra_blocks as Record<string, ConfigBlock>
    : {};
  return { ...withPolars, extra_blocks: withWgSolveBlock(existing, solveSettings) };
}

/**
 * The wire a send would write for the document on screen, right now.
 *
 * CAD freshness is decided by hashing this payload against the one the linked
 * document was built from, so the status has to hash what a send would produce
 * rather than the bare design. Hashing `serializeDesign` alone reported "up to
 * date" after a directivity or solver change, because the blocks carrying it
 * are added on the way out and the comparison never saw them.
 */
export function currentDesignWire(
  design: Record<string, unknown>,
  state = useSolveOptionsStore.getState(),
): Record<string, unknown> {
  return designWireWithSolveSettings(
    design,
    polarConfigFromUi(state.polar),
    wgSolveSettingsFromStore(state),
  );
}
