import { decodeFrame, type DecodedFrame } from './frame';
import {
  registerRevisionTimer,
  serializeDesign,
  subscribeRevision,
  useDesignStore,
  type DesignDocument,
  type RevisionEvent,
} from '../stores/design';

export type ConnectionState = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'disconnected';

export interface PreviewSnapshot {
  connection: ConnectionState;
  epoch: number | null;
  frame: DecodedFrame | null;
  displayedRevision: number | null;
  lastValidRevision: number | null;
  stale: boolean;
  dropped: number;
  error: string | null;
  /** Validated server field paths for the current preview error. */
  errorFields: Readonly<Record<string, string>> | null;
  /** Design revision the current error belongs to, so the UI can say so. */
  errorRevision: number | null;
}

interface SocketEvent {
  data?: unknown;
}

export interface WebSocketLike {
  binaryType: string;
  readyState: number;
  onopen: ((event: SocketEvent) => void) | null;
  onmessage: ((event: SocketEvent) => void) | null;
  onerror: ((event: SocketEvent) => void) | null;
  onclose: ((event: SocketEvent) => void) | null;
  send(data: string): void;
  close(): void;
}

export type WebSocketFactory = (url: string) => WebSocketLike;

interface HelloMessage {
  v: 1;
  kind: 'hello';
  epoch: number;
  heartbeatSec: number;
  limits?: { maxFrameBytes?: number };
}

interface DroppedMessage { v: 1; kind: 'dropped'; epoch?: number; seq: number }
interface ErrorMessage {
  v: 1;
  kind: 'error';
  epoch?: number;
  seq?: number;
  designRevision?: number;
  code: string;
  message?: string;
  fields?: Record<string, string>;
}

/** Keep only actionable wire entries; malformed values remain a global error. */
function validatedErrorFields(value: unknown): Record<string, string> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const entries = Object.entries(value).filter(([key, message]) => key.trim() && typeof message === 'string' && message.trim());
  return entries.length ? Object.fromEntries(entries) : null;
}

const OPEN = 1;
export const PREVIEW_COARSE_INTERVAL_MS = 33;
export const PREVIEW_FINE_IDLE_MS = 140;
const defaultFactory: WebSocketFactory = (url) => new WebSocket(url) as unknown as WebSocketLike;

