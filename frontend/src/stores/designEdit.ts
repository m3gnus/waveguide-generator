/** Persisted editor record shared by the design store and domain editors. */
export interface EditRecord<TDoc> {
  document: TDoc | null;
  authoring: unknown;
  status: 'valid' | 'unfinished' | 'invalid';
  draftKey: string;
}

/** Domain-owned records enter through commitEmbedded, not ordinary parameter edits. */
export type EmbeddedDesignUpdates = Record<string, unknown>;
