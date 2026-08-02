const AVAILABLE = Object.freeze({ available: true, reason: '' });
const FREEFORM_LOCAL_EXPORT_REASON =
  'Not available for FREEFORM yet — use STEP export or save the .mwg config.';

function unavailableLocalExports(reason) {
  return Object.freeze({
    stl: Object.freeze({ available: false, reason }),
    fusion_csv: Object.freeze({ available: false, reason }),
  });
}

export const FORMULA_EXPORT_CAPABILITIES = Object.freeze({
  // Keep these explicit rather than importing geometry/useCases.js: that module
  // pulls in the export pipeline and would create an application-layer cycle.
  // src/modules/export/index.js remains the runtime guard for stale callers.
  ICW: unavailableLocalExports(
    'Not available for ICW yet — the local geometry engine cannot build it; use STEP export.'
  ),
  LOOKUP: unavailableLocalExports(
    'Not available for LOOKUP yet — the local geometry engine cannot build it; use STEP export.'
  ),
  FREEFORM: unavailableLocalExports(FREEFORM_LOCAL_EXPORT_REASON),
});

export function getExportCapability(formula, format) {
  return FORMULA_EXPORT_CAPABILITIES[formula]?.[format] || AVAILABLE;
}
