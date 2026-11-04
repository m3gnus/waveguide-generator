import { writeToOutputFolder } from '../api/workspace';
import type { ConfirmReplacements } from '../api/exportDestination';
import { activityOperation } from '../shell/activityOperation';
import { writePendingClipboardItem } from '../api/clipboard';

const DEFAULT_PIXEL_RATIO = 2;
const MAX_PIXEL_RATIO = 3;

function visibleCanvases(container: HTMLElement): HTMLCanvasElement[] {
  return [...container.querySelectorAll<HTMLCanvasElement>('canvas')].filter((canvas) => {
    const rect = canvas.getBoundingClientRect();
    const style = getComputedStyle(canvas);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  });
}

function layerPixelRatio(canvas: HTMLCanvasElement): number {
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return 1;
  return Math.max(canvas.width / rect.width, canvas.height / rect.height);
}

/**
 * Flatten every visible canvas in a result chart into one bitmap.
 *
 * ECharts progressively rendered heatmaps are several transparent canvases
 * stacked in DOM order. The browser's native Copy Image action sees only the
 * top layer; drawing every layer here reproduces the composited chart that is
 * actually visible on screen. The same path also captures the 2D beam map and
 * the current WebGL balloon view.
 */
export function composeChartCanvases(
  container: HTMLElement,
  background?: string,
  createCanvas: () => HTMLCanvasElement = () => document.createElement('canvas'),
): HTMLCanvasElement {
  const layers = visibleCanvases(container);
  if (!layers.length) throw new Error('This chart is empty, so there is no image to copy. Choose a chart with data.');

  const bounds = container.getBoundingClientRect();
  if (!bounds.width || !bounds.height) throw new Error('Open this chart, then copy or export it.');
  const pixelRatio = Math.min(MAX_PIXEL_RATIO, Math.max(
    DEFAULT_PIXEL_RATIO,
    ...layers.map(layerPixelRatio).filter(Number.isFinite),
  ));
  const output = createCanvas();
  output.width = Math.max(1, Math.round(bounds.width * pixelRatio));
  output.height = Math.max(1, Math.round(bounds.height * pixelRatio));
  const context = output.getContext('2d');
  if (!context) throw new Error('WG could not make an image of this chart. Try again.');

  context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  context.fillStyle = background || getComputedStyle(container).backgroundColor || '#fff';
  context.fillRect(0, 0, bounds.width, bounds.height);
  for (const layer of layers) {
    const rect = layer.getBoundingClientRect();
    context.drawImage(layer, rect.left - bounds.left, rect.top - bounds.top, rect.width, rect.height);
  }
  return output;
}

function pngBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error('WG could not make an image of this chart. Try again.'));
    }, 'image/png');
  });
}

export async function chartPngBlob(container: HTMLElement, background?: string): Promise<Blob> {
  return pngBlob(composeChartCanvases(container, background));
}

export async function copyChartPng(container: HTMLElement, background?: string): Promise<void> {
  if (!navigator.clipboard?.write || typeof ClipboardItem === 'undefined') {
    throw new Error('Copying images is not available here. Use Export PNG instead.');
  }
  await activityOperation({ kind: 'export', label: 'Copying chart PNG' }, fetch, async () => {
    await writePendingClipboardItem('image/png', chartPngBlob(container, background));
  });
}

/**
 * Write one chart image into the folder the user chose for it.
 *
 * This was an `<a download>`, which reaches a browser tab and reaches nobody in
 * the desktop window -- a WebView2 host with no download handler -- so the
 * image was encoded and then silently went nowhere. It takes the same route
 * every other export takes: the user names a folder, and the server writes it.
 * The caller captures this detached canvas together with its filename before
 * asking for a destination, so a following run cannot replace its pixels.
 */
export async function saveChartPng(
  canvas: HTMLCanvasElement,
  filename: string,
  destination: string,
  confirmReplacements?: ConfirmReplacements,
): Promise<string> {
  return activityOperation({ kind: 'export', label: 'Encoding chart PNG' }, fetch, async (scoped, activity) => {
    const blob = await pngBlob(canvas);
    activity.label('Writing chart PNG');
    const written = await writeToOutputFolder(
      '', [{ filename, blob }], scoped, 'confirm', destination, confirmReplacements,
    );
    return written.directory;
  });
}
