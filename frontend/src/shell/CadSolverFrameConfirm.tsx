import { useEffect, useMemo, useRef, useState } from 'react';
import {
  confirmSolverFrame,
  getSolverFrame,
  type SolverFrameAxisOption,
  type SolverFramePreview,
  type SolverFrameSnapshot,
} from '../api/solverFrame';
import { parseMSH, type ParsedMSH } from '../viewport/mshParser';
import {
  framePreviewProjection,
  type FramePreviewProjection,
  type SolverFrameAxis,
} from '../viewport/solverFrame';

type Loaded = { preview: Extract<SolverFramePreview, { linked: false }>; mesh: ParsedMSH };

async function loadModel(ingestId: string, fetcher: typeof fetch): Promise<ParsedMSH> {
  // This thumbnail draws every triangle twice on a 220×150 canvas. The exact
  // solve mesh is already available and preserves the same source tags.
  const response = await fetcher(`/api/cadlink/ingest/${encodeURIComponent(ingestId)}/mesh`);
  if (response.ok) return parseMSH(await response.text());
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
  const margin = 14;
  const scale = Math.min(
    (VIEW_SIZE.width - 2 * margin) / Math.max(high[0] - low[0], 1e-9),
    (VIEW_SIZE.height - 2 * margin) / Math.max(high[1] - low[1], 1e-9),
  );
  // Centred, so a model on either side of the axis reads as such.
  const offsetX = (VIEW_SIZE.width - (high[0] - low[0]) * scale) / 2;
  const offsetY = (VIEW_SIZE.height - (high[1] - low[1]) * scale) / 2;
  const toX = (h: number) => offsetX + (h - low[0]) * scale;
  const toY = (v: number) => VIEW_SIZE.height - offsetY - (v - low[1]) * scale;
  const trace = (triangle: number) => {
    const offset = triangle * 6;
    context.beginPath();
    context.moveTo(toX(view.triangles[offset]), toY(view.triangles[offset + 1]));
    context.lineTo(toX(view.triangles[offset + 2]), toY(view.triangles[offset + 3]));
    context.lineTo(toX(view.triangles[offset + 4]), toY(view.triangles[offset + 5]));
    context.closePath();
  };
  context.clearRect(0, 0, VIEW_SIZE.width, VIEW_SIZE.height);
  context.fillStyle = 'rgba(128, 128, 128, 0.25)';
  for (let triangle = 0; triangle < view.source.length; triangle += 1) {
    if (view.source[triangle]) continue;
    trace(triangle);
    context.fill();
  }
  // Sources last, filled and outlined: a flat throat seen edge-on has no area
  // and would otherwise vanish, which is exactly the view that matters most.
  context.fillStyle = 'rgba(220, 90, 40, 0.9)';
  context.strokeStyle = 'rgba(220, 90, 40, 0.9)';
  context.lineWidth = 2;
  for (let triangle = 0; triangle < view.source.length; triangle += 1) {
    if (!view.source[triangle]) continue;
    trace(triangle);
    context.fill();
    context.stroke();
  }
  // Solver +Z from the origin: the radiation axis every engine measures along.
  const originX = toX(0);
  const originY = toY(0);
  context.strokeStyle = 'rgb(40, 120, 220)';
  context.fillStyle = 'rgb(40, 120, 220)';
  context.lineWidth = 2;
  context.beginPath();
  context.moveTo(originX, originY);
  context.lineTo(VIEW_SIZE.width - 6, originY);
  context.stroke();
  context.beginPath();
  context.moveTo(VIEW_SIZE.width - 2, originY);
  context.lineTo(VIEW_SIZE.width - 10, originY - 4);
  context.lineTo(VIEW_SIZE.width - 10, originY + 4);
  context.closePath();
  context.fill();
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
 * project, after seeing the model in that frame -- or change the one confirmed.
 * The preview applies the exact matrix the backend meshes with; nothing here
 * computes a frame of its own. No axis is chosen for the user: until one has
 * been confirmed, nothing is preselected and nothing can be confirmed. */
export function CadSolverFrameConfirm({ snapshot, label, onConfirmed, mode = 'confirm', fetcher = fetch }: {
  snapshot: SolverFrameSnapshot;
  label: string;
  onConfirmed: (axis: SolverFrameAxis) => void;
  /** 'confirm': an operation waits for it, and confirming solves. 'change': the
   * project's frame, changed for later preparations only. */
  mode?: 'confirm' | 'change';
  fetcher?: typeof fetch;
}) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [axis, setAxis] = useState<SolverFrameAxis | null>(null);
  const [confirming, setConfirming] = useState(false);
  const snapshotKey = 'operationId' in snapshot ? `operation:${snapshot.operationId}` : `ingest:${snapshot.ingestId}`;

  useEffect(() => {
    let current = true;
    setLoaded(null);
    setLoadError(null);
    setConfirmError(null);
    setAxis(null);
    void (async () => {
      const preview = await getSolverFrame(snapshot, fetcher);
      if (preview.linked) throw new Error('this model is linked to a WG design and needs no frame');
      const mesh = await loadModel(preview.ingestId, fetcher);
      if (!current) return;
      setLoaded({ preview, mesh });
      // Only a confirmed axis the snapshot still allows is shown as chosen.
      const confirmed = preview.confirmed?.axis ?? null;
      setAxis(confirmed && preview.axes.some((item) => item.axis === confirmed && item.allowed) ? confirmed : null);
    })().catch((reason: unknown) => {
      if (current) setLoadError(reason instanceof Error ? reason.message : String(reason));
    });
    return () => { current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- snapshotKey identifies the snapshot
  }, [fetcher, snapshotKey]);

  const option: SolverFrameAxisOption | null = loaded && axis
    ? loaded.preview.axes.find((item) => item.axis === axis) ?? null
    : null;
  const projection = useMemo(
    () => (loaded && option ? framePreviewProjection(loaded.mesh, option.previewFromRecord) : null),
    [loaded, option],
  );

  if (loadError) {
    return <div className="cad-solver-frame" role="status">Cannot preview the solver frame of {label}: {loadError}</div>;
  }
  if (!loaded) {
    return <div className="cad-solver-frame" data-frame-preview="loading" role="status">Loading the solver frame preview…</div>;
  }
  const confirmed = loaded.preview.confirmed?.axis ?? null;
  const confirm = () => {
    if (!axis) return;
    setConfirming(true);
    setConfirmError(null);
    void confirmSolverFrame(snapshot, axis, fetcher)
      .then((answer) => {
        if (!answer.linked) setLoaded({ ...loaded, preview: answer });
        onConfirmed(axis);
      })
      .catch((reason: unknown) => setConfirmError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setConfirming(false));
  };
  const unchanged = mode === 'change' && axis !== null && axis === confirmed;
  return <div className="cad-solver-frame" data-frame-preview="ready">
    {mode === 'confirm'
      ? <span>
        {label} was authored in CAD, so WG cannot know which way it radiates. Choose the model axis
        that points out of the mouth. WG solves along the solver +Z (blue) from the model’s origin, and
        remembers your choice for this project.
      </span>
      : confirmed === null
        // Nothing to change yet: choosing one is a first choice, not a change.
        ? <span>
          Choose the model axis that points out of the mouth. WG remembers it for this project and
          prepares the model in that frame from now on.
        </span>
        : <span>
          Confirmed for this project: <b>{confirmed}</b>. Change it if the model was
          reoriented in CAD. A change applies to later preparations of this project only: runs already
          solved keep the frame they were solved in. Prepare the model again to solve it in the new frame.
        </span>}
    <fieldset>
      <legend>Radiates along</legend>
      {loaded.preview.axes.map((item) => <label key={item.axis} title={item.reason ?? undefined}>
        <input
          type="radio"
          name={`solver-frame-${snapshotKey}`}
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
    {axis && projection
      ? <>
        <div className="cad-frame-views" data-frame-preview-axis={axis}>
          <FrameView projection={projection} index={0} label={`Side: model ${axis} → solver +Z, solver Y up`}/>
          <FrameView projection={projection} index={1} label={`Top: model ${axis} → solver +Z, solver X up`}/>
        </div>
        <span>Solver frame: model {axis} → solver +Z. Drive sources are shown in orange.</span>
      </>
      : <span>Choose the axis the model radiates along to preview it in the solver frame.</span>}
    {confirmError && <span className="cad-solver-frame-error" role="alert">Could not confirm the solver frame: {confirmError}</span>}
    <button
      className="primary"
      data-action="confirm-frame"
      disabled={confirming || axis === null || unchanged}
      aria-label={axis
        ? `${mode === 'confirm' ? 'Confirm solver frame' : 'Use solver frame'} ${axis}: ${label}`
        : `Choose a solver frame axis: ${label}`}
      onClick={confirm}
    >{axis === null
        // An instruction, not a dead button: the choice is the radios above.
        ? 'Pick an axis above'
        : mode === 'confirm' ? `Confirm ${axis} and solve` : `Use ${axis} for this project`}</button>
  </div>;
}
