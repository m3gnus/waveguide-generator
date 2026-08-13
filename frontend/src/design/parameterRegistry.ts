import type { DesignDocument, DesignFamily } from '../stores/design';
import type { WorkspaceMode } from '../stores/workspaceMode';

export type ParameterSection =
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

export interface ParameterSectionDefinition {
  title: ParameterSection;
  tab: ParameterTab;
  description: string;
  /** Section policy is deliberately data-aware rather than a mode list. CAD
   * is the first consumer; later sections can use the same gate when their
   * presence depends on actual design content. */
  visibleWhen?: (ctx: { mode: WorkspaceMode; design: DesignDocument }) => boolean;
}

export type ParameterKind = 'number' | 'select' | 'text' | 'toggle' | 'table' | 'indicator';

export interface ParameterOption {
  value: string | number;
  label: string;
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
  visibleWhen?: (design: DesignDocument) => boolean;
  disabledWhen?: (design: DesignDocument) => string | undefined;
}

const allFamilies: DesignFamily[] = ['R-OSSE', 'OSSE', 'ICW', 'FREEFORM'];
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

export const PARAMETER_REGISTRY: ParameterDefinition[] = [
  // Complete family profile scalars.
  number('common.scale', 'scale', 'scale', profile, 'Scale', { families: allFamilies, min: .1, max: 2, step: .001, precision: 3, description: 'Shrinks or enlarges the waveguide below and above 1, taking the source radius, wall thickness and horn mesh sizes with it. Enclosure dimensions are not scaled.' }),
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
  number('osse.h', 'h', 'h', profile, 'Shape factor', { symbol: 'h', families: ['OSSE'], min: 0, max: 10, description: 'Adds a half-sine bulge in millimetres to the radius, peaking halfway and vanishing at both ends. It spans the whole driver-to-mouth path, including any extension and slot, and applies to the circular arc too. Scale does not multiply it.' }),

  // ICW is axisymmetric by construction: the mesher evaluates every ICW param
  // at phi=0, so none of these take a per-azimuth expression. Say so rather
  // than repeating v1's "can be an expression" wording, which was copied over
  // from the R-OSSE fields and does not hold here.
  number('icw.r0', 'r0', 'r0', profile, 'Throat radius', { families: ['ICW'], unit: 'mm', min: .1, max: 200, description: 'Radius at the throat, where the driver attaches. The curvature solver starts the wall here.' }),
  number('icw.a0', 'a0', 'a0', profile, 'Throat half-angle', { families: ['ICW'], unit: '°', min: -90, max: 90, description: "Wall angle where the curve leaves the throat, measured from the horn axis. This is the curve's starting angle, not a target." }),
  number('icw.L', 'L', 'L', profile, 'Horn length', { families: ['ICW'], unit: 'mm', min: 1, max: 1_000, visibleWhen: (design) => design.termination !== 'rollback', description: 'Target axial length. The curvature solver sizes the curve to reach it. Rollback terminations use Rollback depth instead.' }),
  number('icw.R', 'R', 'R', profile, 'Mouth radius', { families: ['ICW'], unit: 'mm', min: .1, max: 1_000, disabledWhen: (design) => (design.coverage_angle ?? 0) > 0 ? 'Emergent while coverage hold is enabled.' : undefined, description: 'Target mouth radius the curvature solver sizes the curve to reach. On a rollback it is the aperture radius where the wall first turns through 90°, not the radius at the curled tip. Ignored while a coverage hold is enabled, which makes the mouth radius an output.' }),
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
  number('icw.a', 'a', 'a', profile, 'Legacy coverage coefficient', { families: ['ICW'], unit: '°', min: -180, max: 180, description: 'Kept so an imported file round-trips unchanged. The ICW solver does not read it — the curve comes from the throat, size and coverage targets above.' }),
  number('icw.k', 'k', 'k', profile, 'Legacy flare coefficient', { families: ['ICW'], min: -100, max: 100, description: 'Kept so an imported file round-trips unchanged. The ICW solver does not read it — the curve comes from the throat, size and coverage targets above.' }),
  number('icw.q', 'q', 'q', profile, 'Legacy smoothness coefficient', { families: ['ICW'], min: -100, max: 100, description: 'Kept so an imported file round-trips unchanged. The ICW solver does not read it — the curve comes from the throat, size and coverage targets above.' }),
  number('icw.curl', 'curl', 'curl', profile, 'Curl', { families: ['ICW'], min: -100, max: 100, description: 'Kept so an imported file round-trips unchanged. The ICW solver does not read it — use Termination and Rollback curl angle to curl the mouth.' }),

  number('freeform.length', 'length', 'length', profile, 'Length', { families: ['FREEFORM'], unit: 'mm', min: 20, max: 1_000, step: 1, description: 'Axial length shared by the horizontal and vertical profiles, spanned by both splines from throat to mouth. Interior anchors are stored as fractions of it, so changing it moves them all proportionally.' }),
  number('freeform.throatRadius', 'throatRadius', 'profile_h.points.0.r', profile, 'Throat radius', { families: ['FREEFORM'], mirrorPaths: ['profile_v.points.0.r'], unit: 'mm', min: .1, max: 200, description: 'Radius at the throat. Shared by both profile planes, so the throat stays circular.' }),
  number('freeform.throatAngle', 'throatAngle', 'profile_h.throat_angle_deg', profile, 'Throat angle', { families: ['FREEFORM'], mirrorPaths: ['profile_v.throat_angle_deg'], unit: '°', min: -90, max: 90, description: 'Wall angle leaving the driver exit, shared by both profile planes, fixing the spline tangent at the throat. An angle set on the first point of a plane overrides it for that plane.' }),
  number('freeform.mouthRadiusH', 'mouthRadiusH', 'profile_h.points.$last.r', profile, 'Horizontal mouth radius', { families: ['FREEFORM'], unit: 'mm', min: .1, max: 1_000, step: 1, description: 'Half-width of the mouth in the horizontal plane.' }),
  number('freeform.mouthAngleH', 'mouthAngleH', 'profile_h.mouth_angle_deg', profile, 'Horizontal mouth angle', { families: ['FREEFORM'], unit: '°', min: -90, max: 90, description: 'Wall angle the horizontal spline arrives at the mouth with, fixing the spline tangent there. An angle set on the last horizontal point overrides it.' }),
  { id: 'freeform.interiorH', legacyKey: 'interiorH', path: 'profile_h.points', section: profile, label: 'Horizontal spline points', kind: 'table', families: ['FREEFORM'], description: 'Optional anchors the horizontal spline must pass through, placed strictly between throat and mouth. Give each a position and radius; set its angle to pin the wall direction there, or leave it blank for an automatic tangent.' },
  number('freeform.mouthRadiusV', 'mouthRadiusV', 'profile_v.points.$last.r', profile, 'Vertical mouth radius', { families: ['FREEFORM'], unit: 'mm', min: .1, max: 1_000, step: 1, description: 'Half-height of the mouth in the vertical plane.' }),
  number('freeform.mouthAngleV', 'mouthAngleV', 'profile_v.mouth_angle_deg', profile, 'Vertical mouth angle', { families: ['FREEFORM'], unit: '°', min: -90, max: 90, description: 'Wall angle the vertical spline arrives at the mouth with, fixing the spline tangent there. An angle set on the last vertical point overrides it.' }),
  { id: 'freeform.interiorV', legacyKey: 'interiorV', path: 'profile_v.points', section: profile, label: 'Vertical spline points', kind: 'table', families: ['FREEFORM'], description: 'Optional anchors the vertical spline must pass through, placed strictly between throat and mouth. Give each a position and radius; set its angle to pin the wall direction there, or leave it blank for an automatic tangent.' },
  { id: 'freeform.crossSections', legacyKey: 'crossSections', path: 'cross_sections', section: profile, label: 'Cross-section stations', kind: 'table', families: ['FREEFORM'], description: 'Cross-section outline at increasing positions along the throat-to-mouth axis, blended between the stations you list; the H and V splines still set each section\'s half-width and half-height. The station at 0 must be an ellipse. Superellipse exponents run 2 to 16, and a corner radius has to fit the local half-size.' },
  select('freeform.inflectionPolicy', 'inflectionPolicy', 'inflection_policy', profile, 'Curve direction', [{ value: 'warn', label: 'Warn on S-curves' }, { value: 'reject', label: 'Enforce one-way' }], { families: ['FREEFORM'], description: "An S-curve is a stretch where the wall's outward angle falls back by more than a degree before opening again. Warn records it and builds anyway; Enforce one-way rejects the profile instead." }),

  // Throat extension, morph, coverage/length modes, and complete OSSE guide controls.
  number('common.throat_ext_angle', 'throatExtAngle', 'throat_ext_angle', throatExtension, 'Throat extension angle', { families: osseFamilies, unit: '°', min: -90, max: 90, description: 'Taper of the separate conical adapter behind the main horn. The driver end works out as r0 minus the extension length times tan of this angle, so positive angles narrow it toward the driver and negative ones widen it; 0 makes a straight tube. This angle is independent of a0: equal values make a tangent OS-SE join, while R-OSSE\'s finished tangent also depends on m, r and q.' }),
  number('common.throat_ext_length', 'throatExtLength', 'throat_ext_length', throatExtension, 'Throat extension length', { families: osseFamilies, unit: 'mm', min: 0, max: 1_000, description: 'Length of the conical adapter added behind the throat, between the driver and the horn. 0 leaves it out. It does not change the computed OS-SE or R-OSSE flare; it increases the physical total length by this amount.' }),
  number('common.slot_length', 'slotLength', 'slot_length', throatExtension, 'Straight slot length', { families: osseFamilies, unit: 'mm', min: 0, max: 1_000, description: 'Length of a straight r0 section between the throat extension and the start of the flare; 0 leaves it out. Under OSSE Total length mode it is subtracted from Horn length rather than added. A non-zero Shape factor h bulges it too, so it is not perfectly straight.' }),
  select('common.length_mode', 'lengthMode', 'length_mode', throatExtension, 'Length mode', [{ value: 'profile', label: 'Profile length' }, { value: 'total', label: 'Total length' }], { families: osseFamilies, description: 'What Horn length measures. Profile length leaves all of it for the flare, so the slot makes the part longer; Total length subtracts the slot from it instead, shortening the flare. The throat extension is added on top either way. R-OSSE derives its own length and ignores this.' }),
  { id: 'common.coverage_mode', legacyKey: 'coverageMode', path: 'coverage_mode', section: profile, label: 'Coverage mode', kind: 'text', families: ['ICW'], description: 'Carried through from the imported file so it round-trips unchanged. Nothing in the solve reads it; the coverage hold is driven by Coverage angle above.' },
  select('morph.target_shape', 'morphTarget', 'morph.target_shape', morphTarget, 'Morph target', [{ value: 0, label: 'None' }, { value: 1, label: 'Rectangle' }, { value: 2, label: 'Circle' }], { families: ['R-OSSE', 'OSSE', 'ICW'], description: 'Outline the mouth is blended toward as it approaches the baffle. None keeps the outline the profile formula produces.' }),
  number('morph.target_width', 'morphWidth', 'morph.target_width', morphTarget, 'Target width', { families: ['R-OSSE', 'OSSE', 'ICW'], unit: 'mm', min: 0, max: 2_000, description: 'Full target width. 0 derives it from the mouth, rounding the half-width up to a whole millimetre before doubling. A Circle target uses the larger of width and height as its diameter.' }),
  number('morph.target_height', 'morphHeight', 'morph.target_height', morphTarget, 'Target height', { families: ['R-OSSE', 'OSSE', 'ICW'], unit: 'mm', min: 0, max: 2_000, description: 'Full target height. 0 derives it from the mouth, rounding the half-height up to a whole millimetre before doubling. A Circle target uses the larger of width and height as its diameter.' }),
  number('morph.corner_radius', 'morphCorner', 'morph.corner_radius', morphTarget, 'Corner radius', { families: ['R-OSSE', 'OSSE', 'ICW'], unit: 'mm', min: 0, max: 100, step: 1, description: 'Rounds the corners of a Rectangle target, clamped to the smaller half-dimension of that target. Has no effect on a Circle target.' }),
  number('morph.rate', 'morphRate', 'morph.rate', morphTarget, 'Morph rate', { families: ['R-OSSE', 'OSSE', 'ICW'], min: 0, max: 100, description: 'Exponent on the blend once morphing starts. 1 is a straight ramp; higher values hold the formula shape longer and swing to the target near the mouth; 0 jumps straight to the target.' }),
  number('morph.fixed_part', 'morphFixed', 'morph.fixed_part', morphTarget, 'Fixed part', { families: ['R-OSSE', 'OSSE', 'ICW'], min: 0, max: 1, step: .01, description: 'Fraction of the profile from the throat left untouched before blending starts; 0 blends from the throat. 1 disables morphing altogether, as does any value at or above Truncation limit on R-OSSE. Throat extensions and slots are never morphed.' }),
  select('morph.allow_shrinkage', 'morphAllowShrinkage', 'morph.allow_shrinkage', morphTarget, 'Allow shrinkage', yesNo, { families: ['R-OSSE', 'OSSE', 'ICW'], description: 'Whether the target may be smaller than the mouth the formula produced. No raises the target width and height to at least the mouth bounds, so a smaller target is enlarged rather than honoured.' }),
  select('osse.throat_profile', 'throatProfile', 'throat_profile', guidingCurve, 'Throat profile', [{ value: 1, label: 'OS-SE' }, { value: 3, label: 'Circular arc' }], { families: ['OSSE'], description: 'Which curve carries the wall from throat to mouth: the OS-SE formula above, or a plain circular arc driven by the two arc controls at the bottom of this section. The arc ignores a0, k, s, n and q; Shape factor h and Profile rotation still apply.' }),
  number('osse.rotation', 'rot', 'rotation', guidingCurve, 'Profile rotation', { families: ['OSSE'], unit: '°', min: -360, max: 360, description: 'Rotates each meridian about the point [0, r0] before it is revolved, which reshapes the surface rather than tilting the finished horn rigidly. A p-dependent value turns each meridian differently and warps the mouth.' }),
  select('guide.curve_type', 'gcurveType', 'guiding_curve.curve_type', guidingCurve, 'Guiding curve mode', [{ value: 0, label: 'Explicit coverage' }, { value: 1, label: 'Superellipse' }, { value: 2, label: 'Superformula' }], { families: ['OSSE'], description: 'Where the mouth coverage angle comes from. Explicit uses the angle you typed; the other two solve, per azimuth, the coverage angle that puts the OS-SE radius on the guide shape at the guide distance, overriding that field. The solved angle is clamped to 0.5°–89°.' }),
  number('guide.distance', 'gcurveDist', 'guiding_curve.distance', guidingCurve, 'Guiding curve distance', { families: ['OSSE'], min: 0, max: 10_000, description: 'Which station along the flare the guide shape is matched at. Above 0 through 1 is a fraction of the main length; above 1 is millimetres, clamped to the mouth. 0 means the mouth.' }),
  number('guide.width', 'gcurveWidth', 'guiding_curve.width', guidingCurve, 'Guiding curve width', { families: ['OSSE'], unit: 'mm', min: 0, max: 2_000, description: 'Horizontal size of the guide shape; 0 disables guiding and falls back to the explicit coverage angle. It is the exact full width for a Superellipse, but only a base scale for a Superformula, whose own terms change the real span.' }),
  number('guide.aspect_ratio', 'gcurveAspectRatio', 'guiding_curve.aspect_ratio', guidingCurve, 'Guiding curve aspect ratio', { families: ['OSSE'], min: .01, max: 100, description: 'Vertical scale of the guide shape divided by its horizontal scale. 1 makes the two equal — a circle at superellipse exponent 2, not a square — and below 1 makes it wider than tall.' }),
  number('guide.superellipse_n', 'gcurveSeN', 'guiding_curve.superellipse_n', guidingCurve, 'Guiding superellipse exponent', { families: ['OSSE'], min: .01, max: 100, visibleWhen: (design) => design.guiding_curve.curve_type === 1, description: 'Exponent of the superellipse guide shape. 2 gives an ellipse and higher values square it off toward a rounded rectangle. Anything below 2 is clamped up to 2.' }),
  number('guide.superformula', 'gcurveSf', 'guiding_curve.superformula', guidingCurve, 'Superformula tuple', { families: ['OSSE'], min: -1_000, max: 1_000, visibleWhen: (design) => design.guiding_curve.curve_type === 2, description: 'All six superformula terms as one comma-separated list, in the order a, b, m, n1, n2, n3. A complete six-value list overrides every individual field below and uses its single m for both m1 and m2; a shorter list is ignored.' }),
  ...(['sf_a', 'sf_b', 'sf_m1', 'sf_m2', 'sf_n1', 'sf_n2', 'sf_n3'] as const).map((key) => number(`guide.${key}`, `gcurveSf${key.slice(3).toUpperCase()}`, `guiding_curve.${key}`, guidingCurve, `Superformula ${key.slice(3)}`, { families: ['OSSE'], min: -1_000, max: 1_000, visibleWhen: (design) => design.guiding_curve.curve_type === 2, description: SUPERFORMULA_HELP[key] })),
  number('guide.rotation', 'gcurveRot', 'guiding_curve.rotation', guidingCurve, 'Guiding curve rotation', { families: ['OSSE'], unit: '°', min: -360, max: 360, description: 'Rotates the guide shape counterclockwise about the horn axis, turning the coverage pattern with it. A p-dependent expression is evaluated per azimuth and distorts the outline instead of turning it.' }),
  number('osse.circ_arc_term_angle', 'circArcTermAngle', 'circ_arc_term_angle', guidingCurve, 'Circular arc terminal angle', { families: ['OSSE'], unit: '°', min: -180, max: 180, visibleWhen: (design) => design.throat_profile === 3, description: 'Wall angle the arc reaches at the mouth. Used only when Throat profile is Circular arc and Radius override is not in play. An angle parallel to the throat-to-mouth chord has no solution and falls back to a constant mouth radius.' }),
  number('osse.circ_arc_radius', 'circArcRadius', 'circ_arc_radius', guidingCurve, 'Circular arc radius override', { families: ['OSSE'], unit: 'mm', min: 0, max: 10_000, visibleWhen: (design) => design.throat_profile === 3, description: 'Forces the arc radius and takes precedence over Terminal angle. It is honoured only when positive and at least half the throat-to-mouth chord; 0 or an impossible radius silently falls back to Terminal angle. Circular arc only.' }),

  // Source including schema-carried contours and explicit velocity convention.
  select('source.shape', 'sourceShape', 'source.shape', sourceDefinition, 'Source surface', [{ value: 1, label: 'Spherical cap' }, { value: 2, label: 'Flat disc' }], { description: 'Shape of the driving surface placed at the throat. A spherical cap approximates a dome radiating into the horn; a flat disc models a flat piston.' }),
  number('source.radius', 'sourceRadius', 'source.radius', sourceDefinition, 'Source radius', { unit: 'mm', min: -1, max: 2_000, description: 'Sphere radius of a spherical-cap source; a flat disc ignores it. A value of 0 or below derives the radius from the throat radius and throat angle instead of setting it directly.' }),
  select('source.curvature', 'sourceCurv', 'source.curvature', sourceDefinition, 'Source curvature', [{ value: 0, label: 'Auto' }, { value: 1, label: 'Convex' }, { value: -1, label: 'Concave' }], { description: 'Which way a spherical-cap source bulges, seen from the horn. Auto picks the usual direction for the chosen surface.' }),
  number('source.velocity', 'sourceAmplitude', 'source.velocity', sourceDefinition, 'Source amplitude', { unit: 'm/s', min: 0, max: 100, step: .01, precision: 3, description: 'Despite the name this never scales the result: the solve always drives the source at unit velocity. It is read only under the Legacy convention, where it carries ATH\'s direction code and must be exactly 1 (normal) or 2 (axial).' }),
  select('source.velocity_convention', 'sourceVelocity', 'source.velocity_convention', sourceDefinition, 'Velocity convention', [{ value: 'normal', label: 'Normal velocity' }, { value: 'axial', label: 'Axial rigid-piston velocity' }, { value: 'legacy', label: 'Legacy config value' }], { description: 'Which way the source surface moves. Normal drives every point along its own surface normal; Axial drives it as a rigid piston along the horn axis. Legacy takes the same choice from the numeric code in Source amplitude instead.' }),
  { id: 'source.contours', legacyKey: 'sourceContours', path: 'source.contours', section: sourceDefinition, label: 'Source contours', kind: 'text', description: 'Preserved for ATH round-trip only — custom source contours are not implemented here. Any non-empty value stops both the viewport preview and the solve from meshing.' },

  // Full enclosure schema block.
  number('enclosure.depth', 'encDepth', 'enclosure.depth', wallEnclosure, 'Enclosure depth', { unit: 'mm', min: 0, max: 2_000, step: 1, visibleWhen: (design) => design.enclosure.depth > 0, description: 'Depth of the box behind the front baffle. A positive value is raised if needed to clear the horn — at least its axial span plus 1 mm. 0 means no box, leaving a free-standing horn shaped by Wall thickness. Infinite-baffle simulations ignore the enclosure entirely.' }),
  number('enclosure.edge_radius', 'encEdge', 'enclosure.edge_radius', wallEnclosure, 'Edge radius', { unit: 'mm', min: 0, max: 1_000, visibleWhen: (design) => design.enclosure.depth > 0, description: 'Size of the roundover or bevel on the outer box edges, front and rear. The front edge is the main control over baffle diffraction. It is reduced automatically to fit the box and to leave the baffle margins intact.' }),
  select('enclosure.edge_type', 'encEdgeType', 'enclosure.edge_type', wallEnclosure, 'Edge finish', [{ value: 1, label: 'Rounded' }, { value: 2, label: 'Chamfered' }], { visibleWhen: (design) => design.enclosure.depth > 0, description: 'Whether the front edges are cut as a circular roundover or a flat bevel. Both use Edge radius for their size.' }),
  ...([['space_l', 'encSpaceL', 'Left margin', 'left'], ['space_t', 'encSpaceT', 'Top margin', 'top'], ['space_r', 'encSpaceR', 'Right margin', 'right'], ['space_b', 'encSpaceB', 'Bottom margin', 'bottom']] as const).map(([path, key, label, side]) => number(`enclosure.${path}`, key, `enclosure.${path}`, wallEnclosure, label, { unit: 'mm', min: 0, max: 2_000, visibleWhen: (design) => design.enclosure.depth > 0, description: `Flat baffle left between the horn mouth and the ${side} edge of the box, which is what sets the box size on that side. A positive margin is never eaten by Edge radius — the roundover shrinks instead.` })),
  number('enclosure.front_resolution', 'encFrontResolution', 'enclosure.front_resolution', solveExportMesh, 'Front baffle mesh resolution', { unit: 'mm', min: .01, max: 1_000, description: 'Target element size on the enclosure front baffle. A four-value ATH tuple round-trips unchanged, but only its first value is meshed — every quadrant gets that size.' }),
  number('enclosure.back_resolution', 'encBackResolution', 'enclosure.back_resolution', solveExportMesh, 'Rear baffle mesh resolution', { unit: 'mm', min: .01, max: 1_000, description: 'Target element size on the enclosure rear panel. A four-value ATH tuple round-trips unchanged, but only its first value is meshed — every quadrant gets that size.' }),

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
  number('mesh.aperture_resolution_scale', 'apertureResolutionScale', 'mesh.aperture_resolution_scale', solveExportMesh, 'Aperture mesh scale', { min: .01, max: 100, description: 'Multiplies the element size inside the infinite-baffle aperture cap relative to Mouth mesh resolution. Above 1 coarsens the cap to save solve time; anything below 1 is clamped back to 1, so it cannot refine it.' }),
  number('mesh.max_triangles', 'maxTriangles', 'mesh.max_triangles', solveExportMesh, 'Triangle warning threshold', { min: 1, max: 10_000_000, step: 1_000, precision: 0, description: 'Advisory warning threshold, in full-domain-equivalent triangles. Solves continue past it, but two hard ceilings you cannot raise from here will still stop one: 22,000 triangles in the actual solved domain, and the dense-solver memory limit.' }),
  // Kept in the registry solely for text/wire traceability. The old approval
  // control is intentionally absent from the panel because the threshold is
  // advisory and the independent process-safety ceiling cannot be bypassed.
  select('mesh.allow_large_mesh', 'allowLargeMesh', 'mesh.allow_large_mesh', solveExportMesh, 'Legacy large-mesh approval', [{ value: 0, label: 'Ignored' }, { value: 1, label: 'Ignored' }], { visibleWhen: () => false, description: 'Compatibility-only imported value; it does not change solve limits.' }),
  number('mesh.vertical_offset', 'verticalOffset', 'mesh.vertical_offset', solveExportMesh, 'Export vertical offset', { unit: 'mm', min: -10_000, max: 10_000, description: 'Shifts the design vertically, for placing a horn off-centre on its baffle. It applies to the viewport, the solver mesh and the solid STEP export; STL, surface STEP and profile CSV are recentred without it. It is forced to 0 on the quarter and half-Y domains, where an offset would move the cut off its symmetry plane.' }),
  number('mesh.wall_thickness', 'wallThickness', 'mesh.wall_thickness', wallEnclosure, 'Wall thickness', { unit: 'mm', min: 0, max: 1_000, description: "Thickness of the shell built around a free-standing horn, offset from its surface and closed by a disc behind the throat. Used only when Enclosure depth is 0 and the simulation is free-standing; infinite baffle ignores it. Unset means ATH's 5 mm default free-standing, and 0 in infinite baffle.", visibleWhen: (design) => design.enclosure.depth <= 0 && design.mesh.wall_thickness > 0 }),

  number('simulation.f1', 'freqStart', 'simulation.f1', frequencySweep, 'Sweep start', { unit: 'Hz', min: 20, max: 20_000, step: 10, precision: 0, description: 'Lowest frequency of the generated sweep. Ignored when Solve options is set to an explicit frequency list.' }),
  number('simulation.f2', 'freqEnd', 'simulation.f2', frequencySweep, 'Sweep end', { unit: 'Hz', min: 20, max: 20_000, step: 10, precision: 0, description: 'Highest frequency of the generated sweep; raising it may need a finer Mouth mesh resolution to resolve the shorter wavelength. Ignored when Solve options is set to an explicit frequency list.' }),
  number('simulation.num_frequencies', 'numFreqs', 'simulation.num_frequencies', frequencySweep, 'Frequency samples', { min: 10, max: 200, step: 1, precision: 0, description: 'How many frequencies are solved between the start and end. Every frequency costs about the same, so this sets solve time almost directly. Spacing is chosen under Solve options, which also overrides this entirely when an explicit list is used.' }),
  select('simulation.sim_type', 'simType', 'simulation.sim_type', solveExportMesh, 'Simulation type', [{ value: 'freestanding', label: 'Free-standing' }, { value: 'infinite-baffle', label: 'Infinite baffle' }], { description: 'What the horn radiates into. Free-standing solves the whole body in open air, with the enclosure or wall shell you configured. Infinite baffle mounts the mouth flush in an unbounded rigid wall, ignoring both of those and removing all cabinet-edge diffraction; it requires the Metal backend.' }),
  // CircSym is not a backend, it is the axisymmetric meridian fast path: the
  // solver takes it automatically for a body of revolution. The stored values
  // stay as ATH spells them so designs round-trip; only the labels changed.
  select('simulation.solver_mode', 'solverMode', 'simulation.solver_mode', solveExportMesh, 'Solver mode', [{ value: 'auto', label: 'Auto — meridian when eligible' }, { value: 'full_3d', label: 'Force full 3D' }, { value: 'circsym', label: 'Force axisymmetric meridian' }], { description: 'Controls the Metal backend\'s solve path. Auto solves a body of revolution on one rotated meridian slice — far faster — and everything else in full 3D. Eligibility depends on the observation settings as well as the geometry. Forcing the meridian path fails clearly when ineligible, and BEMPP is always full 3D.' }),

  // These saved fields remain in the document for ATH text fidelity, but none
  // is a solve-domain control. Runtime symmetry replaces Mesh.Quadrants before
  // a parametric mesh is built, while geometry export always restores all four
  // quadrants so the downloaded part is whole. The other two are similarly
  // inert here: the mesher rejects Mesh.ThroatSegments outright and never reads
  // a throat slice density.
  { id: 'mesh.quadrants', legacyKey: 'quadrants', path: 'mesh.quadrants', section: 'Output & Passthrough', label: 'Stored ATH Mesh.Quadrants', kind: 'indicator', description: 'Preserved for ATH round-trip only. WG overwrites this stored value from Solve domain before a parametric solve and ignores it for geometry export, which always restores the full part.' },
  number('mesh.throat_segments', 'throatSegments', 'mesh.throat_segments', 'Output & Passthrough', 'ATH throat slice samples', { min: 0, max: 4_096, step: 1, precision: 0, description: 'Preserved for ATH round-trip only. Use Z-map sampling to bias axial stations toward the throat.' }),
  number('mesh.throat_slice_density', 'throatSliceDensity', 'mesh.throat_slice_density', 'Output & Passthrough', 'ATH throat slice density', { min: .01, max: .99, step: .01, description: 'Preserved for ATH round-trip only; the HornLab mesher does not read it.' }),

  select('output.stl', 'outputSTL', 'output.stl', 'Output & Passthrough', 'STL output flag', yesNo, { description: "ATH's own Output.STL flag, kept so an imported file round-trips unchanged. It does not drive this app's exports — use the export menu for that." }),
  select('output.msh', 'outputMSH', 'output.msh', 'Output & Passthrough', 'MSH output flag', yesNo, { description: "ATH's own Output.MSH flag, kept so an imported file round-trips unchanged. It does not drive this app's exports — use the export menu for that." }),
  { id: 'passthrough.abec', legacyKey: 'ABEC', section: 'Output & Passthrough', label: 'ABEC passthrough blocks', kind: 'indicator', description: 'Whether the imported file carried ABEC project blocks. This app does not read or edit them, but writes them back out unchanged when you export the config.' },
  { id: 'passthrough.report', legacyKey: 'Report', section: 'Output & Passthrough', label: 'Report passthrough block', kind: 'indicator', description: "Whether the imported file carried ATH's Report block. It is written back out unchanged on export; this app's own plots are configured in the results dock instead." },
  { id: 'passthrough.extra', legacyKey: 'extraBlocks', section: 'Output & Passthrough', label: 'Other passthrough blocks', kind: 'indicator', description: 'Any further named blocks in the imported file that this app does not model. They are preserved verbatim and written back out on export.' },
  { id: 'passthrough.keys', legacyKey: 'extraKeys', section: 'Output & Passthrough', label: 'Unknown flat keys', kind: 'indicator', description: 'Top-level settings in the imported file that this app does not model. They are preserved verbatim and written back out on export, so nothing is lost in a round-trip.' },
];

