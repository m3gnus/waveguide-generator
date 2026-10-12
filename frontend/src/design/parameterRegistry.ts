import { designForFamily, type DesignDocument, type DesignFamily } from '../stores/design';
import { NATIVE_FAMILIES, type NativeFamily } from './nativeFamilies';
import type { WorkspaceMode } from '../stores/workspaceMode';
import { backendSupports, hostLimitation, selectionLimitation, type BackendFeature, type BackendIdentity } from './backendSupport';
import type { EngineCapability } from '../jobs/actions';

export type ParameterSection =
  | 'Profile' | 'Throat' | 'Mouth' | 'Cross-section' | 'Mounting'
  | 'Frequency sweep' | 'Source excitation' | 'Mesh' | 'Export' | 'Imported ATH values'
  | 'Directivity map' | 'Solver' | 'Experimental'
  | 'Profile Dimensions'
  | 'Throat Extension'
  | 'Morph Target'
  | 'Wall & Enclosure'
  | 'Guiding Curve'
  | 'Surface sampling'
  | 'Frequency Sweep'
  | 'Source Definition'
  | 'Solve & export mesh'
  | 'Output & Passthrough';

export type ParameterTab = 'geometry' | 'simulation';

export interface ParameterSectionContext {
  mode: WorkspaceMode;
  design: DesignDocument;
  /** Whether a parametric waveguide design stands behind what is on screen. */
  waveguideLinked: boolean;
}

export interface ParameterSectionDefinition {
  title: ParameterSection;
  tab: ParameterTab;
  description: string;
  /** Complete presentation order, including custom-control slots. Legacy consumers ignore it. */
  fieldOrder?: readonly string[];
  /** Section policy is deliberately data-aware rather than a mode list. CAD
   * is the first consumer; later sections can use the same gate when their
   * presence depends on actual design content. */
  visibleWhen?: (ctx: ParameterSectionContext) => boolean;
}

export type ParameterKind = 'number' | 'select' | 'text' | 'toggle' | 'table' | 'indicator';

export interface ParameterOption {
  value: string | number;
  label: string;
  /**
   * Hidden unless the active solver backend implements this feature.
   *
   * A design that already selects the option keeps it listed, warned rather
   * than removed: a .wg file written on a Mac, or an ATH .cfg carrying
   * `ABEC.SimType = 1`, must still be able to show what it is set to and let
   * the user change it. Hiding the value out from under such a design would
   * leave it failing at solve with nothing on screen to explain why.
   */
  requiresFeature?: BackendFeature;
  /** Preserve an imported choice without offering it for a fresh selection. */
  currentOnly?: boolean;
  /** Feature whose absence makes `label` misleading rather than impossible. */
  degradedWithout?: BackendFeature;
  /** Shown in place of `label` when `degradedWithout` is unsupported. */
  degradedLabel?: string;
  /**
   * Set on an option that stays listed but cannot be chosen, because no engine
   * on this host can run `requiresFeature`. It says why, in a short sentence.
   */
  unavailableReason?: string;
}

export interface ParameterDefinition {
  id: string;
  legacyKey: string;
  path?: string;
  mirrorPaths?: string[];
  section: ParameterSection;
  label: string;
  unit?: string;
  symbol?: string;
  kind: ParameterKind;
  families?: DesignFamily[];
  min?: number;
  max?: number;
  step?: number;
  precision?: number;
  options?: ParameterOption[];
  description?: string;
  disabledReason?: string;
  optional?: boolean;
  group?: 'Advanced ICW';
  visibleWhen?: (design: DesignDocument) => boolean;
  disabledWhen?: (design: DesignDocument) => string | undefined;
}

const allFamilies: DesignFamily[] = ['R-OSSE', 'OSSE', 'ICW', 'FREEFORM', 'LOOKUP'];
const osseFamilies: DesignFamily[] = ['R-OSSE', 'OSSE'];
const profile = 'Profile Dimensions' as const;
const throatExtension = 'Throat Extension' as const;
const morphTarget = 'Morph Target' as const;
const wallEnclosure = 'Wall & Enclosure' as const;
const guidingCurve = 'Guiding Curve' as const;
const surfaceSampling = 'Surface sampling' as const;
const frequencySweep = 'Frequency Sweep' as const;
const sourceDefinition = 'Source Definition' as const;
const solveExportMesh = 'Solve & export mesh' as const;
const parametricWorkspaceOnly: NonNullable<ParameterSectionDefinition['visibleWhen']> = ({ mode }) => mode === 'parametric';
const guidingCurveSectionReason = (design: DesignDocument) => design.formula === 'OSSE'
  && design.guiding_curve.curve_type !== 0 && design.guiding_curve.width !== 0
  ? 'Active guiding curves require cross-section exponent 2 and aspect ratio 1.' : undefined;
/**
 * Sections that define WG's own horn.
 *
 * In CAD mode these still belong on screen for a project WG originated -- they
 * are what you edit before sending the design back to CAD. A project authored
 * in CAD has no horn of WG's behind it, so every field here would be an input
 * that cannot move anything.
 */
const waveguideDefinitionOnly: NonNullable<ParameterSectionDefinition['visibleWhen']> = (
  { mode, waveguideLinked },
) => mode === 'parametric' || waveguideLinked;

const number = (
  id: string,
  legacyKey: string,
  path: string,
  section: ParameterSection,
  label: string,
  options: Partial<ParameterDefinition> = {},
): ParameterDefinition => ({
  id, legacyKey, path, section, label, kind: 'number', step: .1, precision: 2, ...options,
});

const select = (
  id: string,
  legacyKey: string,
  path: string,
  section: ParameterSection,
  label: string,
  options: ParameterOption[],
  rest: Partial<ParameterDefinition> = {},
): ParameterDefinition => ({ id, legacyKey, path, section, label, kind: 'select', options, ...rest });

export const ICW_ADVANCED_FIELD_IDS = [
  'icw.r_aperture', 'icw.x_aperture', 'icw.x_setback', 'icw.kappa0',
  'icw.kappa_abs_max', 'icw.dkappa_ds_abs_max', 'icw.theta_max_deg',
  'icw.pin_mouth_radius', 'icw.icw_S', 'icw.icw_seed', 'icw.icw_coeffs',
] as const;
const icwInputReason = (design: DesignDocument) => design.icw_seed != null
  ? 'Imported seed mode fits the seed profile; target controls are ignored.'
  : design.icw_coeffs != null ? 'Imported coefficients define the curve directly; target controls are ignored.' : undefined;
const advancedIcw = { families: ['ICW'] as DesignFamily[], optional: true, group: 'Advanced ICW' as const };
const rollbackIcwReason = (design: DesignDocument) => icwInputReason(design)
  ?? (design.termination !== 'rollback' ? 'Used only for rollback termination.' : undefined);

const yesNo: ParameterOption[] = [{ value: 0, label: 'No' }, { value: 1, label: 'Yes' }];

// The seven superformula terms do genuinely different things, so each gets its
// own line rather than one blurb repeated seven times. Wording follows the
// mesher's evaluation in profile_morph._guiding_curve_target_radius:
// r = (|cos(m1*p/4)/a|^n2 + |sin(m2*p/4)/b|^n3)^(-1/n1).
const SUPERFORMULA_HELP: Record<'sf_a' | 'sf_b' | 'sf_m1' | 'sf_m2' | 'sf_n1' | 'sf_n2' | 'sf_n3', string> = {
  sf_a: 'Scales the cosine term of the superformula, stretching the guide shape along the horizontal axis. Sign is ignored.',
  sf_b: 'Scales the sine term of the superformula, stretching the guide shape along the vertical axis. Sign is ignored.',
  sf_m1: 'Angular frequency of the cosine term; sign is ignored. Match it to m2 for the usual symmetric outline, where the pair sets how many lobes it has — 4 gives the rounded-square family. When they differ, neither one alone is the lobe count.',
  sf_m2: 'Angular frequency of the sine term; sign is ignored. Match it to m1 for a symmetric outline. A complete six-value tuple ignores this field and uses its single m for both terms.',
  sf_n1: 'Overall exponent on the combined terms. Smaller values exaggerate the in-and-out variation, larger values flatten the outline toward a circle. Sign is ignored, and 0 gives extreme geometry.',
  sf_n2: 'Exponent on the cosine term, setting how sharp or bulged the lobes are. Keep it at 0 or above — a negative value blows up wherever the cosine crosses zero and fails the build.',
  sf_n3: 'Exponent on the sine term, setting how sharp or bulged the lobes are. Keep it at 0 or above — a negative value blows up wherever the sine crosses zero and fails the build.',
};

const nativeHorns = NATIVE_FAMILIES.filter((family) => family !== 'SOURCE-DISK');
const authoredAdapters: DesignFamily[] = ['OSSE-ADAPTER', 'OSSE-ADAPTER-AXIAL'];
const literalAdapters: DesignFamily[] = ['OSSE-ADAPTER-CONTROLS'];
const nativeNumber = (id: string, path: string, label: string, options: Partial<ParameterDefinition>): ParameterDefinition =>
  number(`native.${id}`, path, path, profile, label, options);

