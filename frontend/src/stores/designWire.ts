/**
 * Compose the design payload that leaves WG as a `.cfg`.
 *
 * A design file describes a horn *and* the simulation it belongs to. The
 * directivity blocks ATH understands go through `designWireWithAthPolars`;
 * everything else a solve depends on goes into WG's own `WG.Solve` block, so
 * reopening a design restores the sweep, the mesh policy, and the measurement
 * origin it was serialized with instead of whatever was last set on this machine.
 *
 * The design's name rides along in ATH's own `Report.Title`, so the file states
 * what it is called and reopening it recovers the name rather than inheriting
 * whatever the last file was called.
 */
import { designWireWithAthPolars } from './athPolars';
import type { ConfigBlock } from './design';
import { blocksWithDesignTitle } from './designName';
import { useDocumentStore } from './document';
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

/** The settings currently on screen, for serializing or sending the edited design. */
export function wgSolveSettingsFromStore(
  state = useSolveOptionsStore.getState(),
): WgSolveSettings {
  return {
    symmetry: state.symmetry,
    meshValidationMode: state.meshValidationMode,
    verbose: state.verbose,
    frequencySpacing: state.frequencySpacing,
    frequencyMode: state.frequencyMode,
    frequencyListText: state.frequencyListText,
    observationOrigin: state.polar.observationOrigin,
    sphericalSampling: state.polar.sphericalSampling,
    fieldPlane: state.polar.fieldPlane !== false,
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
    fieldPlane: polar.field_plane !== false,
  };
}

/**
 * Everything a serialized `.cfg` carries that does not live in the design document.
 *
 * The design store's `designRevision` is a *geometry* revision: it drives the
 * preview rebuild, so directivity and solver edits deliberately do not bump it.
 * Those edits are still document changes -- they are written into the file and
 * sent to CAD -- which left the unsaved indicator and the CAD freshness check
 * blind to them. This signature is the thing those two compare instead, so a
 * measurement distance typed after the opened-file baseline reads as unsaved work without
 * pretending the mesh needs rebuilding.
 */
export function documentSettingsSignature(
  state = useSolveOptionsStore.getState(),
): string {
  // This runs as a Zustand selector on the render path, so it must be total:
  // a directivity grid mid-edit can be invalid (`polarConfigFromUi` throws for
  // it), and a selector throw unmounts the entire app. An invalid grid is
  // still a document change, so it is serialized raw -- the signature keeps
  // changing as the user types -- rather than resolved.
  let polar: unknown;
  try {
    polar = polarConfigFromUi(state.polar);
  } catch {
    polar = { invalid: state.polar };
  }
  return JSON.stringify({
    polar,
    solve: wgSolveSettingsFromStore(state),
  });
}

/** Overlay the design's name, the ATH polar blocks, and WG's solver block. */
export function designWireWithSolveSettings(
  design: Record<string, unknown>,
  polarValue: unknown,
  solveSettings: WgSolveSettings | null,
  designName: string = useDocumentStore.getState().designName,
): Record<string, unknown> {
  const withPolars = designWireWithAthPolars(design, polarValue);
  const existing = isRecord(withPolars.extra_blocks)
    ? withPolars.extra_blocks as Record<string, ConfigBlock>
    : {};
  const named = blocksWithDesignTitle(existing, designName);
  return {
    ...withPolars,
    extra_blocks: solveSettings ? withWgSolveBlock(named, solveSettings) : named,
  };
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
  designName: string = useDocumentStore.getState().designName,
): Record<string, unknown> {
  return designWireWithSolveSettings(
    design,
    polarConfigFromUi(state.polar),
    wgSolveSettingsFromStore(state),
    designName,
  );
}
