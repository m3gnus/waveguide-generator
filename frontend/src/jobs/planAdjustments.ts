import type { PlanAdjustment } from './actions';

/**
 * One line describing a change the server makes to the design before solving
 * it, or null for a kind this client does not describe.
 *
 * An explicit 0 mm wall is worded as an override, and an unset wall as a
 * default: the first changes something the user asked for.
 */
export function planAdjustmentNotice(adjustment: PlanAdjustment): string | null {
  if (adjustment.kind !== 'bempp_wall_default') return null;
  const effective = `${adjustment.effective_mm} mm`;
  if (adjustment.requested === 'explicit_zero') {
    return `BEMPP cannot solve a free-standing bare shell, so the 0 mm wall thickness is overridden: this solve uses a ${effective} closed wall.`;
  }
  return `No wall thickness is set, so this free-standing BEMPP solve uses the ${effective} closed-wall default.`;
}
