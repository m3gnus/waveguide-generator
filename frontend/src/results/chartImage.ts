import { writeToOutputFolder } from '../api/workspace';
import type { ConfirmReplacements } from '../api/exportDestination';
import { abortableActivity, activityOperation } from '../shell/activityOperation';
import { writePendingClipboardItem } from '../api/clipboard';
import { httpErrorMessage, ProgramFault } from '../api/httpError';
import type { ChartSpec } from './chartSpec';

/**
 * Chart images, drawn by hornlab-plots on the server.
 *
 * A card's Copy and Export PNG, the `png` export format and the HTML report all
 * get their images here, in the export theme, from the same series the screen
 * draws, the 3D balloon and the forward beam map included.
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
 * Draw several chart images one after another, counted on the status bar.
 * Inside an export this joins the export's activity, which keeps its own
 * label unless told: say what the counted steps are, then hand it back.
 */
export async function drawCounted<T>(
  label: string,
  fetcher: typeof fetch,
  items: readonly T[],
  draw: (item: T, fetcher: typeof fetch) => Promise<string>,
): Promise<string[]> {
  return activityOperation({ kind: 'export', label }, fetcher, async (scoped, activity) => {
    const outer = activity.currentLabel();
    activity.label(label);
    try {
      const images: string[] = [];
      activity.progress(0, items.length);
      for (const [index, item] of items.entries()) {
        images.push(await draw(item, scoped));
        activity.progress(index + 1, items.length);
      }
      return images;
    } finally {
      activity.label(outer);
    }
  });
}

/**
 * Draw a chart image as its own status-bar activity, abortable by `signal`
 * (an export whose folder dialog was cancelled) or from the status bar.
 */
export function drawChartPng(draw: (fetcher: typeof fetch) => Promise<Blob>, signal?: AbortSignal): Promise<Blob> {
  return abortableActivity({ kind: 'export', label: 'Drawing chart PNG' }, fetch, signal, (scoped, aborted) => (
    draw((input, init) => scoped(input, { ...init, signal: aborted }))
  ));
}

/**
 * Write one chart image into the folder the user chose for it.
 *
 * The image is started before the folder is asked for (`drawChartPng`) and
 * handed in as a promise, so it is drawn while the user picks a folder and
 * describes the chart as it was when Export was pressed.
 */
export async function saveChartPng(
  image: Promise<Blob>,
  filename: string,
  destination: string,
  confirmReplacements?: ConfirmReplacements,
): Promise<string> {
  return activityOperation({ kind: 'export', label: 'Writing chart PNG' }, fetch, async (scoped) => {
    const blob = await image;
    const written = await writeToOutputFolder(
      '', [{ filename, blob }], scoped, 'confirm', destination, confirmReplacements,
    );
    return written.directory;
  });
}
