import { colorOffset, colorTable } from './colorTable';

/**
 * The directivity map as one bitmap at the plot's device resolution.
 *
 * It used to be one ECharts rectangle per interpolated cell, which at the
 * expanded size were 4 x 8.5 CSS px blocks -- visibly pixelated, and tens of
 * thousands of display-list entries per map. Here every device pixel is
 * interpolated from the solved grid itself, bilinearly in index space (the
 * sweep is log-spaced, so that is log-frequency), the same interpolation the
 * exported PNG uses.
 *
 * Geometry. The map's category axes are a display grid `factor` times denser
 * than the solved one; category `i` of `n` is centred at `(i + .5) / n` of the
 * plot, which is also where `contourPointToPixels` puts contour vertices. A
 * solved sample `j` sits on display category `j * factor`. Pixels beyond the
 * first and last sample centres hold the edge value, as the edge cells did.
 */

/** Above this the bitmap is drawn smaller and scaled up; the field is smooth. */
const MAX_BITMAP_PIXELS = 2_000_000;

interface AxisSamples { lower: Int32Array; weight: Float32Array }

/** For each output pixel along one axis, its lower solved index and weight. */
function axisSamples(pixels: number, sampleCount: number, factor: number, bottomUp: boolean): AxisSamples {
  const lower = new Int32Array(pixels);
  const weight = new Float32Array(pixels);
  const displayCount = (sampleCount - 1) * factor + 1;
  const lastLower = Math.max(0, sampleCount - 2);
  for (let pixel = 0; pixel < pixels; pixel += 1) {
    const centre = bottomUp ? pixels - pixel - .5 : pixel + .5;
    const position = Math.max(0, Math.min(sampleCount - 1, ((centre / pixels) * displayCount - .5) / factor));
    const index = Math.min(lastLower, Math.floor(position));
    lower[pixel] = index;
    weight[pixel] = sampleCount > 1 ? position - index : 0;
  }
  return { lower, weight };
}

/**
 * RGBA pixels of a directivity map. `values` holds the solved levels with row 0
 * the first angle, drawn at the bottom; `floor` is the bottom of the ramp. A
 * pixel with no solved neighbour (a masked rear hemisphere) stays transparent.
 */
export function directivityMapPixels(
  values: Array<Array<number | null>>,
  floor: number,
  colormap: string[],
  width: number,
  height: number,
  factor: number,
): Uint8ClampedArray {
  const pixels = new Uint8ClampedArray(width * height * 4);
  const rows = values.length;
  const columns = values[0]?.length ?? 0;
  if (!rows || !columns || width <= 0 || height <= 0) return pixels;
  const source = new Float64Array(rows * columns).fill(Number.NaN);
  for (let row = 0; row < rows; row += 1) {
    for (let column = 0; column < columns; column += 1) {
      const value = values[row][column];
      if (value !== null && Number.isFinite(value)) source[row * columns + column] = value;
    }
  }
  const table = colorTable(colormap);
  const x = axisSamples(width, columns, Math.max(1, factor), false);
  const y = axisSamples(height, rows, Math.max(1, factor), true);
  const right = columns > 1 ? 1 : 0;
  const up = rows > 1 ? columns : 0;
  let out = 0;
  for (let py = 0; py < height; py += 1) {
    const rowStart = y.lower[py] * columns;
    const ty = y.weight[py];
    for (let px = 0; px < width; px += 1, out += 4) {
      const index = rowStart + x.lower[px];
      const tx = x.weight[px];
      // Weights of the four neighbours; a missing one drops out and the rest
      // are renormalised, exactly as interpolateDirectivityGrid does.
      let sum = 0;
      let total = 0;
      const a = source[index];
      const b = source[index + right];
      const c = source[index + up];
      const d = source[index + up + right];
      let w = (1 - tx) * (1 - ty);
      if (w > 0 && !Number.isNaN(a)) { sum += a * w; total += w; }
      w = tx * (1 - ty);
      if (w > 0 && !Number.isNaN(b)) { sum += b * w; total += w; }
      w = (1 - tx) * ty;
      if (w > 0 && !Number.isNaN(c)) { sum += c * w; total += w; }
      w = tx * ty;
      if (w > 0 && !Number.isNaN(d)) { sum += d * w; total += w; }
      if (total === 0) continue;
      const color = colorOffset(1 - sum / total / floor);
      pixels[out] = table[color];
      pixels[out + 1] = table[color + 1];
      pixels[out + 2] = table[color + 2];
      pixels[out + 3] = 255;
    }
  }
  return pixels;
}

/** The map bitmap as a canvas ECharts can draw, or null where there is no 2D context. */
export function directivityMapCanvas(
  values: Array<Array<number | null>>,
  floor: number,
  colormap: string[],
  width: number,
  height: number,
  factor: number,
): HTMLCanvasElement | null {
  const shrink = Math.min(1, Math.sqrt(MAX_BITMAP_PIXELS / Math.max(1, width * height)));
  const drawnWidth = Math.max(1, Math.round(width * shrink));
  const drawnHeight = Math.max(1, Math.round(height * shrink));
  const canvas = document.createElement('canvas');
  canvas.width = drawnWidth;
  canvas.height = drawnHeight;
  let context: CanvasRenderingContext2D | null = null;
  try { context = canvas.getContext('2d'); } catch { return null; }
  if (!context) return null;
  const image = context.createImageData(drawnWidth, drawnHeight);
  image.data.set(directivityMapPixels(values, floor, colormap, drawnWidth, drawnHeight, factor));
  context.putImageData(image, 0, 0);
  return canvas;
}

/**
 * The pixel span of solved sample `index` along one plot axis: half a solved
 * step either side of its centre, clipped to the plot. This is the hover
 * target that reports the solved value under the pointer.
 */
export function solvedSampleSpan(index: number, factor: number, displayCount: number, start: number, length: number): [number, number] {
  const unit = length / Math.max(1, displayCount);
  const centre = (index * factor + .5) * unit;
  const half = (Math.max(1, factor) / 2) * unit;
  return [start + Math.max(0, centre - half), start + Math.min(length, centre + half)];
}
