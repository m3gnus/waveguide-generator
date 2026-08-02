const AVAILABLE = Object.freeze({ available: true, reason: '' });
const FREEFORM_LOCAL_EXPORT_REASON =
  'Not available for FREEFORM yet — use STEP export or save the .mwg config.';

export const FORMULA_EXPORT_CAPABILITIES = Object.freeze({
  FREEFORM: Object.freeze({
    stl: Object.freeze({ available: false, reason: FREEFORM_LOCAL_EXPORT_REASON }),
    fusion_csv: Object.freeze({ available: false, reason: FREEFORM_LOCAL_EXPORT_REASON }),
  }),
});

export function getExportCapability(formula, format) {
  return FORMULA_EXPORT_CAPABILITIES[formula]?.[format] || AVAILABLE;
}