export const LEGACY_PARAMETER_REGISTRY: ParameterDefinition[] = [
  nativeNumber('L', 'L', 'Body length', { families: nativeHorns, unit: 'mm', min: .01, max: 10000, description: 'Intrinsic length of the original circular OSSE body before scale, adapter trimming or arc attachment. Axial, arc and roundover formulas require finished length 1–2000 mm; use axial_scale for Z on axial bodies and scale otherwise. Adapter-axial instead bounds total finished adapter plus retained-body depth to 1–2000 mm.' }),
  nativeNumber('r0', 'r0', 'Body throat radius', { families: nativeHorns, unit: 'mm', min: .01, max: 10000, description: 'Intrinsic radius of the original OSSE body. Axial, arc and roundover formulas require r0 × scale in 1–200 mm. Adapter-axial instead requires finished driver radius in 1–200 mm; driver diameter is controlled separately.' }),
  nativeNumber('a', 'a', 'Body coverage half-angle', { families: nativeHorns, unit: '°', min: 1, max: 79.999, description: 'Circular body flare angle. Must exceed the throat half-angle. Axial, arc and roundover formulas require 5 through 75 degrees.' }),
  nativeNumber('a0', 'a0', 'Body throat half-angle', { families: nativeHorns, unit: '°', min: 0, max: 79.999, description: 'Circular body tangent angle at its original throat, strictly below its coverage angle.' }),
  nativeNumber('k', 'k', 'Body flare constant', { families: nativeHorns, min: .01, max: 100, description: 'Positive OSSE flare constant. Axial, arc and roundover formulas require 0.5 through 10.' }),
  nativeNumber('scale', 'scale', 'Transverse / uniform scale', { families: nativeHorns, min: .001, max: 100, step: .001, precision: 3, description: 'Scales intrinsic body and adapter dimensions uniformly, or X/Y only when Absolute axial scale is present. Mesh sizes, lip radius, wall and final placement stay in finished mm. OSSE-ADAPTER and OSSE-ADAPTER-CONTROLS allow 0.001–100; other native horns require 0.01–10. Axial formulas also require axial_scale / scale in 0.1–10.' }),
  nativeNumber('axial_scale', 'axial_scale', 'Absolute axial scale', { families: ['OSSE-AXIAL', 'OSSE-ADAPTER-AXIAL'], min: .01, max: 10, step: .001, precision: 3, description: 'Absolute Z factor, replacing uniform scale on Z. Axial/transverse ratio must be 0.1 through 10.' }),
  ...([
    ['driver_exit_diameter_mm', 'Driver exit diameter', 'mm', .001, 20000, 'Intrinsic driver opening diameter. The adapter joins this opening to the retained OSSE body.'],
    ['exit_half_angle_deg', 'Driver exit half-angle', '°', -89, 89, 'Signed authored driver tangent angle; anisotropic scaling changes the finished angle.'],
    ['length_mm', 'Adapter length', 'mm', .001, 10000, 'Intrinsic axial distance from driver plane to body join.'],
    ['join_t', 'Retained body start', 'fraction', 0, .999999, 'Fraction along the original OSSE body where the adapter joins it. The remaining body keeps its shape.'],
    ['driver_handle_mm', 'Driver handle length', 'mm', .001, 10000, 'Cubic handle distance along the driver tangent. Invalid or backward cubic controls are refused.'],
    ['body_handle_mm', 'Body handle length', 'mm', .001, 10000, 'Cubic handle distance along the body tangent, giving a smooth oriented join.'],
  ] as const).map(([path, label, unit, min, max, description]) => nativeNumber(`adapter.${path}`, `throat_adapter.${path}`, label, { families: authoredAdapters, unit, min, max, step: .001, precision: 6, description })),
  ...[0, 1, 2].flatMap((pole) => [0, 1].map((axis) => nativeNumber(`controls.${pole}.${axis}`, `throat_adapter.control_points_mm.${pole}.${axis}`, `Cubic P${pole} ${axis === 0 ? 'Z' : 'radius'}`, { families: literalAdapters, unit: 'mm', min: -1000000, max: 1000000, step: .001, precision: 6, description: 'Independent literal cubic pole in join-local coordinates. P3 is (0, body throat radius). The actual curve must stay forward and positive; the join may have a tangent corner.' }))),
  nativeNumber('arc.join_t', 'terminating_arc.join_t', 'Arc attachment', { families: ['OSSE-ARC'], unit: 'fraction', min: .000001, max: 1, step: .001, precision: 6, description: 'Fraction of original body retained before the exact tangent- and curvature-matched circular suffix.' }),
  nativeNumber('arc.end_tangent_deg', 'terminating_arc.end_tangent_deg', 'Arc end tangent', { families: ['OSSE-ARC'], unit: '°', min: .001, max: 180, description: 'Absolute oriented tangent from forward Z toward increasing radius. Must exceed the join tangent; above 90 degrees turns backward. Source-plane clearance is required.' }),
  nativeNumber('lip.radius', 'mesh.mouth_roundover_radius_mm', 'Mouth roundover radius', { families: ['OSSE-ROUNDOVER'], section: wallEnclosure, unit: 'mm', min: .2, max: 200, description: 'Finished circular lip radius, at least 0.1 mm larger than wall thickness. The full lip must clear the rear and its outer offset must not fold.' }),
  nativeNumber('lip.wall', 'mesh.wall_thickness', 'Wall thickness', { families: ['OSSE-ROUNDOVER'], section: wallEnclosure, unit: 'mm', min: .1, max: 199.9, description: 'Finished normal wall thickness. Must remain at least 0.1 mm below the lip radius and satisfy the curvature clearance guard.' }),
  ...([
    ['radius_mm', 'Source disk radius', .1, 200, 'Finished radius of the normal-driven front disk.'],
    ['outer_radius_mm', 'Source body radius', .2, 500, 'Finished exterior radius, at least 0.1 mm larger than source disk radius.'],
    ['depth_mm', 'Source body depth', .1, 500, 'Finished depth behind the front disk. The rear and side are rigid.'],
  ] as const).map(([path, label, min, max, description]) => nativeNumber(`source.${path}`, `source_body.${path}`, label, { families: ['SOURCE-DISK'], unit: 'mm', min, max, description })),
  nativeNumber('source.size', 'mesh.size_mm', 'Uniform source mesh size', { families: ['SOURCE-DISK'], section: solveExportMesh, unit: 'mm', min: .1, max: 50, description: 'Uniform finished-mm target, capped at source radius / 8. The mesher enforces its source-body allocation and triangle limits.' }),
  ...(['throat', 'mouth', 'rear'] as const).map((region) => nativeNumber(`mesh.${region}`, `mesh.${region}_resolution`, `${region[0].toUpperCase()}${region.slice(1)} mesh resolution`, { families: nativeHorns, section: solveExportMesh, unit: 'mm', min: .01, max: 10000, description: 'Finished-mm mesh target. Native curvature certificates may require finer facets or refuse the build. Smaller values cost solve time and memory.' })),
  nativeNumber('mesh.budget', 'mesh.max_triangles', 'Native mesh triangle budget', { families: [...NATIVE_FAMILIES], section: solveExportMesh, min: 1, max: 10000000, step: 1000, precision: 0, description: 'Mesher triangle limit for native exports. Solve meshes also retain the independent 22,000-triangle and dense-memory ceilings.' }),
  select('native.mesh.allow_large', 'allow_large_mesh', 'mesh.allow_large_mesh', solveExportMesh, 'Allow larger native exports', yesNo, { families: [...NATIVE_FAMILIES], description: 'Allows native exports to exceed the user triangle budget. Source-body hard allocation limits and solve safety ceilings still apply.' }),
  nativeNumber('mesh.offset', 'mesh.vertical_offset', 'Export vertical offset', { families: [...NATIVE_FAMILIES], section: solveExportMesh, unit: 'mm', min: -10000, max: 10000, description: 'Finished Y placement for native STEP and STL exports. Live preview and solve stay centred.' }),

  // Plain coefficients; per-azimuth expressions are not supported by the mesher.
  number('common.s1', 's1', 's1', profile, 'Throat stretch amount', { symbol: 's1', families: osseFamilies, min: 0, max: 10, step: .01, precision: 3, description: 'How much extra length is added near the throat. Uses a plain number from 0 to 10; either stretch control at zero turns stretching off. Needs mesher 0.2.4.' }),
  number('common.s2', 's2', 's2', profile, 'Throat stretch sharpness', { symbol: 's2', families: osseFamilies, min: 0, max: 10, step: .01, precision: 3, description: 'How quickly the extra length builds up near the throat. Uses a plain number from 0 to 10; larger values make the transition sharper. Per-angle expressions are not supported. Needs mesher 0.2.4.' }),
  // Complete family profile scalars.
  number('common.scale', 'scale', 'scale', profile, 'Scale', { families: allFamilies, min: .1, max: 2, step: .001, precision: 3, description: 'Shrinks or enlarges the waveguide below and above 1. Native designs and saved older ATH imports also scale the source radius, wall thickness and horn mesh sizes. Current ATH imports scale the finished horn as ATH does and keep the source radius, vertical offset, wall thickness and mesh sizes as written. Enclosure dimensions are not scaled.' }),
  number('rosse.R', 'R', 'R', profile, 'Mouth radius', { symbol: 'R', families: ['R-OSSE'], unit: 'mm', min: .1, max: 1_000, description: 'Target radius at the end of the curve. Truncation limit and morphing can both change the mouth you actually get. Accepts an expression in the azimuthal angle p, so the mouth need not be circular — for example "140 - 20*sin(p)^2".' }),
  number('rosse.a', 'a', 'a', profile, 'Mouth coverage angle', { symbol: 'a', families: ['R-OSSE'], unit: '°', min: -180, max: 180, description: 'Sets how steeply the horn flares, which is what fixes its derived length — the mouth radius is pinned by R, not by this. Larger values give a shorter, steeper horn. Sign is ignored. Accepts an expression in p.' }),
  number('rosse.a0', 'a0', 'a0', profile, 'Throat coverage angle', { symbol: 'a0', families: ['R-OSSE'], unit: '°', min: -90, max: 90, description: 'Sets how fast the profile starts opening at the throat. It seeds the throat-side curve rather than fixing the finished tangent, which m, r and q also move. Matching it to Throat extension angle therefore does not guarantee a tangent join. Accepts an expression in p, such as "15 + 2*sin(p)".' }),
  number('rosse.r0', 'r0', 'r0', profile, 'Throat radius', { symbol: 'r0', families: ['R-OSSE'], unit: 'mm', min: .1, max: 200, description: 'Radius where the main flare starts. With a throat extension the driver end is narrower — r0 minus the extension length times tan of its angle. Accepts an expression in p, such as "12.7 + sin(p)".' }),
  number('rosse.k', 'k', 'k', profile, 'Throat rounding', { symbol: 'k', families: ['R-OSSE'], min: .1, max: 10, description: 'Rounds the transition out of the throat. Higher values blend the throat into the flare more gradually.' }),
  number('rosse.m', 'm', 'm', profile, 'Apex shift', { symbol: 'm', families: ['R-OSSE'], min: 0, max: 1, step: .01, description: 'Where along the profile the virtual apex sits, from 0 at the throat to 1 at the mouth. The wall reaches its deepest point there, so values below 1 curl the mouth back on itself — that is how an R-OSSE rollback is made.' }),
  number('rosse.b', 'b', 'b', profile, 'Bending', { symbol: 'b', families: ['R-OSSE'], min: -10, max: 10, description: 'Bends the profile axially on top of the apex shape. Which way depends on Apex shift: above 0.5 positive b pulls the mouth back, below 0.5 it pushes it forward, and at exactly 0.5 b does nothing.' }),
  number('rosse.r', 'r', 'r', profile, 'Apex radius', { symbol: 'r', families: ['R-OSSE'], min: .01, max: 2, step: .01, description: 'Size of the rounded region around the virtual apex. Smaller values give a tighter, more abrupt bend.' }),
  number('rosse.q', 'q', 'q', profile, 'Shape factor', { symbol: 'q', families: ['R-OSSE'], min: .5, max: 10, description: 'Sets how the profile crosses from the throat curve to the mouth curve. Higher values hold the throat shape longer and then open faster near the mouth.' }),
  number('rosse.tmax', 'tmax', 'tmax', profile, 'Truncation limit', { symbol: 'tmax', families: ['R-OSSE'], min: .5, max: 1, step: .01, description: 'Cuts the profile short at this fraction of its computed curve. 1 keeps the whole thing.' }),

  number('osse.L', 'L', 'L', profile, 'Horn length', { symbol: 'L', families: ['OSSE'], unit: 'mm', min: .1, max: 1_000, description: 'Axial length of the horn profile. Length mode decides whether the straight slot is carved out of it; the throat extension is always added on top. Accepts an expression in p.' }),
  number('osse.a', 'a', 'a', profile, 'Mouth coverage angle', { symbol: 'a', families: ['OSSE'], unit: '°', min: -180, max: 180, description: 'The angle the OS-SE flare tends toward, rather than the exact tangent at the mouth. Larger values open the horn wider; sign is ignored. An active guiding curve replaces it, and Circular arc uses it as the endpoint angle instead.' }),
  number('osse.a0', 'a0', 'a0', profile, 'Throat coverage angle', { symbol: 'a0', families: ['OSSE'], unit: '°', min: -90, max: 90, description: 'Wall angle where the OS-SE curve leaves the throat, which sets how fast it starts opening. Matching it to Throat extension angle makes the conical extension and OS-SE wall meet tangentially. Ignored when Throat profile is Circular arc. Accepts an expression in p, such as "15 + 2*sin(p)".' }),
  number('osse.r0', 'r0', 'r0', profile, 'Throat radius', { symbol: 'r0', families: ['OSSE'], unit: 'mm', min: .1, max: 200, description: 'Radius where the main flare starts. With a throat extension the driver end is narrower — r0 minus the extension length times tan of its angle. Accepts an expression in p, such as "12.7 + sin(p)".' }),
  // v1's tooltip said higher k expands FASTER out of the throat. Measured
  // against _osse_radius it is the reverse: at L=120, a=30°, a0=15.5°, r0=12.7
  // the mouth radius falls 78.9 -> 57.1 mm as k goes 0.5 -> 10.
  number('osse.k', 'k', 'k', profile, 'Flare constant', { symbol: 'k', families: ['OSSE'], min: .1, max: 15, description: 'How gradually the profile leaves the throat. Higher values expand more slowly and blend the throat into the flare more smoothly, ending at a narrower mouth for the same length and angles.' }),
  number('osse.s', 's', 's', profile, 'Termination shape', { symbol: 's', families: ['OSSE'], min: -10, max: 10, description: 'How far the mouth end is pushed off the plain OS-SE curve: positive flares it outward, negative pulls it in, 0 leaves the curve alone. Ignored by the Circular arc profile.' }),
  number('osse.n', 'n', 'n', profile, 'Termination curvature', { symbol: 'n', families: ['OSSE'], min: 1, max: 10, step: .001, precision: 3, description: 'How abruptly the mouth-end offset comes in. Higher values hold the OS-SE shape longer and then turn sharply; the direction of the turn comes from Termination shape, not from here.' }),
  number('osse.q', 'q', 'q', profile, 'Termination smoothness', { symbol: 'q', families: ['OSSE'], min: .1, max: 2, step: .001, precision: 3, description: 'Where the mouth-end offset finishes, at 1/q of the length: above 1 it completes before the mouth, below 1 not until past it. It also scales the offset, which reaches s×L/q.' }),
  number('osse.h', 'h', 'h', profile, 'Shape factor', { symbol: 'h', families: ['OSSE'], min: 0, max: 10, description: 'Adds a half-sine bulge in millimetres to the radius, peaking halfway and vanishing at both ends. It spans the whole driver-to-mouth path, including any extension and slot, and applies to the circular arc too. In native designs Scale does not multiply it; current ATH imports scale the finished horn, this bulge included.' }),

  // ICW is axisymmetric by construction: the mesher evaluates every ICW param
  // at phi=0, so none of these take a per-azimuth expression. Say so rather
  // than repeating v1's "can be an expression" wording, which was copied over
  // from the R-OSSE fields and does not hold here.
  number('icw.r0', 'r0', 'r0', profile, 'Throat radius', { families: ['ICW'], unit: 'mm', min: .1, max: 200, description: 'Radius at the throat, where the driver attaches. The curvature solver starts the wall here.' }),
  number('icw.a0', 'a0', 'a0', profile, 'Throat half-angle', { families: ['ICW'], unit: '°', min: -90, max: 90, description: "Wall angle where the curve leaves the throat, measured from the horn axis. This is the curve's starting angle, not a target." }),
  number('icw.L', 'L', 'L', profile, 'Horn length', { families: ['ICW'], unit: 'mm', min: 1, max: 1_000, visibleWhen: (design) => design.termination !== 'rollback', description: 'Target axial length. The curvature solver sizes the curve to reach it. Rollback terminations use Rollback depth instead.' }),
  number('icw.R', 'R', 'R', profile, 'Mouth radius', { families: ['ICW'], unit: 'mm', min: .1, max: 1_000, optional: true, disabledWhen: (design) => (design.coverage_angle ?? 0) > 0 && !design.pin_mouth_radius ? 'Emergent while coverage hold is enabled; enable Pin mouth radius to target R.' : undefined, description: 'Target mouth radius the curvature solver sizes the curve to reach. On a rollback it is the aperture radius where the wall first turns through 90°, not the radius at the curled tip. During coverage hold it is emergent unless Pin mouth radius is enabled. Clear R to use the advanced Aperture radius on rollback.' }),
  number('icw.coverage_angle', 'coverage_angle', 'coverage_angle', profile, 'Coverage angle', { families: ['ICW'], unit: '°', min: 0, max: 80, step: 1, precision: 0, disabledWhen: (design) => design.termination === 'rollback' ? 'Coverage hold applies to flat-baffle termination only.' : undefined, description: 'Above 0 this holds a constant wall angle — the constant-directivity plateau — between the hold start and end, and Mouth radius becomes an emergent output rather than a target. 0 turns the hold off and sizes the curve from Horn length and Mouth radius alone. Flat baffle only.' }),
  number('icw.hold_start', 'hold_start', 'hold_start', profile, 'Coverage hold start', { families: ['ICW'], unit: 'σ', min: .05, max: .9, step: .01, visibleWhen: (design) => design.termination !== 'rollback', description: 'Where the constant-angle plateau begins, as a fraction of arc length from throat (0) to mouth (1). Only used when Coverage angle is above 0.' }),
  number('icw.hold_end', 'hold_end', 'hold_end', profile, 'Coverage hold end', { families: ['ICW'], unit: 'σ', min: .1, max: .95, step: .01, visibleWhen: (design) => design.termination !== 'rollback', description: 'Where the constant-angle plateau ends, as a fraction of arc length. Must be past the hold start. Only used when Coverage angle is above 0.' }),
  number('icw.n_coeff', 'n_coeff', 'n_coeff', profile, 'Curvature coefficients', { families: ['ICW'], min: 5, max: 24, step: 1, precision: 0, description: 'How many spline coefficients describe the wall curvature. More gives the solver more freedom to shape the wall, and a slower solve. A coverage hold needs at least 9; values from 5 to 8 are dropped and the solver uses 16 instead.' }),
  select('icw.termination', 'termination', 'termination', profile, 'Termination', [{ value: 'flat_baffle', label: 'Flat baffle' }, { value: 'rollback', label: 'Rollback' }], { families: ['ICW'], description: 'How the mouth ends. Flat baffle meets the baffle tangentially at 90° with continuous curvature; Rollback carries the lip past 90° so it curls back on itself.' }),
  number('icw.theta1_deg', 'theta1_deg', 'theta1_deg', profile, 'Rollback curl angle', { families: ['ICW'], unit: '°', min: 91, max: 179, step: 1, precision: 0, visibleWhen: (design) => design.termination === 'rollback', description: 'Wall angle at the curled tip, measured from the forward horn axis. 90° points straight out sideways and larger values turn the tip further back. Rollback terminations only.' }),
  number('icw.depth', 'depth', 'depth', profile, 'Rollback depth', { families: ['ICW'], unit: 'mm', min: 1, max: 1_000, step: 1, visibleWhen: (design) => design.termination === 'rollback', description: 'Axial position of the curled tip, measured forward from the throat — not the frontmost point of the horn, since the 90° crossing sits further forward. A rollback is sized by this instead of Horn length, with Mouth radius setting the aperture.' }),
  // ICW schema additions not present in the v1 centralized inventory. The
  // mesher's ICW builder never reads these four (they are absent from
  // _ICW_PARAM_KEYS), so the tooltip has to say they are inert rather than
  // implying they shape the curve.
  number('icw.r_aperture', 'r_aperture', 'r_aperture', profile, 'Aperture radius', { ...advancedIcw, unit: 'mm', disabledWhen: (design) => rollbackIcwReason(design) ?? (design.R != null ? 'R takes precedence; clear Mouth radius to use this fallback.' : undefined), description: 'Rollback radius at the foremost 90° wall-angle crossing. Used only when R is absent. Unset adds no radial target; rollback needs a radial target. Feasibility is checked by the mesher.' }),
  number('icw.x_aperture', 'x_aperture', 'x_aperture', profile, 'Aperture axial position', { ...advancedIcw, unit: 'mm', disabledWhen: rollbackIcwReason, description: 'Rollback axial coordinate of the foremost 90° crossing, measured from the throat. Unset adds no target. At least one of this, Rollback depth or Tip setback is needed; setting several adds simultaneous constraints.' }),
  number('icw.x_setback', 'x_setback', 'x_setback', profile, 'Tip setback', { ...advancedIcw, unit: 'mm', disabledWhen: rollbackIcwReason, description: 'Rollback aperture axial position minus the curled tip axial position. Positive means the tip sits behind the aperture. Unset adds no target. May be combined with depth and aperture position if all targets are feasible.' }),
  number('icw.kappa0', 'kappa0', 'kappa0', profile, 'Throat curvature', { ...advancedIcw, unit: '1/mm', step: .001, precision: 6, disabledWhen: icwInputReason, description: 'Signed wall curvature at the throat. Unset uses 0, a locally conical throat. There is no fixed scalar range; the requested shape must remain feasible.' }),
  number('icw.kappa_abs_max', 'kappa_abs_max', 'kappa_abs_max', profile, 'Maximum absolute curvature', { ...advancedIcw, unit: '1/mm', min: 0, step: .001, precision: 6, disabledWhen: icwInputReason, description: 'Strictly positive cap on sampled absolute wall curvature. Unset leaves it unbounded. Guides the target solve and is checked afterwards; a violated cap fails rather than silently changing the requested targets.' }),
  number('icw.dkappa_ds_abs_max', 'dkappa_ds_abs_max', 'dkappa_ds_abs_max', profile, 'Maximum curvature slope', { ...advancedIcw, unit: '1/mm per σ', min: 0, step: .001, precision: 6, disabledWhen: icwInputReason, description: 'Strictly positive cap on sampled |dκ/dσ|. Despite the key name, the pinned mesher differentiates with respect to normalized arc length σ, not physical s. Unset leaves it unbounded. Guides the target solve and is verified afterwards.' }),
  number('icw.theta_max_deg', 'theta_max_deg', 'theta_max_deg', profile, 'Maximum wall angle', { ...advancedIcw, unit: '°', min: 90, max: 180, disabledWhen: rollbackIcwReason, description: 'Rollback cap on the sampled wall angle, strictly above 90° and at most 180°. Unset leaves it unbounded. It must allow the terminal angle; it does not choose that angle. Flat-baffle mode ignores it.' }),
  select('icw.pin_mouth_radius', 'pin_mouth_radius', 'pin_mouth_radius', profile, 'Pin mouth radius', [{ value: '', label: 'Default (No)' }, { value: 'false', label: 'No' }, { value: 'true', label: 'Yes' }], { ...advancedIcw, disabledWhen: (design) => icwInputReason(design) ?? (design.termination === 'rollback' || !((design.coverage_angle ?? 0) > 0) ? 'Used only with a flat-baffle coverage hold.' : undefined), description: 'Default is false: coverage hold lets mouth radius emerge. Yes adds R as a hard target during a flat-baffle coverage hold, which can make the targets infeasible. No effect without coverage or on rollback.' }),
  number('icw.icw_S', 'icw_S', 'icw_S', profile, 'Direct curve arc length', { ...advancedIcw, unit: 'mm', min: 0, disabledWhen: (design) => design.icw_seed != null ? 'Seed mode takes precedence over direct coefficients.' : design.icw_coeffs == null ? 'Used only with imported direct coefficients.' : undefined, description: 'Required strictly positive total arc length for imported icw_coeffs. There is no default. Ignored by the target solver and by seed mode.' }),
  { id: 'icw.icw_seed', legacyKey: 'icw_seed', path: 'icw_seed', section: profile, label: 'Imported ICW seed', kind: 'indicator', ...advancedIcw, visibleWhen: (design) => design.icw_seed != null, description: 'Preserved nested OSSE/R-OSSE seed. The mesher fits it in preference to direct coefficients and targets. Edit the native file to change the seed.' },
  { id: 'icw.icw_coeffs', legacyKey: 'icw_coeffs', path: 'icw_coeffs', section: profile, label: 'Imported ICW coefficients', kind: 'indicator', ...advancedIcw, visibleWhen: (design) => design.icw_coeffs != null, description: 'Preserved direct curvature coefficient list; requires Direct curve arc length. Seed mode takes precedence if both are present. Edit the native file to change the list.' },

  // Imported v1 ICW scalars preserved for .cfg round-trip only. The
  // mesher's ICW builder never reads these four (they are absent from
  // _ICW_PARAM_KEYS), so the tooltip has to say they are inert rather than
  // implying they shape the curve.
  number('icw.a', 'a', 'a', profile, 'Legacy coverage coefficient', { families: ['ICW'], unit: '°', min: -180, max: 180, description: 'Kept so an imported file round-trips unchanged. The ICW solver does not read it — the curve comes from the throat, size and coverage targets above.' }),
  number('icw.k', 'k', 'k', profile, 'Legacy flare coefficient', { families: ['ICW'], min: -100, max: 100, description: 'Kept so an imported file round-trips unchanged. The ICW solver does not read it — the curve comes from the throat, size and coverage targets above.' }),
  number('icw.q', 'q', 'q', profile, 'Legacy smoothness coefficient', { families: ['ICW'], min: -100, max: 100, description: 'Kept so an imported file round-trips unchanged. The ICW solver does not read it — the curve comes from the throat, size and coverage targets above.' }),
  number('icw.curl', 'curl', 'curl', profile, 'Curl', { families: ['ICW'], min: -100, max: 100, disabledReason: 'Round-trip only; the ICW mesher does not read Curl. Use Termination and Rollback curl angle instead.', description: 'Kept so an imported file round-trips unchanged. The ICW solver does not read it — use Termination and Rollback curl angle to curl the mouth.' }),

  { id: 'lookup.profile', legacyKey: 'lookupProfile', path: 'lookup_profile', section: profile, label: 'Lookup profile points', kind: 'table', families: ['LOOKUP'], description: 'Experimental whole meridian in millimetres, linearly interpolated between [z, r] samples. Supply at least two finite pairs with strictly increasing z. Include any extension or adapter in this table. Radius may rise or fall.' },
  number('lookup.a0', 'lookupSourceAngle', 'a0', profile, 'Automatic source-cap angle', { families: ['LOOKUP'], unit: '°', min: -90, max: 90, description: 'Angle used for the automatic source cap; it does not reshape the lookup meridian.' }),
  number('freeform.length', 'length', 'length', profile, 'Length', { families: ['FREEFORM'], unit: 'mm', min: 20, max: 1_000, step: 1, description: 'Axial length shared by the horizontal and vertical profiles, spanned by both splines from throat to mouth. Interior anchors are stored as fractions of it, so changing it moves them all proportionally.' }),
  number('freeform.throatRadius', 'throatRadius', 'profile_h.points.0.r', profile, 'Throat radius', { families: ['FREEFORM'], mirrorPaths: ['profile_v.points.0.r'], unit: 'mm', min: .1, max: 200, description: 'Radius at the throat. Shared by both profile planes, so the throat stays circular.' }),
  number('freeform.throatAngle', 'throatAngle', 'profile_h.throat_angle_deg', profile, 'Throat angle', { families: ['FREEFORM'], mirrorPaths: ['profile_v.throat_angle_deg'], unit: '°', min: -90, max: 90, description: 'Wall angle leaving the driver exit, shared by both profile planes, fixing the spline tangent at the throat. An angle set on the first point of a plane overrides it for that plane.' }),
  number('freeform.mouthRadiusH', 'mouthRadiusH', 'profile_h.points.$last.r', profile, 'Horizontal mouth radius', { families: ['FREEFORM'], unit: 'mm', min: .1, max: 1_000, step: 1, description: 'Half-width of the mouth in the horizontal plane.' }),
  number('freeform.mouthAngleH', 'mouthAngleH', 'profile_h.mouth_angle_deg', profile, 'Horizontal mouth angle', { families: ['FREEFORM'], unit: '°', min: -90, max: 90, description: 'Wall angle the horizontal spline arrives at the mouth with, fixing the spline tangent there. An angle set on the last horizontal point overrides it.' }),
  { id: 'freeform.interiorH', legacyKey: 'interiorH', path: 'profile_h.points', section: profile, label: 'Horizontal spline points', kind: 'table', families: ['FREEFORM'], description: 'Optional anchors the horizontal spline must pass through, placed strictly between throat and mouth. Give each a position and radius; set its angle to pin the wall direction there, or leave it blank for an automatic tangent.' },
  number('freeform.mouthRadiusV', 'mouthRadiusV', 'profile_v.points.$last.r', profile, 'Vertical mouth radius', { families: ['FREEFORM'], unit: 'mm', min: .1, max: 1_000, step: 1, description: 'Half-height of the mouth in the vertical plane.' }),
  number('freeform.mouthAngleV', 'mouthAngleV', 'profile_v.mouth_angle_deg', profile, 'Vertical mouth angle', { families: ['FREEFORM'], unit: '°', min: -90, max: 90, description: 'Wall angle the vertical spline arrives at the mouth with, fixing the spline tangent there. An angle set on the last vertical point overrides it.' }),
  { id: 'freeform.interiorV', legacyKey: 'interiorV', path: 'profile_v.points', section: profile, label: 'Vertical spline points', kind: 'table', families: ['FREEFORM'], description: 'Optional anchors the vertical spline must pass through, placed strictly between throat and mouth. Give each a position and radius; set its angle to pin the wall direction there, or leave it blank for an automatic tangent.' },
  { id: 'freeform.crossSections', legacyKey: 'crossSections', path: 'cross_sections', section: profile, label: 'Cross-section stations', kind: 'table', families: ['FREEFORM'], description: 'Cross-section outline at increasing positions along the throat-to-mouth axis, blended between the stations you list; the H and V splines still set each section\'s half-width and half-height. The station at 0 must be a circle or ellipse. Circle uses the local H/V radii just like Ellipse, so it is circular only when they are equal. Superellipse exponents run 2 to 16, and a corner radius has to fit the local half-size.' },
  select('freeform.inflectionPolicy', 'inflectionPolicy', 'inflection_policy', profile, 'Curve direction', [{ value: 'warn', label: 'Warn on S-curves' }, { value: 'reject', label: 'Enforce one-way' }], { families: ['FREEFORM'], description: "An S-curve is a stretch where the wall's outward angle falls back by more than a degree before opening again. Warn records it and builds anyway; Enforce one-way rejects the profile instead." }),

  // Throat extension, morph, coverage/length modes, and complete OSSE guide controls.
  number('common.throat_ext_angle', 'throatExtAngle', 'throat_ext_angle', throatExtension, 'Throat extension angle', { families: osseFamilies, unit: '°', min: -90, max: 90, description: 'Taper of the separate conical adapter behind the main horn. The driver end works out as r0 minus the extension length times tan of this angle, so positive angles narrow it toward the driver and negative ones widen it; 0 makes a straight tube. This angle is independent of a0: equal values make a tangent OS-SE join, while R-OSSE\'s finished tangent also depends on m, r and q.' }),
  number('common.throat_ext_length', 'throatExtLength', 'throat_ext_length', throatExtension, 'Throat extension length', { families: osseFamilies, unit: 'mm', min: 0, max: 1_000, description: 'Length of the conical adapter added behind the throat, between the driver and the horn. 0 leaves it out. It does not change the computed OS-SE or R-OSSE flare; it increases the physical total length by this amount.' }),
  number('common.slot_length', 'slotLength', 'slot_length', throatExtension, 'Straight slot length', { families: osseFamilies, unit: 'mm', min: 0, max: 1_000, description: 'Length of a straight r0 section between the throat extension and the start of the flare; 0 leaves it out. Under OSSE Total length mode it is subtracted from Horn length rather than added. A non-zero Shape factor h bulges it too, so it is not perfectly straight.' }),
  select('common.length_mode', 'lengthMode', 'length_mode', throatExtension, 'Length mode', [{ value: 'profile', label: 'Profile length' }, { value: 'total', label: 'Total length' }], { families: osseFamilies, description: 'What Horn length measures. Profile length leaves all of it for the flare, so the slot makes the part longer; Total length subtracts the slot from it instead, shortening the flare. The throat extension is added on top either way. R-OSSE derives its own length and ignores this.' }),
  { id: 'common.coverage_mode', legacyKey: 'coverageMode', path: 'coverage_mode', section: profile, label: 'Coverage mode', kind: 'text', families: ['ICW'], description: 'Carried through from the imported file so it round-trips unchanged. Nothing in the solve reads it; the coverage hold is driven by Coverage angle above.' },
  select('morph.target_shape', 'morphTarget', 'morph.target_shape', morphTarget, 'Morph target', [{ value: 0, label: 'None' }, { value: 1, label: 'Rectangle' }, { value: 2, label: 'Circle' }, { value: 3, label: 'Superellipse' }], { families: ['R-OSSE', 'OSSE', 'ICW', 'FREEFORM', 'LOOKUP'], description: 'Outline the mouth is blended toward as it approaches the baffle. None keeps the outline the profile formula produces. A Circle takes half the larger target extent as its radius, so on an anisotropic mouth it expands the smaller axis and cannot preserve both mouth endpoints. Only Superellipse is shape-only; exponent 2 gives an ellipse.' }),
  number('morph.target_width', 'morphWidth', 'morph.target_width', morphTarget, 'Target width', { families: ['R-OSSE', 'OSSE', 'ICW', 'FREEFORM', 'LOOKUP'], unit: 'mm', min: 0, max: 2_000, visibleWhen: (design) => design.morph.target_shape !== 0, description: 'Full target width. For Rectangle and Circle, 0 derives it from the mouth by rounding the half-width up to a whole millimetre before doubling. For Superellipse, 0 uses the exact full mouth width. A Circle uses the larger of width and height as its diameter.' }),
  number('morph.target_height', 'morphHeight', 'morph.target_height', morphTarget, 'Target height', { families: ['R-OSSE', 'OSSE', 'ICW', 'FREEFORM', 'LOOKUP'], unit: 'mm', min: 0, max: 2_000, visibleWhen: (design) => design.morph.target_shape !== 0, description: 'Full target height. For Rectangle and Circle, 0 derives it from the mouth by rounding the half-height up to a whole millimetre before doubling. For Superellipse, 0 uses the exact full mouth height. A Circle uses the larger of width and height as its diameter.' }),
  number('morph.target_exponent', 'morphExponent', 'morph.target_exponent', morphTarget, 'Target exponent', { families: ['R-OSSE', 'OSSE', 'ICW', 'FREEFORM', 'LOOKUP'], min: 2, max: 16, visibleWhen: (design) => design.morph.target_shape === 3, description: 'Exponent of the Superellipse target. 2, the default, gives an ellipse; higher values square off the outline toward a rounded rectangle. An expression in the azimuthal angle p is evaluated around the mouth.' }),
  number('morph.corner_radius', 'morphCorner', 'morph.corner_radius', morphTarget, 'Corner radius', { families: ['R-OSSE', 'OSSE', 'ICW', 'FREEFORM', 'LOOKUP'], unit: 'mm', min: 0, max: 100, step: 1, visibleWhen: (design) => design.morph.target_shape === 1, description: 'Rounds the corners of a Rectangle target, clamped to the smaller half-dimension of that target. It has no effect on a Circle or Superellipse target. On R-OSSE, OSSE and ICW, 0 gives a sharp rectangle. FREEFORM rejects a morph whose intermediate outlines turn non-convex, which a small corner or a target far from the drawn mouth can cause; the error names a minimum corner radius only when some radius would pass. For a rectangular FREEFORM mouth, make the mouth cross-section station a Rounded rectangle instead.' }),
  number('morph.rate', 'morphRate', 'morph.rate', morphTarget, 'Morph rate', { families: ['R-OSSE', 'OSSE', 'ICW', 'FREEFORM', 'LOOKUP'], min: 0, max: 100, visibleWhen: (design) => design.morph.target_shape !== 0, description: 'Exponent on the blend once morphing starts. 1 is a straight ramp; higher values hold the formula shape longer and swing to the target near the mouth; 0 jumps straight to the target.' }),
  number('morph.fixed_part', 'morphFixed', 'morph.fixed_part', morphTarget, 'Fixed part', { families: ['R-OSSE', 'OSSE', 'ICW', 'FREEFORM', 'LOOKUP'], min: 0, max: 1, step: .01, visibleWhen: (design) => design.morph.target_shape !== 0, description: 'Fraction of the profile from the throat left untouched before blending starts; 0 blends from the throat. 1 disables morphing altogether, as does any value at or above Truncation limit on R-OSSE. Throat extensions and slots are never morphed.' }),
  select('morph.allow_shrinkage', 'morphAllowShrinkage', 'morph.allow_shrinkage', morphTarget, 'Allow shrinkage', yesNo, { families: ['R-OSSE', 'OSSE', 'ICW', 'FREEFORM', 'LOOKUP'], visibleWhen: (design) => design.morph.target_shape !== 0, description: 'Whether the target may be smaller than the mouth the formula produced. No raises the target width and height to at least the mouth bounds, so a smaller target is enlarged rather than honoured.' }),
  select('osse.throat_profile', 'throatProfile', 'throat_profile', guidingCurve, 'Throat profile', [{ value: 1, label: 'OS-SE' }, { value: 3, label: 'Circular arc (legacy)', currentOnly: true }], { families: ['OSSE'], description: 'OS-SE carries the wall from throat to mouth. Existing native designs can retain the legacy circular arc, driven by the two arc controls below. The legacy arc ignores a0, k, s, n and q; Shape factor h and Profile rotation still apply. For a terminating arc on a new design, use OSSE-ARC.' }),
  number('osse.rotation', 'rot', 'rotation', guidingCurve, 'Profile rotation', { families: ['OSSE'], unit: '°', min: -360, max: 360, description: 'Rotates each meridian about the point [0, r0] before it is revolved, which reshapes the surface rather than tilting the finished horn rigidly. A p-dependent value turns each meridian differently and warps the mouth.' }),
  select('guide.curve_type', 'gcurveType', 'guiding_curve.curve_type', guidingCurve, 'Guiding curve mode', [{ value: 0, label: 'Explicit coverage' }, { value: 1, label: 'Superellipse' }, { value: 2, label: 'Superformula' }], { families: ['OSSE'], description: 'Where the mouth coverage angle comes from. Explicit uses the angle you typed; the other two solve, per azimuth, the coverage angle that puts the OS-SE radius on the guide shape at the guide distance, overriding that field. The solved angle is clamped to 0.5°–89°.' }),
  number('guide.distance', 'gcurveDist', 'guiding_curve.distance', guidingCurve, 'Guiding curve distance', { families: ['OSSE'], min: 0, max: 10_000, description: 'Which station along the flare the guide shape is matched at. Above 0 through 1 is a fraction of the main length; above 1 is millimetres, clamped to the mouth. 0 means the mouth.' }),
  number('guide.width', 'gcurveWidth', 'guiding_curve.width', guidingCurve, 'Guiding curve width', { families: ['OSSE'], unit: 'mm', min: 0, max: 2_000, description: 'Horizontal size of the guide shape; 0 disables guiding and falls back to the explicit coverage angle. It is the exact full width for a Superellipse, but only a base scale for a Superformula, whose own terms change the real span.' }),
  number('guide.aspect_ratio', 'gcurveAspectRatio', 'guiding_curve.aspect_ratio', guidingCurve, 'Guiding curve aspect ratio', { families: ['OSSE'], min: .01, max: 100, description: 'Vertical scale of the guide shape divided by its horizontal scale. 1 makes the two equal — a circle at superellipse exponent 2, not a square — and below 1 makes it wider than tall.' }),
  number('guide.superellipse_n', 'gcurveSeN', 'guiding_curve.superellipse_n', guidingCurve, 'Guiding superellipse exponent', { families: ['OSSE'], min: .01, max: 100, visibleWhen: (design) => design.guiding_curve.curve_type === 1, description: 'Exponent of the superellipse guide shape. 2 gives an ellipse and higher values square it off toward a rounded rectangle. Anything below 2 is clamped up to 2.' }),
  number('guide.superformula', 'gcurveSf', 'guiding_curve.superformula', guidingCurve, 'Superformula tuple', { families: ['OSSE'], min: -1_000, max: 1_000, visibleWhen: (design) => design.guiding_curve.curve_type === 2, description: 'All six superformula terms as one comma-separated list, in the order a, b, m, n1, n2, n3. A complete six-value list overrides every individual field below and uses its single m for both m1 and m2. A single value (such as 0) means not given, and the individual fields apply. A list of two to five values is refused with an error.' }),
  ...(['sf_a', 'sf_b', 'sf_m1', 'sf_m2', 'sf_n1', 'sf_n2', 'sf_n3'] as const).map((key) => number(`guide.${key}`, `gcurveSf${key.slice(3).toUpperCase()}`, `guiding_curve.${key}`, guidingCurve, `Superformula ${key.slice(3)}`, { families: ['OSSE'], min: -1_000, max: 1_000, visibleWhen: (design) => design.guiding_curve.curve_type === 2, description: SUPERFORMULA_HELP[key] })),
  number('guide.rotation', 'gcurveRot', 'guiding_curve.rotation', guidingCurve, 'Guiding curve rotation', { families: ['OSSE'], unit: '°', min: -360, max: 360, description: 'Rotates the guide shape counterclockwise about the horn axis, turning the coverage pattern with it. A p-dependent expression is evaluated per azimuth and distorts the outline instead of turning it.' }),
  number('osse.circ_arc_term_angle', 'circArcTermAngle', 'circ_arc_term_angle', guidingCurve, 'Circular arc terminal angle', { families: ['OSSE'], unit: '°', min: -180, max: 180, visibleWhen: (design) => design.throat_profile === 3, description: 'Wall angle the arc reaches at the mouth. Used only when Throat profile is Circular arc (legacy) and Radius override is not in play. An angle parallel to the throat-to-mouth chord has no solution and falls back to a constant mouth radius.' }),
  number('osse.circ_arc_radius', 'circArcRadius', 'circ_arc_radius', guidingCurve, 'Circular arc radius override', { families: ['OSSE'], unit: 'mm', min: 0, max: 10_000, visibleWhen: (design) => design.throat_profile === 3, description: 'Forces the arc radius and takes precedence over Terminal angle. It is honoured only when positive and at least half the throat-to-mouth chord; 0 or an impossible radius silently falls back to Terminal angle. Circular arc (legacy) only.' }),

  // Source including schema-carried contours and explicit velocity convention.
  select('source.shape', 'sourceShape', 'source.shape', sourceDefinition, 'Source surface', [{ value: 1, label: 'Spherical cap' }, { value: 2, label: 'Flat disc' }], { description: 'Shape of the driving surface placed at the throat. A spherical cap approximates a dome radiating into the horn; a flat disc models a flat piston.' }),
  number('source.radius', 'sourceRadius', 'source.radius', sourceDefinition, 'Source radius', { unit: 'mm', min: -1, max: 2_000, description: 'Sphere radius of a spherical-cap source; a flat disc ignores it. A value of 0 or below derives the radius from the throat radius and throat angle instead of setting it directly.' }),
  select('source.curvature', 'sourceCurv', 'source.curvature', sourceDefinition, 'Source curvature', [{ value: 0, label: 'Auto' }, { value: 1, label: 'Convex' }, { value: -1, label: 'Concave' }], { description: 'Which way a spherical-cap source bulges, seen from the horn. Auto picks the usual direction for the chosen surface.' }),
  number('source.velocity', 'sourceAmplitude', 'source.velocity', sourceDefinition, 'Source amplitude', { unit: 'm/s', min: 0, max: 100, step: .01, precision: 3, visibleWhen: (design) => design.source.velocity_convention === 'legacy', description: 'Despite the name this never scales the result: the solve always drives the source at unit velocity. It is read only under the Legacy convention, where it carries ATH\'s direction code and must be exactly 1 (normal) or 2 (axial).' }),
  select('source.velocity_convention', 'sourceVelocity', 'source.velocity_convention', sourceDefinition, 'Velocity convention', [{ value: 'normal', label: 'Normal velocity' }, { value: 'axial', label: 'Axial rigid-piston velocity' }, { value: 'legacy', label: 'Legacy config value', currentOnly: true }], { description: 'Which way the source surface moves. Normal drives every point along its own surface normal; Axial drives it as a rigid piston along the horn axis. Legacy takes the same choice from the numeric code in Source amplitude instead.' }),
  { id: 'source.contours', legacyKey: 'sourceContours', path: 'source.contours', section: sourceDefinition, label: 'Source contours', kind: 'text', description: 'Legacy ATH Source.Contours is preserved for round-trip only; a non-empty value stops waveguide preview and solve meshing. For native custom contours, use “Edit source contour…” (Experimental) on the Geometry tab.' },

  number('cross_section.exponent', 'cross_section.exponent', 'cross_section.exponent', profile, 'Cross-section exponent', { families: ['OSSE', 'R-OSSE', 'ICW', 'LOOKUP'], min: 0, disabledWhen: guidingCurveSectionReason, description: 'Dimensionless superellipse exponent. 2 gives an ellipse; larger values make squarer sections. Sets the base section before morphing. Active guiding curves require exponent 2 and aspect ratio 1. FREEFORM uses its station outlines and native formulas require circular sections.' }),
  number('cross_section.aspect_ratio', 'cross_section.aspect_ratio', 'cross_section.aspect_ratio', profile, 'Cross-section aspect ratio', { families: ['OSSE', 'R-OSSE', 'ICW', 'LOOKUP'], min: 0, disabledWhen: guidingCurveSectionReason, description: 'Dimensionless width-to-height scaling of the base section before morphing. 1 keeps equal extents for a scalar profile. Ellipses retain both mirror planes; rotated or azimuth-dependent profiles are checked by the mesher. Active guiding curves require ratio 1.' }),
  // Full enclosure schema block.
  number('enclosure.depth', 'encDepth', 'enclosure.depth', wallEnclosure, 'Enclosure depth', { unit: 'mm', min: 0, max: 2_000, step: 1, visibleWhen: (design) => design.enclosure.depth > 0, description: 'Depth of the box behind the front baffle. A positive value is raised if needed to clear the horn — at least its axial span plus the depth margin. 0 means no box, leaving a free-standing horn shaped by Wall thickness. Infinite-baffle simulations ignore the enclosure entirely.' }),
  number('enclosure.depth_margin_mm', 'enclosure.depth_margin_mm', 'enclosure.depth_margin_mm', wallEnclosure, 'Rear clearance', { unit: 'mm', min: 0, visibleWhen: (design) => design.enclosure.depth > 0, description: 'Minimum clearance behind the horn. Raises the enclosure depth to at least the horn axial span plus this margin. Defaults to 1 mm.' }),
  number('enclosure.edge_radius', 'encEdge', 'enclosure.edge_radius', wallEnclosure, 'Edge radius', { unit: 'mm', min: 0, max: 1_000, visibleWhen: (design) => design.enclosure.depth > 0, description: 'Size of the roundover or bevel on the outer box edges, front and rear. The front edge is the main control over baffle diffraction. It is reduced automatically to fit the box and to leave the baffle margins intact.' }),
  select('enclosure.edge_type', 'encEdgeType', 'enclosure.edge_type', wallEnclosure, 'Edge finish', [{ value: 1, label: 'Rounded' }, { value: 2, label: 'Chamfered' }], { visibleWhen: (design) => design.enclosure.depth > 0, description: 'Whether the front edges are cut as a circular roundover or a flat bevel. Both use Edge radius for their size.' }),
  ...([['space_l', 'encSpaceL', 'Left margin', 'left'], ['space_t', 'encSpaceT', 'Top margin', 'top'], ['space_r', 'encSpaceR', 'Right margin', 'right'], ['space_b', 'encSpaceB', 'Bottom margin', 'bottom']] as const).map(([path, key, label, side]) => number(`enclosure.${path}`, key, `enclosure.${path}`, wallEnclosure, label, { unit: 'mm', min: 0, max: 2_000, visibleWhen: (design) => design.enclosure.depth > 0, description: `Flat baffle left between the horn mouth and the ${side} edge of the box, which is what sets the box size on that side. A positive margin is never eaten by Edge radius — the roundover shrinks instead.` })),
  number('enclosure.front_resolution', 'encFrontResolution', 'enclosure.front_resolution', wallEnclosure, 'Front baffle mesh resolution', { kind: 'text', unit: 'mm', visibleWhen: (design) => design.enclosure.depth > 0, description: 'Target element sizes in mm on the enclosure front baffle. Enter one value for every quadrant, or up to four comma-separated values: q1 (+x,+y), q2 (-x,+y), q3 (-x,-y), q4 (+x,-y). Short lists repeat their last value; 8,12 means 8,12,12,12. Non-positive entries use the mouth resolution. Unequal reflected quadrant sizes prevent that symmetry reduction.' }),
  number('enclosure.back_resolution', 'encBackResolution', 'enclosure.back_resolution', wallEnclosure, 'Rear baffle mesh resolution', { kind: 'text', unit: 'mm', visibleWhen: (design) => design.enclosure.depth > 0, description: 'Target element sizes in mm on the enclosure rear panel. Enter one value for every quadrant, or up to four comma-separated values: q1 (+x,+y), q2 (-x,+y), q3 (-x,-y), q4 (+x,-y). Short lists repeat their last value; 8,12 means 8,12,12,12. Non-positive entries use the mouth resolution. Unequal reflected quadrant sizes prevent that symmetry reduction.' }),

  // How the horn surface itself is sampled, which is what reaches the exported
  // and solved geometry. The preview is error-bounded rather than segment
  // driven: it derives its own azimuthal and axial sampling from a chord-error
  // budget and overwrites the angular and length counts (see
  // server/preview/core.py preview_options). Corner samples and the Z-map do
  // survive into it, so only the first two are labelled as export-only.
  number('mesh.angular_segments', 'angularSegments', 'mesh.angular_segments', surfaceSampling, 'Surface angular samples', { min: 0, max: 4_096, step: 1, precision: 0, description: 'Starting number of azimuthal samples around the horn for the exported and solved mesh; the mesher may raise it to hit the mm resolution targets. Values below 4 become 4 and others round up to a multiple of 4. The viewport uses its own adaptive count.' }),
  number('mesh.length_segments', 'lengthSegments', 'mesh.length_segments', surfaceSampling, 'Surface length samples', { min: 0, max: 4_096, step: 1, precision: 0, description: 'Starting number of axial intervals along the profile for the exported and solved mesh — one more station than intervals. The mesher may raise it to hit the mm resolution targets, and the viewport uses its own adaptive count.' }),
  number('mesh.corner_segments', 'cornerSegments', 'mesh.corner_segments', surfaceSampling, 'Surface corner samples', { min: 0, max: 1_024, step: 1, precision: 0, description: 'Extra azimuthal budget spent on the corners of an active Rectangle morph, giving (angular + corner) / 4 points per quadrant. It does nothing without that morph. Unlike the two counts above, it does reach the viewport.' }),
  select('mesh.sampling_mode', 'samplingMode', 'mesh.sampling_mode', surfaceSampling, 'Z-map sampling mode', [{ value: 'uniform', label: 'Uniform' }, { value: 'ath-default-zmap', label: 'ATH default Z-map' }, { value: 'zmap', label: 'Custom Z-map points' }], { description: 'How axial stations are spread along the profile. OSSE and R-OSSE take all three modes; FREEFORM takes Uniform or a custom map only, and ICW is always uniform in arc length and rejects a custom map. A custom map reaches the viewport as well as the export.' }),
  { id: 'mesh.z_map_points', legacyKey: 'zMapPoints', path: 'mesh.z_map_points', section: surfaceSampling, label: 'Z-map points', kind: 'text', visibleWhen: (design) => design.mesh.sampling_mode === 'zmap', description: 'The custom axial station map, as increasing interior x,y pairs inside 0–1 — the endpoints are added for you. Alternatively give one non-decreasing value per station, from 0 to 1. It is parsed and applied, not just stored.' },
  number('mesh.throat_resolution', 'throatResolution', 'mesh.throat_resolution', solveExportMesh, 'Throat mesh resolution', { unit: 'mm', min: .01, max: 1_000, description: 'Target triangle edge length near the throat in the solved and exported mesh. The element size is blended from here to the mouth value. Smaller values cost solve time and memory.' }),
  number('mesh.mouth_resolution', 'mouthResolution', 'mesh.mouth_resolution', solveExportMesh, 'Mouth mesh resolution', { unit: 'mm', min: .01, max: 1_000, description: 'Target triangle edge length near the mouth in the solved and exported mesh. It has to resolve the shortest wavelength you solve: for 20 kHz, λ/6 is approximately 2.86 mm.' }),
  number('schema-gap.max_edge', 'maxEdge', 'mesh.max_edge', solveExportMesh, 'Maximum edge guard', { unit: 'mm', min: .01, max: 10_000, description: 'Optional post-build guard for the longest realized triangle edge.' }),
  number('mesh.rear_resolution', 'rearResolution', 'mesh.rear_resolution', solveExportMesh, 'Rear mesh resolution', { unit: 'mm', min: .01, max: 1_000, description: 'Target triangle edge length over the whole outside shell and rear throat closure of a free-standing thickened horn. Those surfaces radiate far less than the horn interior, so they can usually be coarser. Unused with an enclosure or in infinite baffle.' }),
  // Sizes the infinite-baffle aperture cap and nothing else, so a free-standing
  // design never reads it — on any backend, not just the ones without Metal.
  number('mesh.aperture_resolution_scale', 'apertureResolutionScale', 'mesh.aperture_resolution_scale', solveExportMesh, 'Aperture mesh scale', { min: 1, max: 100, visibleWhen: (design) => design.simulation.sim_type === 'infinite-baffle', description: 'Multiplies the element size inside the infinite-baffle aperture cap relative to Mouth mesh resolution. 1 makes the cap use the Mouth mesh resolution. Above 1 coarsens the cap to save solve time, at the cost of accuracy above roughly 6–8 kHz. Values below 1 are refused when the mesh is built.' }),
  number('mesh.max_triangles', 'maxTriangles', 'mesh.max_triangles', solveExportMesh, 'Triangle warning threshold', { min: 1, max: 10_000_000, step: 1_000, precision: 0, description: 'Advisory warning threshold, in full-domain-equivalent triangles. Solves continue past it, but two hard ceilings you cannot raise from here will still stop one: 22,000 triangles in the actual solved domain, and the dense-solver memory limit.' }),
  // Kept in the registry solely for text/wire traceability. The old approval
  // control is intentionally absent from the panel because the threshold is
  // advisory and the independent process-safety ceiling cannot be bypassed.
  select('mesh.allow_large_mesh', 'allowLargeMesh', 'mesh.allow_large_mesh', solveExportMesh, 'Legacy large-mesh approval', [{ value: 0, label: 'Ignored' }, { value: 1, label: 'Ignored' }], { visibleWhen: () => false, description: 'Compatibility-only imported value; it does not change solve limits.' }),
  number('mesh.vertical_offset', 'verticalOffset', 'mesh.vertical_offset', solveExportMesh, 'Export vertical offset', { unit: 'mm', min: -10_000, max: 10_000, description: 'Shifts the exported design vertically, for placing a horn off-centre on its baffle. It applies only to the solid STEP and Send to CAD exports. The viewport, the solver mesh, every acoustic result and the recentred exports (STL, surface STEP, profile CSV) are all built in the recentred frame, so simulation results never depend on it.' }),
  number('mesh.wall_thickness', 'wallThickness', 'mesh.wall_thickness', wallEnclosure, 'Wall thickness', { unit: 'mm', min: 0, max: 1_000, description: "Thickness of the shell built around a free-standing horn, offset from its surface and closed by a disc behind the throat. Used only when Enclosure depth is 0 and the simulation is free-standing; infinite baffle ignores it. Unset means ATH's 5 mm default free-standing, and 0 in infinite baffle.", visibleWhen: (design) => design.enclosure.depth <= 0 && design.mesh.wall_thickness > 0 }),

  number('simulation.f1', 'freqStart', 'simulation.f1', frequencySweep, 'Sweep start', { unit: 'Hz', min: 20, max: 20_000, step: 10, precision: 0, description: 'Lowest frequency of the generated sweep; defaults to 50 Hz. Ignored when Solve options is set to an explicit frequency list.' }),
  number('simulation.f2', 'freqEnd', 'simulation.f2', frequencySweep, 'Sweep end', { unit: 'Hz', min: 20, max: 20_000, step: 10, precision: 0, description: 'Highest frequency of the generated sweep; raising it may need a finer Mouth mesh resolution to resolve the shorter wavelength. Ignored when Solve options is set to an explicit frequency list.' }),
  number('simulation.num_frequencies', 'numFreqs', 'simulation.num_frequencies', frequencySweep, 'Frequency samples', { min: 10, max: 200, step: 1, precision: 0, description: 'How many frequencies are solved between the start and end; new designs use 32. Every frequency costs about the same, so this sets solve time almost directly. Spacing is chosen under Solve options, which also overrides this entirely when an explicit list is used.' }),
  select('simulation.sim_type', 'simType', 'simulation.sim_type', solveExportMesh, 'Simulation type', [{ value: 'freestanding', label: 'Free-standing' }, { value: 'infinite-baffle', label: 'Infinite baffle', requiresFeature: 'infinite-baffle' }], { description: 'What the horn radiates into. Free-standing solves the whole body in open air, with the enclosure or wall shell you configured. Infinite baffle mounts the mouth flush in an unbounded rigid wall, ignoring both of those and removing all cabinet-edge diffraction; availability follows the solver capabilities reported by this host.' }),
  // These saved fields remain in the document for ATH text fidelity, but none
  // is a solve-domain control. Runtime symmetry replaces Mesh.Quadrants before
  // a parametric mesh is built, while geometry export always restores all four
  // quadrants so the downloaded part is whole. The other two are similarly
  // inert here: the mesher rejects Mesh.ThroatSegments outright and never reads
  // a throat slice density.
  { id: 'mesh.quadrants', legacyKey: 'quadrants', path: 'mesh.quadrants', section: 'Output & Passthrough', label: 'Stored ATH Mesh.Quadrants', kind: 'indicator', description: 'Preserved for ATH round-trip only. WG overwrites this stored value from Solve domain before a parametric solve and ignores it for geometry export, which always restores the full part.' },
  number('mesh.throat_segments', 'throatSegments', 'mesh.throat_segments', 'Output & Passthrough', 'ATH throat slice samples', { min: 0, max: 4_096, step: 1, precision: 0, disabledReason: 'Mesh.ThroatSegments has no effect on the mesh. Preserved for ATH round-trip only; use Z-map sampling to refine the throat.', description: 'Mesh.ThroatSegments has no effect on the mesh and is refused on ATH import. Preserved for round-trip only. Use Z-map sampling to bias axial stations toward the throat.' }),
  number('mesh.throat_slice_density', 'throatSliceDensity', 'mesh.throat_slice_density', 'Output & Passthrough', 'ATH throat slice density', { min: .01, max: .99, step: .01, description: 'Preserved for ATH round-trip only; the HornLab mesher does not read it.' }),

  select('output.stl', 'outputSTL', 'output.stl', 'Output & Passthrough', 'STL output flag', yesNo, { description: "ATH's own Output.STL flag, kept so an imported file round-trips unchanged. It does not drive this app's exports — use the export menu for that." }),
  select('output.msh', 'outputMSH', 'output.msh', 'Output & Passthrough', 'MSH output flag', yesNo, { description: "ATH's own Output.MSH flag, kept so an imported file round-trips unchanged. It does not drive this app's exports — use the export menu for that." }),
  { id: 'passthrough.abec', legacyKey: 'ABEC', section: 'Output & Passthrough', label: 'Other ABEC passthrough blocks', kind: 'indicator', description: 'Whether the imported file carried ABEC project blocks other than the polar blocks managed by Directivity Map. These remaining blocks are not edited here and are written back unchanged.' },
  { id: 'passthrough.report', legacyKey: 'Report', section: 'Output & Passthrough', label: 'Report passthrough block', kind: 'indicator', description: "Whether the imported file carried ATH's Report block. It is written back out unchanged on export; this app's own plots are configured in the results dock instead." },
  { id: 'passthrough.extra', legacyKey: 'extraBlocks', section: 'Output & Passthrough', label: 'Other passthrough blocks', kind: 'indicator', description: 'Any further named blocks in the imported file that this app does not model. They are preserved verbatim and written back out on export.' },
  { id: 'passthrough.keys', legacyKey: 'extraKeys', section: 'Output & Passthrough', label: 'Unknown flat keys', kind: 'indicator', description: 'Top-level settings in the imported file that this app does not model. They are preserved verbatim and written back out on export, so nothing is lost in a round-trip.' },
];

