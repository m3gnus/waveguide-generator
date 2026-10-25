import type { ReactNode } from 'react';
import { useDeveloperDetails } from '../prefs/preferences';

/**
 * Technical detail behind what WG says: raw errors, evidence, identifiers.
 * Rendered only while "Show developer details" (Settings ▸ Advanced) is on,
 * and then one disclosure away; nothing here is ever the only statement of a
 * state, which the model's status line makes in plain words.
 */
export function DeveloperDetails({ children, summary = 'Developer details', className }: {
  children: ReactNode;
  summary?: string;
  className?: string;
}) {
  if (!useDeveloperDetails()) return null;
  return <details className={['developer-details', className].filter(Boolean).join(' ')}>
    <summary>{summary}</summary>
    {children}
  </details>;
}
