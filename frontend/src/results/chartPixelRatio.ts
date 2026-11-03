/**
 * Keep thin engineering traces anti-aliased on ordinary 1x displays.
 *
 * ECharts otherwise sizes its backing canvas at exactly one physical pixel per
 * CSS pixel there, which makes diagonal directivity contours visibly stair-step
 * compared with the high-DPI PNG renderer. A 2x floor is enough to remove that
 * effect without the memory cost of letting unusually large ratios run free.
 *
 * Its own module so the canvas-drawn beam map can share it without pulling
 * ECharts into the eager bundle.
 */
export function chartPixelRatio(deviceRatio = globalThis.devicePixelRatio || 1): number {
  return Math.min(3, Math.max(2, deviceRatio));
}