const icwSeedReason = (design: DesignDocument) => design.icw_seed != null
  ? 'Seed mode uses the throat values stored in the imported seed.' : undefined;
const icwTargetIds = new Set(['icw.L', 'icw.R', 'icw.coverage_angle', 'icw.hold_start', 'icw.hold_end', 'icw.termination', 'icw.theta1_deg', 'icw.depth']);
LEGACY_PARAMETER_REGISTRY.forEach((field) => {
  if (['icw.r0', 'icw.a0'].includes(field.id)) field.disabledWhen = icwSeedReason;
  if (field.id === 'icw.n_coeff') field.disabledWhen = (design) => design.icw_seed == null && design.icw_coeffs != null
    ? 'Direct mode uses the imported coefficient list without a fitted basis.' : undefined;
  if (!icwTargetIds.has(field.id)) return;
  const prior = field.disabledWhen;
  field.disabledWhen = (design) => icwInputReason(design) ?? prior?.(design);
});

const familyTraceKeys: Record<Exclude<DesignFamily, NativeFamily>, readonly string[]> = {
  'R-OSSE': ['scale', 'R', 'a', 'a0', 'r0', 'k', 'm', 'b', 'r', 'q', 'tmax'],
  OSSE: ['scale', 'L', 'a', 'a0', 'r0', 'k', 's', 'n', 'q', 'h'],
  ICW: ['scale', 'r0', 'a0', 'L', 'R', 'coverage_angle', 'hold_start', 'hold_end', 'n_coeff', 'termination', 'theta1_deg', 'depth', ...ICW_ADVANCED_FIELD_IDS.map((id) => id.slice(4))],
  LOOKUP: ['scale', 'lookupProfile', 'lookupSourceAngle'],
  FREEFORM: ['scale', 'length', 'throatRadius', 'throatAngle', 'mouthRadiusH', 'mouthAngleH', 'interiorH', 'mouthRadiusV', 'mouthAngleV', 'interiorV', 'crossSections', 'inflectionPolicy'],
};