const familyTraceKeys: Record<DesignFamily, readonly string[]> = {
  'R-OSSE': ['scale', 'R', 'a', 'a0', 'r0', 'k', 'm', 'b', 'r', 'q', 'tmax'],
  OSSE: ['scale', 'L', 'a', 'a0', 'r0', 'k', 's', 'n', 'q', 'h'],
  ICW: ['scale', 'r0', 'a0', 'L', 'R', 'coverage_angle', 'hold_start', 'hold_end', 'n_coeff', 'termination', 'theta1_deg', 'depth'],
  FREEFORM: ['scale', 'length', 'throatRadius', 'throatAngle', 'mouthRadiusH', 'mouthAngleH', 'interiorH', 'mouthRadiusV', 'mouthAngleV', 'interiorV', 'crossSections', 'inflectionPolicy'],
};

const commonTraceKeys = [
  'throatExtAngle', 'throatExtLength', 'slotLength',
  'morphTarget', 'morphWidth', 'morphHeight', 'morphCorner', 'morphRate', 'morphFixed', 'morphAllowShrinkage',
  'wallThickness', 'encDepth', 'encEdge', 'encEdgeType', 'encSpaceL', 'encSpaceT', 'encSpaceR', 'encSpaceB',
  'throatProfile', 'rot', 'gcurveType', 'gcurveDist', 'gcurveWidth', 'gcurveAspectRatio', 'gcurveSeN', 'gcurveSf',
  'gcurveSfA', 'gcurveSfB', 'gcurveSfM1', 'gcurveSfM2', 'gcurveSfN1', 'gcurveSfN2', 'gcurveSfN3', 'gcurveRot', 'circArcTermAngle', 'circArcRadius',
  'angularSegments', 'lengthSegments', 'cornerSegments', 'throatSegments', 'throatSliceDensity',
  'freqStart', 'freqEnd', 'numFreqs', 'sourceShape', 'sourceRadius', 'sourceCurv', 'sourceVelocity',
  'simType', 'solverMode', 'throatResolution', 'mouthResolution', 'rearResolution', 'apertureResolutionScale',
  'maxTriangles', 'allowLargeMesh', 'verticalOffset', 'quadrants', 'encFrontResolution', 'encBackResolution',
] as const;