function previewUrl(): string {
  if (typeof window === 'undefined') return 'ws://localhost/ws/preview';
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/ws/preview`;
}

export class PreviewSocketManager {
  private socket: WebSocketLike | null = null;
  private seq = 0;
  /** Newest request outcome seen on this connection, across coarse/fine lanes. */
  private latestOutcomeSeq = 0;
  private helloSeen = false;
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private coarseTimer: ReturnType<typeof setTimeout> | null = null;
  private fineTimer: ReturnType<typeof setTimeout> | null = null;
  private coarsePending = false;
  /** Whether the viewport is on the curvature heatmap, the only mode that reads curvature sections. */
  private curvatureWanted = false;
  private heartbeatTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatMs = 30_000;
  private maxFrameBytes: number | undefined;
  /** Revision of the last discontinuous edit; older frames answer a design that no longer exists. */
  private barrierRevision = 0;
  private stopped = true;
  private readonly listeners = new Set<() => void>();
  private unsubscribeRevision: (() => void) | null = null;
  private unregisterTimer: (() => void) | null = null;
  private snapshot: PreviewSnapshot = {
    connection: 'idle', epoch: null, frame: null, displayedRevision: null,
    lastValidRevision: null, stale: true, dropped: 0, error: null, errorFields: null, errorRevision: null,
  };

  constructor(
    private readonly factory: WebSocketFactory = defaultFactory,
    private readonly url: string = previewUrl(),
  ) {}

  getSnapshot = (): PreviewSnapshot => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.unsubscribeRevision = subscribeRevision((event) => this.onRevision(event));
    this.unregisterTimer = registerRevisionTimer(() => this.cancelPreviewTimers());
    this.connect(false);
  }

  /**
   * Re-request the current design at full detail, right now.
   *
   * A preview that failed leaves nothing scheduled — the design has not changed,
   * so no revision event will ever arrive to trigger another attempt. Without an
   * explicit retry the only ways out are editing something else or reloading the
   * page, which is what made a stale viewport feel like a dead end.
   */
  refresh(): void {
    if (this.stopped) {
      this.start();
      return;
    }
    this.cancelPreviewTimers();
    if (this.socket === null || this.socket.readyState !== OPEN) {
      // A dropped socket reconnects on its own; asking again cannot help until
      // it does, and the reconnect sends the current design on `hello`.
      return;
    }
    this.sendCurrent('fine');
  }

  /**
   * Tell the server whether the frame needs analytic curvature sections.
   *
   * Building them costs about a third of a fine frame and an eighth of its
   * bytes, and only the curvature heatmap reads them, so they are requested on
   * demand rather than carried by every fine frame. Turning the mode on
   * re-requests the current design at once; the viewport shows the geometry
   * unshaded in the meantime, which is what its "inspection data is requested"
   * placeholder already describes. Turning it off changes nothing on screen,
   * so it costs no request — the next fine frame simply stops carrying them.
   */
  setCurvatureWanted(wanted: boolean): void {
    if (this.curvatureWanted === wanted) return;
    this.curvatureWanted = wanted;
    if (wanted) this.refresh();
  }

  stop(): void {
    this.stopped = true;
    this.cancelPreviewTimers();
    this.clearTimer('reconnect');
    this.clearTimer('heartbeat');
    this.unsubscribeRevision?.();
    this.unregisterTimer?.();
    this.unsubscribeRevision = null;
    this.unregisterTimer = null;
    const socket = this.socket;
    this.socket = null;
    socket?.close();
    this.update({ connection: 'disconnected' });
  }

  private connect(reconnecting: boolean): void {
    if (this.stopped) return;
    this.update({ connection: reconnecting ? 'reconnecting' : 'connecting', error: null, errorFields: null, errorRevision: null });
    const socket = this.factory(this.url);
    this.socket = socket;
    this.seq = 0;
    this.latestOutcomeSeq = 0;
    this.helloSeen = false;
    socket.binaryType = 'arraybuffer';
    socket.onopen = () => undefined;
    socket.onmessage = (event) => this.onMessage(socket, event.data);
    socket.onerror = () => this.update({ error: 'Preview connection error', errorFields: null, errorRevision: null });
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.clearTimer('heartbeat');
      if (!this.stopped) this.scheduleReconnect();
    };
  }

  private onMessage(socket: WebSocketLike, data: unknown): void {
    if (this.socket !== socket) return;
    this.armHeartbeat();
    if (data instanceof ArrayBuffer) {
      this.onFrame(data);
      return;
    }
    if (typeof data !== 'string') return;
    let message: HelloMessage | DroppedMessage | ErrorMessage;
    try {
      message = JSON.parse(data) as HelloMessage | DroppedMessage | ErrorMessage;
    } catch {
      this.update({ error: 'Malformed preview control message', errorFields: null, errorRevision: null });
      return;
    }
    if ((message as { v?: unknown }).v !== 1) {
      this.update({ error: 'Unsupported preview protocol message', errorFields: null, errorRevision: null });
      return;
    }
    if (message.kind === 'hello') {
      if (this.helloSeen) return;
      this.helloSeen = true;
      this.reconnectAttempt = 0;
      this.heartbeatMs = Math.max(250, message.heartbeatSec * 2_000);
      this.maxFrameBytes = message.limits?.maxFrameBytes;
      this.update({ connection: 'connected', epoch: message.epoch, error: null, errorFields: null, errorRevision: null });
      this.armHeartbeat();
      this.sendProgressive();
      return;
    }
    if ('epoch' in message && message.epoch !== undefined && message.epoch !== this.snapshot.epoch) return;
    if (message.kind === 'dropped') {
      this.update({ dropped: this.snapshot.dropped + 1 });
      return;
    }
    if (message.kind === 'error') {
      // Record every failure, not only the ones whose revision still matches.
      // Editing again while a preview is failing used to swallow the reason and
      // leave the viewport reading STALE with nothing to explain it.
      const fields = validatedErrorFields(message.fields);
      const errorRevision = Number.isInteger(message.designRevision) ? message.designRevision! : null;
      const errorSeq = Number.isInteger(message.seq) ? message.seq! : null;
      // A successful newer frame, or a newer failure already on screen, has
      // superseded this response. The coarse and fine lanes can finish out of
      // order, so accepting it would resurrect a field error the user fixed.
      if (errorRevision !== null && errorRevision < (this.snapshot.lastValidRevision ?? 0)) return;
      if (errorRevision !== null && errorRevision < (this.snapshot.errorRevision ?? 0)) return;
      if (errorSeq !== null && errorSeq < this.latestOutcomeSeq) return;
      const detail = (typeof message.message === 'string' && message.message.trim())
        || Object.values(fields ?? {})[0]
        || (typeof message.code === 'string' && message.code.trim())
        || 'Preview request failed';
      if (errorSeq !== null) this.latestOutcomeSeq = Math.max(this.latestOutcomeSeq, errorSeq);
      this.update({ error: detail, errorFields: fields, errorRevision, stale: true });
    }
  }

  /**
   * Accept geometry that is behind the live design, but never behind an undo.
   *
   * The original rule rendered a frame only when its `designRevision` still
   * equalled the store's. That is unreachable during any continuous gesture:
   * the design store commits a revision per `pointermove` — about 104 a second
   * measured in a real browser — while building even a coarse preview takes
   * 96 ms here and 192 ms on the Windows reference machine. The revision has
   * therefore always moved on by the time geometry comes back, so *every*
   * frame was discarded and the viewport only ever updated in the gaps between
   * gestures. Reproduced end to end against the real mesher: a 2.2 s drag
   * produced 21 valid coarse frames and the client accepted 0 of them.
   *
   * A frame that lags the design by a few revisions is exactly what the stale
   * badge exists to describe, and showing it is what makes a drag look live.
   * The one case where a late frame is not merely old but *wrong* is a
   * discontinuous jump — undo, redo, load, family switch — where the design in
   * flight is not an earlier point on the same gesture. Those all announce
   * themselves with `immediate`, so their revision becomes a barrier and
   * anything older is still refused, which is the property WS-PROTOCOL §4
   * case 2 was written for.
   */
  private onFrame(buffer: ArrayBuffer): void {
    let frame: DecodedFrame;
    try {
      frame = decodeFrame(buffer, this.maxFrameBytes);
    } catch (error) {
      this.update({ error: error instanceof Error ? error.message : String(error), errorFields: null, errorRevision: null });
      return;
    }
    const { header } = frame;
    const revision = useDesignStore.getState().designRevision;
    const frameRevision = header.designRevision ?? -1;
    const frameSeq = header.seq ?? 0;
    if (
      header.v !== 1
      || header.kind !== 'preview'
      || header.epoch !== this.snapshot.epoch
      || frameRevision < this.barrierRevision
      || frameRevision < (this.snapshot.displayedRevision ?? 0)
    ) {
      this.update({ stale: true });
      return;
    }
    // An error belongs to the revision that produced it. A frame older than
    // that revision does not answer it, so it must not clear the message.
    const clearsError = frameSeq >= this.latestOutcomeSeq && (
      this.snapshot.errorRevision === null
      || frameRevision >= this.snapshot.errorRevision
    );
    this.latestOutcomeSeq = Math.max(this.latestOutcomeSeq, frameSeq);
    this.update({
      frame,
      displayedRevision: frameRevision,
      lastValidRevision: frameRevision,
      stale: frameRevision !== revision,
      ...(clearsError ? { error: null, errorFields: null, errorRevision: null } : {}),
    });
  }

  private onRevision(event: RevisionEvent): void {
    if (event.immediate) this.barrierRevision = event.revision;
    // Loading a document is a discontinuity, not a late frame. The rendered
    // revision is a floor that stops an older in-flight answer from replacing a
    // newer one within one editing stream, and `New design` rewinds the counter
    // to 1 -- so without clearing the floor here, every frame for the new
    // document is older than the one on screen and gets dropped. The viewport
    // then keeps showing the previous design, permanently stale, until the user
    // makes as many edits as the old document had revisions.
    if (event.reason === 'load' && event.revision < (this.snapshot.displayedRevision ?? 0)) {
      this.update({ displayedRevision: null });
    }
    this.update({ stale: event.revision !== this.snapshot.displayedRevision });
    if (event.immediate) {
      this.cancelPreviewTimers();
      this.sendProgressive();
      return;
    }
    if (!this.coarseTimer) {
      this.sendCurrent('coarse');
      this.armCoarseWindow();
    } else {
      this.coarsePending = true;
    }
    this.scheduleFine();
  }

  private sendProgressive(): void {
    this.sendCurrent('coarse');
    this.scheduleFine();
  }

  private armCoarseWindow(): void {
    this.coarseTimer = setTimeout(() => {
      this.coarseTimer = null;
      if (!this.coarsePending) return;
      this.coarsePending = false;
      this.sendCurrent('coarse');
      this.armCoarseWindow();
    }, PREVIEW_COARSE_INTERVAL_MS);
  }

  private scheduleFine(): void {
    if (this.fineTimer) clearTimeout(this.fineTimer);
    this.fineTimer = setTimeout(() => {
      this.fineTimer = null;
      this.sendCurrent('fine');
    }, PREVIEW_FINE_IDLE_MS);
  }

  private sendCurrent(lod: 'coarse' | 'fine'): void {
    const socket = this.socket;
    if (!socket || socket.readyState !== OPEN || !this.helloSeen || this.snapshot.epoch === null) return;
    const { design, designRevision } = useDesignStore.getState();
    this.seq += 1;
    socket.send(JSON.stringify({
      v: 1,
      kind: 'preview',
      epoch: this.snapshot.epoch,
      seq: this.seq,
      designRevision,
      design: this.toApiDesign(design),
      lod,
      curvature: this.curvatureWanted,
    }));
  }

  private toApiDesign(design: DesignDocument): Record<string, unknown> {
    return serializeDesign(design);
  }

  private scheduleReconnect(): void {
    this.update({ connection: 'reconnecting', epoch: null, stale: true });
    const delay = Math.min(5_000, 250 * 2 ** this.reconnectAttempt);
    this.reconnectAttempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect(true);
    }, delay);
  }

  private armHeartbeat(): void {
    this.clearTimer('heartbeat');
    this.heartbeatTimer = setTimeout(() => this.socket?.close(), this.heartbeatMs);
  }

  private cancelPreviewTimers(): void {
    if (this.coarseTimer) clearTimeout(this.coarseTimer);
    if (this.fineTimer) clearTimeout(this.fineTimer);
    this.coarseTimer = null;
    this.fineTimer = null;
    this.coarsePending = false;
  }

  private clearTimer(kind: 'heartbeat' | 'reconnect'): void {
    const timer = kind === 'heartbeat' ? this.heartbeatTimer : this.reconnectTimer;
    if (timer) clearTimeout(timer);
    if (kind === 'heartbeat') this.heartbeatTimer = null;
    else this.reconnectTimer = null;
  }

  /**
   * Publish a new snapshot, but only when the snapshot actually changed.
   *
   * Every subscriber reads this through `useSyncExternalStore`, which compares
   * by identity, so a fresh object is a re-render of the whole viewport subtree
   * -- Viewport, the Canvas, the Scene and every SurfaceMesh in it. `onRevision`
   * runs on every committed design mutation, which during a drag is one per
   * pointermove, and all it does is set `stale`. `stale` goes true on the first
   * revision of a gesture and stays true, so all but the first of those were
   * new objects carrying identical values: dozens of full reconciliations a
   * second, on the machine least able to afford them.
   *
   * Frames are exempt from the comparison on purpose -- a decoded frame is a
   * new object every time by definition, and `Object.is` on it is both correct
   * and cheap.
   */
  private update(patch: Partial<PreviewSnapshot>): void {
    const current = this.snapshot;
    let changed = false;
    for (const key of Object.keys(patch) as Array<keyof PreviewSnapshot>) {
      if (!Object.is(current[key], patch[key])) {
        changed = true;
        break;
      }
    }
    if (!changed) return;
    this.snapshot = { ...current, ...patch };
    this.listeners.forEach((listener) => listener());
  }
}

export const previewSocket = new PreviewSocketManager();