const commonTraceKeys = [
  'throatExtAngle', 'throatExtLength', 'slotLength',
  'morphTarget', 'morphWidth', 'morphHeight', 'morphExponent', 'morphCorner', 'morphRate', 'morphFixed', 'morphAllowShrinkage',
  'wallThickness', 'encDepth', 'encEdge', 'encEdgeType', 'encSpaceL', 'encSpaceT', 'encSpaceR', 'encSpaceB',
  'throatProfile', 'rot', 'gcurveType', 'gcurveDist', 'gcurveWidth', 'gcurveAspectRatio', 'gcurveSeN', 'gcurveSf',
  'gcurveSfA', 'gcurveSfB', 'gcurveSfM1', 'gcurveSfM2', 'gcurveSfN1', 'gcurveSfN2', 'gcurveSfN3', 'gcurveRot', 'circArcTermAngle', 'circArcRadius',
  'angularSegments', 'lengthSegments', 'cornerSegments', 'throatSegments', 'throatSliceDensity',
  'freqStart', 'freqEnd', 'numFreqs', 'sourceShape', 'sourceRadius', 'sourceCurv', 'sourceVelocity',
  'simType', 'throatResolution', 'mouthResolution', 'rearResolution', 'apertureResolutionScale',
  'maxTriangles', 'allowLargeMesh', 'verticalOffset', 'quadrants', 'encFrontResolution', 'encBackResolution',
] as const;

