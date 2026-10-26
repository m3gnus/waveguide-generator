import { fit, type Viewport } from '../view';
import type { DisplayModel } from './types';

export function displayView(display: DisplayModel, size: Viewport, all = false) {
  const { bottom = 0, top = 0, right = 0, offsetX = 0, margin = 32 } = display.fitPadding ?? {};
  const fitted = fit(display.bounds(all), { width: Math.max(100, size.width - right), height: Math.max(100, size.height - bottom - top) }, margin);
  return { ...fitted, offsetX: fitted.offsetX + offsetX, offsetY: fitted.offsetY + top };
}
