/** Saved interpretation; adding an inherited version changes this list only. */
export const CURRENT_ATH_IMPORT_VERSIONS = ['ath-2026-09a-v2', 'ath-2026-09a-v3'] as const;
export const ATH_IMPORT_VERSIONS = ['ath-2026-08c-v1', ...CURRENT_ATH_IMPORT_VERSIONS] as const;
export type AthImportVersion = typeof ATH_IMPORT_VERSIONS[number];

export const isAthImportVersion = (version: unknown): version is AthImportVersion =>
  (ATH_IMPORT_VERSIONS as readonly unknown[]).includes(version);

export const usesCurrentAthImport = (version: unknown): boolean =>
  (CURRENT_ATH_IMPORT_VERSIONS as readonly unknown[]).includes(version);