/** Current family-qualified parameter inventory, with family duplicates qualified. */
// Unqualified legacy controls apply to the original families. Native shapes
// expose only their canonical controls and the shared execution settings.
LEGACY_PARAMETER_REGISTRY.forEach((field) => {
  if (!field.families) field.families = [...allFamilies];
  if (field.id.startsWith('simulation.f') || field.id === 'simulation.num_frequencies'
    || field.section === 'Output & Passthrough' && field.kind === 'indicator') {
    field.families = [...allFamilies, ...NATIVE_FAMILIES];
  }
});

export const TRACEABILITY_PARAMETER_INVENTORY = [
  ...Object.entries(familyTraceKeys).flatMap(([family, keys]) => keys.map((key) => ({ key, family: family as DesignFamily }))),
  ...commonTraceKeys.map((key) => ({ key, family: undefined })),
];

export const CAD_PARAMETER_SECTION_DEFINITIONS: readonly ParameterSectionDefinition[] = [
  {
    title: 'Profile Dimensions',
    tab: 'geometry',
    description: 'Primary dimensions for the selected horn family. OSSE and R-OSSE labels carry their ATH formula symbol in parentheses.',
    visibleWhen: waveguideDefinitionOnly,
  },
  {
    title: 'Throat Extension',
    tab: 'geometry',
    description: 'Optional conical throat extension and initial straight slot controls for OSSE-family profiles.',
    visibleWhen: waveguideDefinitionOnly,
  },
  {
    title: 'Morph Target',
    tab: 'geometry',
    description: 'Post-profile shaping that transitions the mouth toward another target shape. Target extents smaller than the mouth are enlarged back to it unless Allow shrinkage is on, so a smaller width or height changes nothing on its own. Leave an extent at 0 to derive it from the mouth.',
    visibleWhen: waveguideDefinitionOnly,
  },
  {
    title: 'Wall & Enclosure',
    tab: 'geometry',
    description: 'Freestanding wall-shell controls and enclosure clearances that change the exported or simulated solid.',
    visibleWhen: waveguideDefinitionOnly,
  },
  {
    title: 'Guiding Curve',
    tab: 'geometry',
    description: 'OSSE-only throat profile, rotation, and guide-shape controls used to bend or infer the horn profile.',
    visibleWhen: waveguideDefinitionOnly,
  },
  {
    title: 'Surface sampling',
    tab: 'geometry',
    description: 'How the horn surface is sampled into a mesh for export and for the BEM solve. The live viewport ignores the angular and length counts — it tessellates adaptively to a chord-error budget and refines on its own once you stop editing — so raising them costs solve time without making the preview smoother.',
    visibleWhen: parametricWorkspaceOnly,
  },
  {
    title: 'Frequency Sweep',
    tab: 'simulation',
    description: 'Backend BEM sweep start, end, and sample count. These stay aligned with import and export config keys.',
    visibleWhen: parametricWorkspaceOnly,
  },
  {
    title: 'Source Definition',
    tab: 'simulation',
    description: 'Source surface, orientation, and contour inputs used to build the radiating boundary.',
    visibleWhen: parametricWorkspaceOnly,
  },
  {
    title: 'Solve & export mesh',
    tab: 'simulation',
    description: 'HornLab mesher sizing and export-coordinate controls used for solves, downloads, and persisted mesh artifacts.',
    visibleWhen: parametricWorkspaceOnly,
  },
  {
    title: 'Output & Passthrough',
    tab: 'simulation',
    description: 'Output flags, plus imported configuration content preserved verbatim for lossless export. Values here round-trip; they do not change the viewport, the solve, or the exported geometry.',
    visibleWhen: parametricWorkspaceOnly,
  },
];

