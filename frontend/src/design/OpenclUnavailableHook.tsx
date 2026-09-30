import type { ReactNode } from 'react';
import type { OpenclUnavailableReason } from '../jobs/actions';

/** Driver-guidance integration hook shared by the numba notice and solver settings.
 * The future component receives this structured reason together with platform;
 * it owns all driver guidance. Never derive a reason from capability prose.
 */
export function OpenclUnavailableHook({ reason, children }: {
  reason: OpenclUnavailableReason | null | undefined;
  children?: ReactNode;
}) {
  return <div data-opencl-unavailable-reason={reason ?? undefined}>{children}</div>;
}
