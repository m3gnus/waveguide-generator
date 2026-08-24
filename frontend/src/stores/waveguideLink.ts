import { useCadReturnStore } from './cadReturn';

/**
 * Whether WG's own waveguide definition applies to what is on screen.
 *
 * The waveguide-formula sections describe a horn WG generates. A project whose
 * geometry was authored in CAD has no such horn -- no `.cfg`, no family, and
 * nothing the OSSE or R-OSSE fields could drive -- so every field in them would
 * be an input that cannot move the model.
 *
 * Stated as "hide only when we positively know", not "show only when linked".
 * The CAD workflow starts by editing a parametric design and sending it out, so
 * CAD mode before any return has arrived must still offer the formula; so must
 * a return from an older ingest that recorded no project at all. The single
 * case that hides is the one the ingest can prove: a resolved project with no
 * design behind it, which is exactly how a CAD-authored return resolves.
 */
export function waveguideDefinitionApplies(
  project: { design_id?: string | null } | null | undefined,
): boolean {
  return project ? Boolean(project.design_id) : true;
}

/** Reactive form, for the parameter rail. */
export function useWaveguideDefinitionApplies(): boolean {
  return waveguideDefinitionApplies(
    useCadReturnStore((state) => state.ingestRecord?.project ?? null),
  );
}

/** Snapshot form, for the command palette, which builds its list on demand. */
export function waveguideDefinitionAppliesNow(): boolean {
  return waveguideDefinitionApplies(
    useCadReturnStore.getState().ingestRecord?.project ?? null,
  );
}