/** Frozen presentation for CAD and the versioned public catalog. Document paths
 * and wire metadata stay unchanged when the parametric editor is reorganised. */
const sectionMoves: Partial<Record<ParameterSection, ParameterSection>> = {
  'Profile Dimensions': 'Profile', 'Throat Extension': 'Throat',
  'Morph Target': 'Mouth', 'Guiding Curve': 'Cross-section',
  'Wall & Enclosure': 'Mounting', 'Surface sampling': 'Mesh',
  'Frequency Sweep': 'Frequency sweep', 'Source Definition': 'Source excitation',
  'Solve & export mesh': 'Mesh', 'Output & Passthrough': 'Export',
};
const importedOnlyIds = new Set(['icw.a', 'icw.k', 'icw.q', 'icw.curl', 'common.coverage_mode', 'source.contours']);
function importedValueIsVisible(field: ParameterDefinition, design: DesignDocument): boolean {
  if (design.text_import_version) return true;
  const read = (document: DesignDocument) => field.path?.split('.').reduce<unknown>((value, key) =>
    value && typeof value === 'object' ? (value as Record<string, unknown>)[key] : undefined, document);
  return read(design) !== read(designForFamily(design.formula));
}
const parametricFields: ParameterDefinition[] = LEGACY_PARAMETER_REGISTRY.map((field) => {
  let section = sectionMoves[field.section] ?? field.section;
  if (['common.s1', 'common.s2', 'source.shape', 'source.radius', 'source.curvature'].includes(field.id)) section = 'Throat';
  if (field.id.startsWith('cross_section.')) section = 'Cross-section';
  if (field.id === 'freeform.inflectionPolicy' || ['enclosure.front_resolution', 'enclosure.back_resolution'].includes(field.id)) section = 'Mesh';
  if (['mesh.vertical_offset', 'native.mesh.offset'].includes(field.id)) section = 'Export';
  if (field.id === 'simulation.sim_type') section = 'Mounting';
  if (importedOnlyIds.has(field.id)) section = 'Imported ATH values';
  return { ...field, section,
    ...(field.id === 'simulation.sim_type' ? { label: 'Mounting', description: 'How the horn is mounted. Bare shell, Thickened waveguide and Waveguide in enclosure solve the whole body in open air with that body. Infinite baffle mounts the mouth flush in an unbounded rigid wall, ignoring the wall and enclosure and removing all cabinet-edge diffraction; availability follows the solver capabilities reported by this host.' } : {}),
    ...(field.section === 'Frequency Sweep' ? { description: field.description?.replaceAll('Solve options', 'Frequency sweep') } : {}),
    ...(importedOnlyIds.has(field.id) ? { visibleWhen: (design: DesignDocument) => importedValueIsVisible(field, design) } : {}),
  };
});

