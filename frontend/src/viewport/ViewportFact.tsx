import type { ReactNode } from 'react';

/** One label and value in the viewport header: the only way a fact is written there. */
export function ViewportFact({ label, unit, title, children }: {
  label: string;
  unit?: string;
  title?: string;
  children: ReactNode;
}) {
  return <span className="viewport-fact" title={title}>
    <span>{label}</span> <b>{children}</b>{unit && <> <em>{unit}</em></>}
  </span>;
}
