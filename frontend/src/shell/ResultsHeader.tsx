import type { ReactNode } from 'react';

/** One layout for CAD and parametric result controls. */
export function ResultsHeader({ identity, controls }: { identity: ReactNode; controls: ReactNode }) {
  return <div className="results-toolbar results-header">
    <div className="results-header-identity">{identity}</div>
    <div className="results-header-controls">{controls}</div>
  </div>;
}