/** Current family-qualified parameter inventory, with family duplicates qualified. */
export const TRACEABILITY_PARAMETER_INVENTORY = [
  ...Object.entries(familyTraceKeys).flatMap(([family, keys]) => keys.map((key) => ({ key, family: family as DesignFamily }))),
  ...commonTraceKeys.map((key) => ({ key, family: undefined })),
];

export const PARAMETER_SECTION_DEFINITIONS: readonly ParameterSectionDefinition[] = [
  {
    title: 'Profile Dimensions',
    tab: 'geometry',
    description: 'Primary dimensions for the selected horn family. OSSE and R-OSSE labels carry their ATH formula symbol in parentheses.',
  },
  {
    title: 'Throat Extension',
    tab: 'geometry',
    description: 'Optional conical throat extension and initial straight slot controls for OSSE-family profiles.',
  },
  {
    title: 'Morph Target',
    tab: 'geometry',
    description: 'Post-profile shaping that transitions the mouth toward another target shape. Target extents smaller than the mouth are enlarged back to it unless Allow shrinkage is on, so a smaller width or height changes nothing on its own. Leave an extent at 0 to derive it from the mouth.',
  },
  {
    title: 'Wall & Enclosure',
    tab: 'geometry',
    description: 'Freestanding wall-shell controls and enclosure clearances that change the exported or simulated solid.',
  },
  {
    title: 'Guiding Curve',
    tab: 'geometry',
    description: 'OSSE-only throat profile, rotation, and guide-shape controls used to bend or infer the horn profile.',
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

export function parameterSectionIsVisible(
  definition: ParameterSectionDefinition,
  mode: WorkspaceMode,
  design: DesignDocument,
): boolean {
  return definition.visibleWhen?.({ mode, design }) ?? true;
}

export const PARAMETER_SECTIONS: ParameterSection[] = PARAMETER_SECTION_DEFINITIONS.map(({ title }) => title);

export function tabForParameterSection(section: ParameterSection): ParameterTab {
  const definition = PARAMETER_SECTION_DEFINITIONS.find((item) => item.title === section);
  if (!definition) throw new Error(`Unknown parameter section: ${section}`);
  return definition.tab;
}

/** The 43 daggered v1 fields, plus the two legacy four-value baffle tuples. */
export const EXPRESSION_PARAMETER_IDS = new Set([
  'rosse.R', 'rosse.a', 'rosse.a0', 'rosse.r0', 'rosse.k', 'rosse.m', 'rosse.b', 'rosse.r', 'rosse.q', 'rosse.tmax',
  'osse.L', 'osse.a', 'osse.a0', 'osse.r0', 'osse.k', 'osse.s', 'osse.n', 'osse.q', 'osse.h',
  'common.throat_ext_angle', 'common.throat_ext_length', 'common.slot_length',
  'morph.target_width', 'morph.target_height', 'morph.corner_radius', 'morph.rate', 'morph.fixed_part',
  'osse.rotation', 'guide.distance', 'guide.width', 'guide.aspect_ratio', 'guide.superellipse_n', 'guide.superformula',
  'guide.sf_a', 'guide.sf_b', 'guide.sf_m1', 'guide.sf_m2', 'guide.sf_n1', 'guide.sf_n2', 'guide.sf_n3',
  'guide.rotation', 'osse.circ_arc_term_angle', 'osse.circ_arc_radius',
  'enclosure.front_resolution', 'enclosure.back_resolution',
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

export function fieldMatchesQuery(field: ParameterDefinition, query: string): boolean {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return true;
  return [field.label, field.symbol, field.legacyKey, field.path, field.id, field.description]
    .some((value) => value?.toLocaleLowerCase().includes(normalized));
}

export function traceEntryIsRegistered(entry: { key: string; family?: DesignFamily }): boolean {
  return PARAMETER_REGISTRY.some((field) => field.legacyKey === entry.key && (!entry.family || fieldAppliesToFamily(field, entry.family)));
}
