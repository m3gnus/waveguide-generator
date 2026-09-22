import { useEffect, useMemo, useRef, useState } from 'react';
import { confirmSolverFrame, type SolverFrameState } from '../api/solverFrame';
import { useCadOperationsStore } from '../stores/cadOperations';
import { useCadSolverFrameStore } from '../stores/cadSolverFrame';
import { parseMSH, type ParsedMSH } from '../viewport/mshParser';
import {
  framePreviewProjection,
  type FramePreviewProjection,
  type SolverFrameAxis,
} from '../viewport/solverFrame';

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

/** The model's mesh for the frame preview, once per preparation. */
function useFrameMesh(ingestId: string, fetcher: typeof fetch): { mesh: ParsedMSH | null; error: string | null } {
  const [state, setState] = useState<{ ingestId: string; mesh: ParsedMSH | null; error: string | null } | null>(null);
  useEffect(() => {
    let current = true;
    void loadModel(ingestId, fetcher)
      .then((mesh) => { if (current) setState({ ingestId, mesh, error: null }); })
      .catch((reason: unknown) => {
        if (current) setState({ ingestId, mesh: null, error: reason instanceof Error ? reason.message : String(reason) });
      });
    return () => { current = false; };
  }, [fetcher, ingestId]);
  return state?.ingestId === ingestId ? state : { mesh: null, error: null };
}

/** The model in the solver frame an axis names: the exact matrix the backend
 * meshes with (`previewFromRecord`), with the solver +Z arrow. */
function FramePreview({ frame, axis, mesh, views }: {
  frame: SolverFrameState;
  axis: SolverFrameAxis;
  mesh: ParsedMSH | null;
  views: 'side' | 'both';
}) {
  const option = frame.axes.find((item) => item.axis === axis) ?? null;
  const projection = useMemo(
    () => (mesh && option ? framePreviewProjection(mesh, option.previewFromRecord) : null),
    [mesh, option],
  );
  if (!projection) return null;
  return <div className="cad-frame-views" data-frame-preview-axis={axis}>
    <FrameView projection={projection} index={0} label={`Side: model ${axis} → solver +Z, solver Y up`}/>
    {views === 'both' && <FrameView projection={projection} index={1} label={`Top: model ${axis} → solver +Z, solver X up`}/>}
  </div>;
}

const SOURCE_WORDS: Record<string, string> = {
  confirmed: 'this project’s frame',
  carried: 'this project’s frame',
  suggested: 'worked out from the model',
};

/**
 * Which way a model authored in CAD radiates, on its Solve card (PLAN.md
 * M1b/M1e): "Radiates along +x · Change" when WG has an answer -- the
 * project's frame, or the one it worked out from the model -- and the
 * question, in CAD axes with its reason in words, only when it has none.
 *
 * Nothing here confirms a frame except the one-click switch a "differs"
 * notice offers: Solve confirms the axis shown (`confirmDisplayedFrame`). The
 * preview applies the server's matrices; nothing here computes a frame.
 */