export const PARAMETER_SECTION_DEFINITIONS: readonly ParameterSectionDefinition[] = [
  { title: 'Profile', fieldOrder: [...parametricFields.filter((field) => field.section === 'Profile' && !field.group).map((field) => field.id), ...ICW_ADVANCED_FIELD_IDS], tab: 'geometry', description: 'Primary dimensions for the selected horn family. OSSE and R-OSSE labels carry their ATH formula symbol in parentheses.', visibleWhen: waveguideDefinitionOnly },
  { title: 'Throat', fieldOrder: ['common.s1', 'common.s2', 'common.throat_ext_angle', 'common.throat_ext_length', 'common.slot_length', 'common.length_mode', 'source.shape', 'source.radius', 'source.curvature'], tab: 'geometry', description: 'Throat stretch, extension, straight slot and source surface cap.', visibleWhen: waveguideDefinitionOnly },
  { title: 'Mouth', tab: 'geometry', description: CAD_PARAMETER_SECTION_DEFINITIONS.find((section) => section.title === 'Morph Target')!.description, visibleWhen: waveguideDefinitionOnly },
  { title: 'Cross-section', tab: 'geometry', description: 'Base cross-section shape and OSSE guiding-curve controls.', visibleWhen: waveguideDefinitionOnly },
  { title: 'Mounting', tab: 'geometry', description: 'Bare shell, thickened waveguide, enclosure or infinite baffle, with wall and enclosure dimensions.', visibleWhen: waveguideDefinitionOnly },
  { title: 'Experimental', tab: 'geometry', description: 'Source contour and horn + woofer assembly editors.', visibleWhen: parametricWorkspaceOnly },
  { title: 'Imported ATH values', tab: 'geometry', description: 'Imported geometry values and legacy fields preserved for file round-trip.', visibleWhen: parametricWorkspaceOnly },
  { title: 'Frequency sweep', tab: 'simulation', description: 'Frequency range, output points, spacing and adaptive sampling.', visibleWhen: parametricWorkspaceOnly },
  { title: 'Directivity map', tab: 'simulation', description: 'Polar planes and angular sampling used for directivity exports and plots.', visibleWhen: parametricWorkspaceOnly },
  { title: 'Source excitation', fieldOrder: ['source.velocity_convention', 'source.velocity'], tab: 'simulation', description: 'Source velocity convention and legacy amplitude.', visibleWhen: parametricWorkspaceOnly },
  { title: 'Solver', tab: 'simulation', description: 'Solve accuracy, backend engine, ground plane and diagnostic logging.', visibleWhen: parametricWorkspaceOnly },
  { title: 'Mesh', fieldOrder: ['control.solveDomain', 'mesh.angular_segments', 'mesh.length_segments', 'mesh.corner_segments', 'mesh.sampling_mode', 'mesh.z_map_points', 'mesh.throat_resolution', 'mesh.mouth_resolution', 'mesh.rear_resolution', 'enclosure.front_resolution', 'enclosure.back_resolution', 'mesh.aperture_resolution_scale', 'schema-gap.max_edge', 'mesh.max_triangles', 'mesh.allow_large_mesh', 'control.meshValidation', 'freeform.inflectionPolicy', 'native.source.size', 'native.mesh.throat', 'native.mesh.mouth', 'native.mesh.rear', 'native.mesh.budget', 'native.mesh.allow_large'], tab: 'simulation', description: CAD_PARAMETER_SECTION_DEFINITIONS.find((section) => section.title === 'Surface sampling')!.description, visibleWhen: parametricWorkspaceOnly },
  { title: 'Export', fieldOrder: ['mesh.vertical_offset', 'native.mesh.offset', 'mesh.quadrants', 'mesh.throat_segments', 'mesh.throat_slice_density', 'output.stl', 'output.msh', 'passthrough.abec', 'passthrough.report', 'passthrough.extra', 'passthrough.keys'], tab: 'simulation', description: 'Export placement and imported-file round-trip values; none change the preview or the solve.', visibleWhen: parametricWorkspaceOnly },
];

