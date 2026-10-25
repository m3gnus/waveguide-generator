import { cadModelSelection, useCadModelSelection } from './cadModelState';

/**
 * Whether WG's own waveguide definition applies to what is on screen.
 *
 * The waveguide-formula sections describe a horn WG generates. A model whose
 * geometry was authored in CAD has no such horn -- no `.cfg`, no family, and
 * nothing the OSSE or R-OSSE fields could drive -- so every field in them would
 * be an input that cannot move the model.
 *
 * With no CAD model selected the definition still applies: the CAD workflow
 * starts by editing a parametric design and sending it out. Once a model is
 * selected, the selection's own authorship decides (`authoredInCad`, from
 * `selectCadModelState`), before any preparation exists. A prepared project
 * with no design behind it is CAD-authored too, whatever the bundle says.
 */
export function waveguideDefinitionApplies(
  project: { design_id?: string | null } | null | undefined,
  authoredInCad = false,
): boolean {
  if (authoredInCad) return false;
  return project ? Boolean(project.design_id) : true;
}

/** Reactive form, for the parameter rail. */
export function useWaveguideDefinitionApplies(): boolean {
  const model = useCadModelSelection();
  return waveguideDefinitionApplies(model.record?.project ?? null, model.authoredInCad);
}

/** Snapshot form, for the command palette, which builds its list on demand. */
export function waveguideDefinitionAppliesNow(): boolean {
  const model = cadModelSelection();
  return waveguideDefinitionApplies(model.record?.project ?? null, model.authoredInCad);
}