export function CadSolverFrame({ ingestId, manifestSha256, label, fetcher = fetch }: {
  ingestId: string;
  /** The snapshot shown: a solve of it that stops at the frame gate means the
   * frame may have changed, so the card reads it again. */
  manifestSha256?: string;
  label: string;
  fetcher?: typeof fetch;
}) {
  const view = useCadSolverFrameStore((state) => state.frames[ingestId]);
  const load = useCadSolverFrameStore((state) => state.load);
  const pick = useCadSolverFrameStore((state) => state.pick);
  const apply = useCadSolverFrameStore((state) => state.apply);
  const [changing, setChanging] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);
  // Each time a solve of this snapshot stops at the frame gate -- a frame
  // changed elsewhere, say -- the card reads the frame again and shows it.
  const frameGate = useCadOperationsStore((state) => Object.values(state.operations)
    .filter((operation) => operation.kind === 'prepare_and_solve'
      && operation.state === 'needs_user_input'
      && operation.reason === 'frame_confirmation_required'
      && manifestSha256 !== undefined
      && operation.snapshot?.manifestSha256 === manifestSha256)
    .map((operation) => `${operation.operationId}:${operation.attemptGeneration}:${operation.updatedAt ?? ''}`)
    .sort()
    .join('|'));
  useEffect(() => { void load(ingestId, fetcher); }, [fetcher, frameGate, ingestId, load]);
  useEffect(() => { setChanging(false); setSwitchError(null); }, [ingestId]);
  const frame = view?.frame ?? null;
  const { mesh } = useFrameMesh(ingestId, fetcher);

  if (!view || (view.status === 'loading' && !frame)) {
    return <p className="cad-solver-frame cad-detail" data-frame-preview="loading" role="status">Reading which way {label} radiates…</p>;
  }
  if (view.linked) return null;
  if (!frame) {
    return <p className="cad-solver-frame cad-detail" role="status">
      WG could not read which way {label} radiates ({view.error}). Solve still stops to ask before it solves.
    </p>;
  }
  const axis = view.axis;
  const suggestion = frame.suggestion ?? null;
  const source = frame.preselected?.source ?? (frame.confirmed ? 'confirmed' : null);
  const asking = axis === null || changing;
  const differs = frame.differs ?? null;
  const switchTo = (target: SolverFrameAxis) => {
    setSwitching(true);
    setSwitchError(null);
    void confirmSolverFrame({ ingestId }, target, fetcher)
      .then((answer) => apply(ingestId, answer))
      .catch((reason: unknown) => setSwitchError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setSwitching(false));
  };
  const unsupported = frame.axes.find((item) => !item.allowed && item.reason)?.reason ?? null;
  return <div className="cad-solver-frame" data-frame-preview="ready" data-solver-frame={axis ?? 'unset'}>
    {!asking && <div className="cad-solver-frame-summary">
      <p className="cad-solver-frame-line">
        <span>Radiates along <b>{axis}</b></span>
        {' · '}<button
          className="link-button"
          data-action="change-solver-frame"
          title="Choose another axis. Solve confirms the one shown; runs already solved keep their frame."
          onClick={() => setChanging(true)}
        >Change</button>
      </p>
      {(view.picked || source) && <small className="cad-detail cad-solver-frame-source">{view.picked ? 'your choice' : SOURCE_WORDS[source!]}</small>}
      <FramePreview frame={frame} axis={axis!} mesh={mesh} views="side"/>
    </div>}
    {asking && <>
      <p className="cad-solver-frame-question">Which way does the mouth face? (CAD axes)</p>
      {/* Why WG asks, in words; never its code. */}
      {!changing && suggestion && suggestion.status !== 'automatic' && <p className="cad-detail cad-solver-frame-reason">{suggestion.reason}</p>}
      <fieldset>
        <legend>Radiates along</legend>
        {frame.axes.map((item) => <label key={item.axis} title={item.reason ?? undefined}>
          <input
            type="radio"
            name={`solver-frame-${ingestId}`}
            value={item.axis}
            checked={item.axis === axis}
            disabled={!item.allowed}
            onChange={() => pick(ingestId, item.axis)}
          />
          {item.axis}
        </label>)}
      </fieldset>
      {unsupported && <p className="cad-detail">{unsupported}</p>}
      {axis
        ? <>
          <FramePreview frame={frame} axis={axis} mesh={mesh} views="both"/>
          <p className="cad-detail">Solver frame: model {axis} → solver +Z (blue arrow). Drive sources are shown in orange. Solve confirms it for this project.</p>
        </>
        : <p className="cad-detail">Choose the model axis that points out of the mouth; the preview shows it as the solver +Z.</p>}
      {changing && <button className="link-button" data-action="done-solver-frame" onClick={() => setChanging(false)}>Done</button>}
    </>}
    {view.changedFrom && axis && <p className="cad-alert cad-alert-notice cad-solver-frame-changed" role="status">
      This project’s solver frame was changed elsewhere to {axis}; this card showed {view.changedFrom}. Solve now solves along {axis}.
    </p>}
    {axis && frame.recordAxis !== axis && <p className="cad-detail">
      Prepared along {frame.recordAxis}; Solve prepares it again along {axis}.
    </p>}
    {differs && <div className="cad-alert cad-alert-notice cad-solver-frame-differs" role="status">
      <span>{differs.message}</span>{' '}
      <button
        className="link-button"
        data-action="switch-solver-frame"
        disabled={switching}
        onClick={() => switchTo(differs.suggestedAxis)}
      >Switch to {differs.suggestedAxis}</button>
    </div>}
    {switchError && <p className="cad-solver-frame-error" role="alert">Could not switch the solver frame: {switchError}</p>}
  </div>;
}
