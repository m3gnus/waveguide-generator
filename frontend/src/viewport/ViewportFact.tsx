import type { ReactNode } from 'react';

/**
 * The order the viewport header gives facts up when one line cannot hold them
 * all (see `oneLineFit.ts`): the highest rank goes first. The mouth, or a
 * source disk's diameter, has no rank and always stays.
 */
export const FACT_DROP = {
  timing: 8,
  kind: 7,
  solveMesh: 6,
  solveDomain: 5,
  overall: 4,
  enclosure: 3,
  depth: 2,
} as const;

/** One label and value in the viewport header: the only way a fact is written there. */
export function ViewportFact({ label, unit, title, drop, children }: {
  label: string;
  unit?: string;
  title?: string;
  drop?: number;
  children: ReactNode;
}) {
  return <span className="viewport-fact" title={title} data-drop={drop}>
    <span>{label}</span> <b>{children}</b>{unit && <> <em>{unit}</em></>}
  </span>;
}
