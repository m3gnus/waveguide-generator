import { useEffect, useMemo, useRef, useState } from 'react';
import {
  confirmSolverFrame,
  getSolverFrame,
  type SolverFrameAxisOption,
  type SolverFramePreview,
} from '../api/solverFrame';
import { parseMSH, type ParsedMSH } from '../viewport/mshParser';
import {
  framePreviewProjection,
  type FramePreviewProjection,
  type SolverFrameAxis,
} from '../viewport/solverFrame';

type Loaded = { preview: Extract<SolverFramePreview, { linked: false }>; mesh: ParsedMSH };

async function loadModel(ingestId: string, fetcher: typeof fetch): Promise<ParsedMSH> {
  // The display tessellation when it exists; the solve mesh is the same model.
  for (const artifact of ['viewport-mesh', 'mesh']) {
    const response = await fetcher(`/api/cadlink/ingest/${encodeURIComponent(ingestId)}/${artifact}`);
    if (response.ok && response.status !== 202) return parseMSH(await response.text());
  }
  throw new Error('the prepared model’s mesh is not available to preview');
}

const VIEW_SIZE = { width: 220, height: 150 };

function drawView(canvas: HTMLCanvasElement, projection: FramePreviewProjection, index: number) {
  const context = canvas.getContext('2d');
  if (!context) return;
  const view = projection.views[index];
  const { min, max } = projection.bounds;
  // The origin stays in view: it is where the solver measures from.
  const low = [Math.min(min[0], 0), Math.min(min[1], 0)];
  const high = [Math.max(max[0], 0), Math.max(max[1], 0)];
  const span = Math.max(high[0] - low[0], high[1] - low[1], 1e-9);
  const margin = 14;
  const scale = (Math.min(VIEW_SIZE.width, VIEW_SIZE.height) - 2 * margin) / span;
  const toX = (h: number) => margin + (h - low[0]) * scale;
  const toY = (v: number) => VIEW_SIZE.height - margin - (v - low[1]) * scale;
  context.clearRect(0, 0, VIEW_SIZE.width, VIEW_SIZE.height);
  for (let triangle = 0; triangle < view.source.length; triangle += 1) {
    const offset = triangle * 6;
    context.beginPath();
    context.moveTo(toX(view.triangles[offset]), toY(view.triangles[offset + 1]));
    context.lineTo(toX(view.triangles[offset + 2]), toY(view.triangles[offset + 3]));
    context.lineTo(toX(view.triangles[offset + 4]), toY(view.triangles[offset + 5]));
    context.closePath();
    context.fillStyle = view.source[triangle] ? 'rgba(220, 90, 40, 0.9)' : 'rgba(128, 128, 128, 0.25)';
    context.fill();
  }
  // Solver +Z from the origin: the radiation axis every engine measures along.
  const originX = toX(0);
  const originY = toY(0);
  context.strokeStyle = 'rgb(40, 120, 220)';
  context.fillStyle = 'rgb(40, 120, 220)';
  context.lineWidth = 2;
  context.beginPath();
  context.moveTo(originX, originY);
  context.lineTo(VIEW_SIZE.width - 4, originY);
  context.stroke();
  context.beginPath();
  context.arc(originX, originY, 3, 0, Math.PI * 2);
  context.fill();
}

function FrameView({ projection, index, label }: { projection: FramePreviewProjection; index: number; label: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (canvas.current) drawView(canvas.current, projection, index);
  }, [projection, index]);
  return <figure className="cad-frame-view">
    <canvas ref={canvas} width={VIEW_SIZE.width} height={VIEW_SIZE.height} role="img" aria-label={label}/>
    <figcaption>{label}</figcaption>
  </figure>;
}

/** Confirm the solver frame of an unlinked (CAD-authored) model, once for its
 * project, after seeing the model in that frame. The preview applies the exact
 * matrix the backend meshes with; nothing here computes a frame of its own. */
export function CadSolverFrameConfirm({ operationId, label, onConfirmed, fetcher = fetch }: {
  operationId: string;
  label: string;
  onConfirmed: (axis: SolverFrameAxis) => void;
  fetcher?: typeof fetch;
}) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [axis, setAxis] = useState<SolverFrameAxis | null>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    let current = true;
    setLoaded(null);
    setError(null);
    void (async () => {
      const preview = await getSolverFrame({ operationId }, fetcher);
      if (preview.linked) throw new Error('this model is linked to a WG design and needs no frame');
      const mesh = await loadModel(preview.ingestId, fetcher);
      if (!current) return;
      setLoaded({ preview, mesh });
      const start = preview.confirmed && preview.axes.some((item) => item.axis === preview.confirmed?.axis && item.allowed)
        ? preview.confirmed.axis
        : preview.recordAxis;
      setAxis(start);
    })().catch((reason: unknown) => {
      if (current) setError(reason instanceof Error ? reason.message : String(reason));
    });
    return () => { current = false; };
  }, [fetcher, operationId]);

  const option: SolverFrameAxisOption | null = loaded && axis
    ? loaded.preview.axes.find((item) => item.axis === axis) ?? null
    : null;
  const projection = useMemo(
    () => (loaded && option ? framePreviewProjection(loaded.mesh, option.previewFromRecord) : null),
    [loaded, option],
  );

  if (error) {
    return <div className="cad-solver-frame" role="status">Cannot preview the solver frame of {label}: {error}</div>;
  }
  if (!loaded || !axis || !option || !projection) {
    return <div className="cad-solver-frame" data-frame-preview="loading" role="status">Loading the solver frame preview…</div>;
  }
  const confirm = () => {
    setConfirming(true);
    void confirmSolverFrame({ operationId }, axis, fetcher)
      .then(() => onConfirmed(axis))
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setConfirming(false));
  };
  return <div className="cad-solver-frame" data-frame-preview="ready">
    <span>
      {label} was authored in CAD, so WG cannot know which way it radiates. Choose the model axis
      that points out of the mouth. WG solves along the solver +Z (blue) from the model’s origin, and
      remembers your choice for this project.
    </span>
    <fieldset>
      <legend>Radiates along</legend>
      {loaded.preview.axes.map((item) => <label key={item.axis} title={item.reason ?? undefined}>
        <input
          type="radio"
          name={`solver-frame-${operationId}`}
          value={item.axis}
          checked={item.axis === axis}
          disabled={!item.allowed || confirming}
          onChange={() => setAxis(item.axis)}
        />
        {item.axis}
      </label>)}
    </fieldset>
    {loaded.preview.axes.some((item) => !item.allowed && item.reason)
      && <span>{loaded.preview.axes.find((item) => !item.allowed)?.reason}</span>}
    <div className="cad-frame-views" data-frame-preview-axis={axis}>
      <FrameView projection={projection} index={0} label={`Side: model ${axis} → solver +Z, solver Y up`}/>
      <FrameView projection={projection} index={1} label={`Top: model ${axis} → solver +Z, solver X up`}/>
    </div>
    <span>Solver frame: model {axis} → solver +Z. Drive sources are shown in orange.</span>
    <button
      className="primary"
      data-action="confirm-frame"
      disabled={confirming}
      aria-label={`Confirm solver frame ${axis} and solve: ${label}`}
      onClick={confirm}
    >Confirm {axis} and solve</button>
  </div>;
}
