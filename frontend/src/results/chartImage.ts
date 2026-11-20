import { writeToOutputFolder } from '../api/workspace';
import type { ConfirmReplacements } from '../api/exportDestination';
import { activityOperation } from '../shell/activityOperation';
import { writePendingClipboardItem } from '../api/clipboard';
import { httpErrorMessage, ProgramFault } from '../api/httpError';
import type { ChartSpec } from './chartSpec';

/**
 * Chart images, drawn by hornlab-plots on the server.
 *
 * A card's Copy and Export PNG, the `png` export format and the HTML report all
 * get their images here, in the export theme, from the same series the screen
 * draws. The 3D balloon and the forward beam map are views rather than charts:
 * hornlab-plots has no renderer for them, so their image is still the canvas on
 * screen (`composeChartCanvases`).
 */
export const CHART_IMAGE_FAILED = 'WG could not draw this chart image. Try again.';

/** Decode a `data:image/png;base64,...` (or bare base64) image into a PNG blob. */
export function pngBlobFromDataUri(image: string): Blob {
  const encoded = image.includes(',') ? image.slice(image.indexOf(',') + 1) : image;
  const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
  return new Blob([bytes], { type: 'image/png' });
}

async function postRender(fetcher: typeof fetch, path: string, body: unknown, fallback: string): Promise<string> {
  const response = await fetcher(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await httpErrorMessage(response, fallback));
  const { image } = await response.json() as { image?: string };
  if (!image) throw new ProgramFault(`HornLab plots returned no image from ${path}.`);
  return image;
}

/** One chart described as data, as a PNG data URI in `theme`. */
export function renderChartSpec(spec: ChartSpec, theme: string, fetcher: typeof fetch, fallback = CHART_IMAGE_FAILED): Promise<string> {
  return postRender(fetcher, '/api/render-chart', { spec, theme }, fallback);
}

/** A directivity map, from `buildDirectivityRenderPayload`, as a PNG data URI. */
export function renderDirectivityMap(payload: Record<string, unknown>, fetcher: typeof fetch, fallback = CHART_IMAGE_FAILED): Promise<string> {
  return postRender(fetcher, '/api/render-directivity', payload, fallback);
}

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
 * Flatten the visible canvases of a view (the 3D balloon, the beam map) into
 * one bitmap: the current camera and frequency, as they are on screen.
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

export function canvasPngBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error('WG could not make an image of this chart. Try again.'));
    }, 'image/png');
  });
}

/**
 * Copy a chart image. `image` is called inside the click gesture and its
 * pending blob handed to the clipboard at once, because WebKit refuses a
 * clipboard write that starts after a fetch.
 */
export async function copyChartPng(image: (fetcher: typeof fetch) => Promise<Blob>): Promise<void> {
  if (!navigator.clipboard?.write || typeof ClipboardItem === 'undefined') {
    throw new Error('Copying images is not available here. Use Export PNG instead.');
  }
  await activityOperation({ kind: 'export', label: 'Copying chart PNG' }, fetch, async (scoped) => {
    await writePendingClipboardItem('image/png', image(scoped));
  });
}

/**
 * Write one chart image into the folder the user chose for it.
 *
 * The image is started before the folder is asked for and handed in as a
 * promise, so it is drawn while the user picks a folder and describes the
 * chart as it was when Export was pressed, not after a later run replaced it.
 */
export async function saveChartPng(
  image: Promise<Blob>,
  filename: string,
  destination: string,
  confirmReplacements?: ConfirmReplacements,
): Promise<string> {
  return activityOperation({ kind: 'export', label: 'Drawing chart PNG' }, fetch, async (scoped, activity) => {
    const blob = await image;
    activity.label('Writing chart PNG');
    const written = await writeToOutputFolder(
      '', [{ filename, blob }], scoped, 'confirm', destination, confirmReplacements,
    );
    return written.directory;
  });
}
