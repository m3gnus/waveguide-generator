export type ScrollDevice = 'wheel' | 'trackpad';
export type WheelDeviceSample = Pick<WheelEvent, 'deltaX' | 'deltaY' | 'deltaMode'>
  & { wheelDeltaY?: number; wheelDelta?: number };

/** Shared start-of-stream evidence for the sketch and 3D viewport.
 * WebKit uses pixels for BOTH devices: a native line step becomes 40 pixels,
 * while wheelDeltaY retains 120 units per tick. Precise trackpad deltas can
 * also be integers. Neither deltaMode alone nor an integer test suffices.
 * Sources: WebKit PlatformEventFactoryMac.mm / WheelEvent.cpp; Mapbox GL JS
 * scroll_zoom.ts (historical fractional notch); VS Code scrollableElement.ts.
 */
export function wheelDeviceEvidence(event: WheelDeviceSample): ScrollDevice | null {
  // Numeric constants also allow use outside a browser (sketch math tests).
  if (event.deltaMode !== 0) return 'wheel';
  if (event.deltaX !== 0) return 'trackpad';
  const y = Math.abs(event.deltaY);
  const legacy = Math.abs(event.wheelDeltaY || event.wheelDelta || 0);
  if (legacy !== 0) return legacy % 120 === 0 ? 'wheel' : 'trackpad';
  // Historical macOS wheel quantum also handled by Mapbox GL JS.
  if (y !== 0 && y % 4.000244140625 === 0) return 'wheel';
  if (y >= 100 && (y % 100 === 0 || y % 120 === 0)) return 'wheel';
  if (y < 40 || !Number.isInteger(y)) return 'trackpad';
  return null;
}