/** Fail closed when an ordered section gains an undeclared field. */
export function orderSectionFields(fields: readonly ParameterDefinition[], definition: ParameterSectionDefinition): ParameterDefinition[] {
  const order = definition.fieldOrder;
  if (!order) return [...fields];
  for (const field of fields) {
    if (!order.includes(field.id)) throw new Error(`${definition.title}: missing presentation order for ${field.id}`);
  }
  return [...fields].sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
}

// Only the parametric presentation is reordered. CAD and the catalog retain the
// legacy registry, including its original help text and field order.
export const PARAMETER_REGISTRY: ParameterDefinition[] = PARAMETER_SECTION_DEFINITIONS.flatMap((definition) =>
  orderSectionFields(parametricFields.filter((field) => field.section === definition.title), definition));

export function parameterSectionIsVisible(
  definition: ParameterSectionDefinition,
  context: ParameterSectionContext,
): boolean {
  return definition.visibleWhen?.(context) ?? true;
}

/** The 43 daggered v1 fields, the morph target exponent, and the two legacy four-value baffle tuples. */
export const EXPRESSION_PARAMETER_IDS = new Set([
  'rosse.R', 'rosse.a', 'rosse.a0', 'rosse.r0', 'rosse.k', 'rosse.m', 'rosse.b', 'rosse.r', 'rosse.q', 'rosse.tmax',
  'osse.L', 'osse.a', 'osse.a0', 'osse.r0', 'osse.k', 'osse.s', 'osse.n', 'osse.q', 'osse.h',
  'common.throat_ext_angle', 'common.throat_ext_length', 'common.slot_length',
  'morph.target_width', 'morph.target_height', 'morph.target_exponent', 'morph.corner_radius', 'morph.rate', 'morph.fixed_part',
  'osse.rotation', 'guide.distance', 'guide.width', 'guide.aspect_ratio', 'guide.superellipse_n', 'guide.superformula',
  'guide.sf_a', 'guide.sf_b', 'guide.sf_m1', 'guide.sf_m2', 'guide.sf_n1', 'guide.sf_n2', 'guide.sf_n3',
  'guide.rotation', 'osse.circ_arc_term_angle', 'osse.circ_arc_radius',
]);

export function fieldAcceptsExpression(field: ParameterDefinition): boolean {
  return EXPRESSION_PARAMETER_IDS.has(field.id);
}

export function fieldAppliesToFamily(field: ParameterDefinition, family: DesignFamily): boolean {
  return !field.families || field.families.includes(family);
}

export function findParameterByPath(
  path: string,
  family: DesignFamily,
  resolvePath: (candidate: string) => string = (candidate) => candidate,
): ParameterDefinition | undefined {
  const resolvedPath = resolvePath(path);
  return PARAMETER_REGISTRY.find((field) => fieldAppliesToFamily(field, family)
    && [field.path, ...(field.mirrorPaths ?? [])]
      .some((candidate) => candidate !== undefined && resolvePath(candidate) === resolvedPath));
}

export function fieldIsVisible(field: ParameterDefinition, design: DesignDocument): boolean {
  return fieldAppliesToFamily(field, design.formula) && (!field.visibleWhen || field.visibleWhen(design));
}

/**
 * The options to offer for `field` on `backend`, with the current value kept.
 *
 * Two things happen here. An option the backend cannot run disappears, unless
 * the design already holds it — a Mac-authored file or an ATH `SimType = 1`
 * import must stay legible and editable rather than silently losing the value
 * it is set to. And an option whose label promises a fast path this backend
 * lacks can be relabelled without silently discarding a legacy value.
 */
export function fieldOptionsForBackend(
  field: ParameterDefinition,
  value: unknown,
  backend: BackendIdentity,
  plan?: readonly EngineCapability[],
  hostEngines?: readonly EngineCapability[],
): ParameterOption[] {
  const options: ParameterOption[] = [];
  for (const option of field.options ?? []) {
    const feature = option.requiresFeature;
    const held = String(option.value) === String(value ?? '');
    if (option.currentOnly && !held) continue;
    if (feature && !held && !backendSupports(backend, feature, plan)) {
      // The chosen engine cannot run it. Keep the option visible but disabled
      // with the reason, so the user is not left wondering where it went: when
      // no engine on the host can run it, say so; when another can, name the
      // selected engine and the ones to switch to.
      const reason = hostLimitation(feature, hostEngines)
        ?? selectionLimitation(feature, backend, plan, hostEngines);
      if (reason) options.push({ ...option, unavailableReason: reason });
      continue;
    }
    options.push(option.degradedWithout && option.degradedLabel
      && !backendSupports(backend, option.degradedWithout, plan)
      ? { ...option, label: option.degradedLabel }
      : option);
  }
  return options;
}

/**
 * The feature the field's *current* value needs but this backend lacks.
 *
 * Drives the inline warning on a revealed-but-unsupported value; undefined
 * whenever the selection is something the backend can actually solve.
 */
export function fieldUnsupportedFeature(
  field: ParameterDefinition,
  value: unknown,
  backend: BackendIdentity,
  plan?: readonly EngineCapability[],
): BackendFeature | undefined {
  const selected = (field.options ?? []).find((option) => String(option.value) === String(value ?? ''));
  const feature = selected?.requiresFeature;
  return feature && !backendSupports(backend, feature, plan) ? feature : undefined;
}

export function fieldMatchesQuery(field: ParameterDefinition, query: string): boolean {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return true;
  return [field.label, field.symbol, field.legacyKey, field.path, field.id, field.description]
    .some((value) => value?.toLocaleLowerCase().includes(normalized));
}

export function traceEntryIsRegistered(entry: { key: string; family?: DesignFamily }): boolean {
  return PARAMETER_REGISTRY.some((field) => field.legacyKey === entry.key && (!entry.family || fieldAppliesToFamily(field, entry.family)));
}


/** Same coefficient refusal reasons as hornlab_mesher.throat_stretch. */
export function throatStretchError(key: 's1' | 's2', value: number): string | undefined {
  if (Number.isFinite(value) && value < 0) return `throat stretch ${key} must not be negative, got ${value}: negative values are not supported (ATH accepts them, but they fold the profile back through the throat for all but very small magnitudes)`;
  if (!Number.isFinite(value) || value > 10) return `throat stretch ${key} must be finite and >= 0 and <= 10, got ${value}`;
  return undefined;
}
