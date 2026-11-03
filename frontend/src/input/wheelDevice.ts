export type ScrollDevice = 'wheel' | 'trackpad';
export type WheelDeviceSample = Pick<WheelEvent, 'deltaX' | 'deltaY' | 'deltaMode'>
  & { wheelDeltaY?: number; wheelDelta?: number };

/** Horizontal movement is trackpad evidence; callers decide its precedence. */
export function hasHorizontalScroll(deltaX: number): boolean {
  return deltaX !== 0;
}

/** Classify legacy notch data without choosing a source or fallback policy. */
export function legacyWheelDeviceEvidence(delta: number): ScrollDevice | null {
  if (delta === 0) return null;
  return Math.abs(delta) % 120 !== 0 ? 'trackpad' : 'wheel';
}

/** Start-of-stream evidence for the 3D viewport's buffered gesture policy.
 * WebKit uses pixels for BOTH devices: a native line step becomes 40 pixels,
 * while wheelDeltaY can be -3 * deltaY for either device. Precise trackpad
 * deltas can also be integers. Neither legacy multiples nor integers suffice.
 * Sources: WebKit PlatformEventFactoryMac.mm / WheelEvent.cpp; Mapbox GL JS
 * scroll_zoom.ts (historical fractional notch); VS Code scrollableElement.ts.
 */
export function wheelDeviceEvidence(event: WheelDeviceSample): ScrollDevice | null {
  // Numeric constants also allow use outside a browser.
  if (event.deltaMode !== 0) return 'wheel';
  if (hasHorizontalScroll(event.deltaX)) return 'trackpad';
  const y = Math.abs(event.deltaY);
  const legacy = event.wheelDeltaY || event.wheelDelta || 0;
  if (legacy !== 0) {
    if (legacyWheelDeviceEvidence(legacy) === 'trackpad') return 'trackpad';
    // WebKit's proportional legacy value is no independent notch evidence.
    // Let ambiguous pixel shapes (e.g. 40/-120, 80/-240) reach the stream probe.
    if (legacy !== -3 * event.deltaY) return 'wheel';
  }
  // Historical macOS wheel quantum also handled by Mapbox GL JS.
  if (y !== 0 && y % 4.000244140625 === 0) return 'wheel';
  if (y >= 100 && (y % 100 === 0 || y % 120 === 0)) return 'wheel';
  if (y < 40 || !Number.isInteger(y)) return 'trackpad';
  return null;
}
