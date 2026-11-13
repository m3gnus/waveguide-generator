import { workspaceModeStore } from '../stores/workspaceMode';
import { viewerPreferences } from '../viewerprefs/viewerPreferences';
import { fieldSentence, validationSentence, failureWords, PLAIN_VALIDATION_FAILURE, readsPlain } from './httpError';
import { decodeFrame, type DecodedFrame, type FrameHeader } from './frame';
import { selectPreferredFrame } from '../viewport/lodPolicy';
import { activityStore, type ActivityHandle } from '../shell/activity';
import {
  currentDocumentLoad,
  registerRevisionTimer,
  serializeDesign,
  subscribeRevision,
  useDesignStore,
  type DesignDocument,
  type RevisionEvent,
} from '../stores/design';

const PLAIN_PREVIEW_FAILURE = 'WG could not update the preview and has recorded the problem.';

export type ConnectionState = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'disconnected';

export interface PreviewSnapshot {
  connection: ConnectionState;
  epoch: number | null;
  frame: DecodedFrame | null;
  /** Last canonical readout in this document, retained through pending frames. */
  lastCanonicalDimensions?: NonNullable<FrameHeader['previewMetadata']>['dimensions_mm'];
  /** Same lane policy as the viewport: a late coarse frame cannot unsettle sizes. */
  dimensionsFrame?: DecodedFrame | null;
  /** A new document completed with only an error, so its sizes are unavailable. */
  dimensionsUnavailable?: boolean;
  /**
   * True from a document load until a frame for the new document is accepted.
   * The retained frame still belongs to the previous document in that window:
   * the viewport keeps showing it, but nothing may present its numbers as this
   * document's.
   */
  awaitingDocumentFrame?: boolean;
  displayedRevision: number | null;
  lastValidRevision: number | null;
  stale: boolean;
  dropped: number;
  error: string | null;
  /** The original failure when `error` is plain words, for the developer disclosure only. */
  errorDetail?: string | null;
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
  private connectionGeneration = 0;
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
  /**
   * The request `seq` a `load` revision event is about to send. A frame whose
   * `seq` predates it answers the document that load replaced.
   *
   * `designRevision` cannot carry this: `load` (New, Open, run recall) resets
   * it, so a low revision on the new document does not mean "older than" a
   * high revision still in flight for the replaced one. `seq` is never reset
   * -- it counts requests for the life of the connection -- so it is the one
   * field that always orders "before this load" correctly regardless of which
   * document either side belongs to.
   */
  private loadFloorSeq = 0;
  private stopped = true;
  private documentLoad = currentDocumentLoad();
  private errorDocumentLoad = this.documentLoad;
  private readonly listeners = new Set<() => void>();
  private unsubscribeRevision: (() => void) | null = null;
  private unregisterTimer: (() => void) | null = null;
  /** The status-bar wait for the current revision, from its first request
   * until its fine frame arrives (shown only once it has run a second). */
  private previewActivity: {
    handle: ActivityHandle; revision: number; documentLoad: number;
    firstSeq: number; coarseSeq: number | null; fineSeq: number | null;
  } | null = null;
  private snapshot: PreviewSnapshot = {
    connection: 'idle', epoch: null, frame: null, displayedRevision: null,
    lastValidRevision: null, stale: true, dropped: 0, error: null, errorDetail: null, errorFields: null, errorRevision: null,
  };

  constructor(
    private readonly factory: WebSocketFactory = defaultFactory,
    private readonly url: string = previewUrl(),
  ) {}

  getSnapshot = (): PreviewSnapshot => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    this.observeRevisions();
    return () => {
      this.listeners.delete(listener);
      if (this.stopped && this.listeners.size === 0) this.stopObservingRevisions();
    };
  };

  private observeRevisions(): void {
    if (!this.unsubscribeRevision) this.unsubscribeRevision = subscribeRevision((event) => this.onRevision(event));
    if (this.documentLoad !== currentDocumentLoad()) {
      if (this.hasDimensions()) {
        this.onRevision({ reason: 'load', revision: useDesignStore.getState().designRevision, immediate: true });
      } else {
        this.documentLoad = currentDocumentLoad();
      }
    }
  }

  private hasDimensions(): boolean {
    // The preferred lane can retain a fine frame after the shared coarse lane
    // has changed. Document cleanup must include every retained readout.
    return this.snapshot.frame?.header.previewMetadata?.dimensions_mm !== undefined
      || this.snapshot.dimensionsFrame?.header.previewMetadata?.dimensions_mm !== undefined
      || this.snapshot.lastCanonicalDimensions != null;
  }

  private stopObservingRevisions(): void {
    this.unsubscribeRevision?.();
    this.unsubscribeRevision = null;
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.observeRevisions();
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
    this.endPreviewActivity(false);
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
    this.endPreviewActivity(false);
    this.cancelPreviewTimers();
    this.clearTimer('reconnect');
    this.clearTimer('heartbeat');
    // Subscribers still need document isolation while live requests are paused.
    if (this.listeners.size === 0) this.stopObservingRevisions();
    this.unregisterTimer?.();
    this.unregisterTimer = null;
    const socket = this.socket;
    this.socket = null;
    socket?.close();
    this.update({ connection: 'disconnected' });
  }

  private connect(reconnecting: boolean): void {
    if (this.stopped) return;
    this.update({ connection: reconnecting ? 'reconnecting' : 'connecting', error: null, errorDetail: null, errorFields: null, errorRevision: null });
    const socket = this.factory(this.url);
    this.socket = socket;
    this.armHeartbeat();
    this.connectionGeneration += 1;
    this.seq = 0;
    this.latestOutcomeSeq = 0;
    this.loadFloorSeq = 0;
    this.helloSeen = false;
    socket.binaryType = 'arraybuffer';
    socket.onopen = () => { if (this.socket === socket) this.armHeartbeat(); };
    socket.onmessage = (event) => this.onMessage(socket, event.data);
    // A dropped socket is not a failed edit: onclose reconnects and the badge says so.
    socket.onerror = () => undefined;
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.endPreviewActivity(false);
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
      this.endPreviewActivity(false);
      this.update({ error: 'Malformed preview control message', errorFields: null, errorRevision: null });
      return;
    }
    if ((message as { v?: unknown }).v !== 1) {
      this.endPreviewActivity(false);
      this.update({ error: 'Unsupported preview protocol message', errorFields: null, errorRevision: null });
      return;
    }
    if (message.kind === 'hello') {
      if (this.helloSeen) return;
      this.helloSeen = true;
      this.reconnectAttempt = 0;
      this.heartbeatMs = Math.max(250, message.heartbeatSec * 2_000);
      this.maxFrameBytes = message.limits?.maxFrameBytes;
      this.update({ connection: 'connected', epoch: message.epoch, error: null, errorDetail: null, errorFields: null, errorRevision: null });
      this.armHeartbeat();
      this.sendProgressive();
      return;
    }
    if ('epoch' in message && message.epoch !== undefined && message.epoch !== this.snapshot.epoch) return;
    if (message.kind === 'dropped') {
      // A dropped fine request ends the wait; a newer one will start its own.
      if (this.activityFor(null, message.seq ?? null)?.fineSeq === message.seq) this.endPreviewActivity(false);
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
      const supported = this.hasDimensions();
      if (supported && errorSeq !== null && errorSeq < this.loadFloorSeq) return;
      // A successful newer frame, or a newer failure already on screen, has
      // superseded this response. The coarse and fine lanes can finish out of
      // order, so accepting it would resurrect a field error the user fixed.
      if ((!supported || this.snapshot.frame?.documentLoad === this.documentLoad) && errorRevision !== null && errorRevision < (this.snapshot.lastValidRevision ?? 0)) return;
      if ((!supported || this.errorDocumentLoad === this.documentLoad) && errorRevision !== null && errorRevision < (this.snapshot.errorRevision ?? 0)) return;
      if (errorSeq !== null && errorSeq < this.latestOutcomeSeq) return;
      this.previewFailed(errorRevision, errorSeq);
      const firstField = Object.entries(fields ?? {})[0];
      const original = (typeof message.message === 'string' && message.message.trim())
        || Object.values(fields ?? {})[0]
        || (typeof message.code === 'string' && message.code.trim())
        || 'Preview request failed';
      // An internal failure's text is for developers; a field's own sentence is for the user.
      const worded = firstField && original === firstField[1] ? fieldSentence(firstField[0].split('.'), original) : validationSentence(original);
      const detail = message.code === 'internal' ? PLAIN_PREVIEW_FAILURE
        : worded && readsPlain(worded) ? worded : PLAIN_VALIDATION_FAILURE;
      if (errorSeq !== null) this.latestOutcomeSeq = Math.max(this.latestOutcomeSeq, errorSeq);
      this.errorDocumentLoad = this.documentLoad;
      const documentFailed = supported && this.snapshot.awaitingDocumentFrame
        && errorSeq !== null && errorSeq >= this.loadFloorSeq
        && errorRevision === useDesignStore.getState().designRevision;
      this.update({ error: detail, errorDetail: detail === original ? null : original, errorFields: fields, errorRevision, stale: true,
        ...(documentFailed ? { awaitingDocumentFrame: false, dimensionsUnavailable: true } : {}) });
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
      this.endPreviewActivity(false);
      this.update({ error: failureWords(error), errorFields: null, errorRevision: null });
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
      || frameSeq < this.loadFloorSeq
      || frameRevision < this.barrierRevision
      || frameRevision < (this.snapshot.displayedRevision ?? 0)
    ) {
      this.update({ stale: this.snapshot.displayedRevision !== revision });
      return;
    }
    // An error belongs to the revision that produced it. A frame older than
    // that revision does not answer it, so it must not clear the message.
    const supported = header.previewMetadata?.dimensions_mm !== undefined || this.hasDimensions();
    const clearsError = frameSeq >= this.latestOutcomeSeq && (
      (supported && (this.errorDocumentLoad !== this.documentLoad || this.snapshot.awaitingDocumentFrame))
      || this.snapshot.errorRevision === null
      || frameRevision >= this.snapshot.errorRevision
    );
    this.latestOutcomeSeq = Math.max(this.latestOutcomeSeq, frameSeq);
    frame.connectionGeneration = this.connectionGeneration;
    if (supported) frame.documentLoad = this.documentLoad;
    const retained = this.snapshot.dimensionsFrame ?? this.snapshot.frame;
    const sameOwner = retained?.documentLoad === frame.documentLoad
      && retained?.connectionGeneration === frame.connectionGeneration;
    // seq and server epoch are process-local. Only this client's connection
    // generation and document identity can own a retained dimension readout.
    const dimensionsFrame = selectPreferredFrame(sameOwner ? retained : null, frame);
    const metadata = dimensionsFrame?.header.previewMetadata;
    const lastCanonicalDimensions = metadata?.dimensions_status === 'pending'
      ? sameOwner ? this.snapshot.lastCanonicalDimensions : undefined
      : metadata?.dimensions_status === 'unavailable' ? null : metadata?.dimensions_mm;
    this.update({
      frame,
      ...(supported ? { dimensionsFrame, dimensionsUnavailable: false,
        lastCanonicalDimensions, awaitingDocumentFrame: false } : {}),
      displayedRevision: frameRevision,
      lastValidRevision: frameRevision,
      stale: frameRevision !== revision,
      ...(clearsError ? { error: null, errorDetail: null, errorFields: null, errorRevision: null } : {}),
    });
    const pending = this.activityFor(frameRevision, frameSeq);
    if (pending?.fineSeq === frameSeq) this.endPreviewActivity(true);
    else if (pending?.coarseSeq === frameSeq) pending.handle.label('Refining the preview');
  }

  private onRevision(event: RevisionEvent): void {
    // Each revision is its own wait: a drag never accumulates one long one.
    this.endPreviewActivity(false);
    // Paused observation exists only to hide dimensions from replaced documents.
    // Older meshers retain the shared state and notifications of the base path.
    const supported = this.hasDimensions();
    if (this.stopped && !supported) {
      if (event.reason === 'load') this.documentLoad = currentDocumentLoad();
      return;
    }
    if (event.immediate) this.barrierRevision = event.revision;
    if (event.reason === 'load') {
      // A load starts a new document identity, and `New design` rewinds its
      // revision counter to 1. A frame already in flight for the *replaced*
      // document can carry a revision far above 1 -- accepting it on revision
      // alone would both show the wrong design and, worse, leave that high
      // number as the floor every subsequent frame of the new document has to
      // clear before anything renders again. `seq` never resets, so recording
      // the seq this load's own first request will carry, and refusing
      // anything older in `onFrame`, tells the two documents' frames apart
      // regardless of what either side's revision says.
      this.loadFloorSeq = this.seq + 1;
      this.documentLoad = currentDocumentLoad();
    }
    // Loading a document is a discontinuity, not a late frame. The rendered
    // revision is a floor that stops an older in-flight answer from replacing a
    // newer one within one editing stream, and `New design` rewinds the counter
    // to 1 -- so without clearing the floor here, every frame for the new
    // document is older than the one on screen and gets dropped. The viewport
    // then keeps showing the previous design, permanently stale, until the user
    // makes as many edits as the old document had revisions.
    // New can also replace a document at the SAME revision (1 -> 1).
    // Its retained frame belongs to the previous document until the load's
    // own request returns, even though the numeric revisions compare equal.
    // The viewport keeps that frame; the previous document's readouts are
    // dropped and hidden until a frame for the new document is accepted.
    if (event.reason === 'load') {
      if (supported) {
        this.update({
          lastCanonicalDimensions: null, awaitingDocumentFrame: true,
          dimensionsFrame: null, dimensionsUnavailable: false,
          displayedRevision: null, lastValidRevision: null,
          error: null, errorDetail: null, errorFields: null, errorRevision: null,
        });
      } else if (event.revision < (this.snapshot.displayedRevision ?? 0)) {
        // Absent keys preserve shared error/badge fields and notifications.
        this.update({ displayedRevision: null });
      }
    }
    this.update({ stale: event.revision !== this.snapshot.displayedRevision });
    if (this.stopped) return;
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
    if (this.previewActivity?.revision !== designRevision || this.previewActivity.documentLoad !== this.documentLoad) {
      this.endPreviewActivity(false);
      this.previewActivity = {
        handle: activityStore.start({ kind: 'preview', label: lod === 'fine' ? 'Refining the preview' : 'Drawing the preview', background: true }),
        revision: designRevision, documentLoad: this.documentLoad, firstSeq: this.seq, coarseSeq: null, fineSeq: null,
      };
    }
    if (lod === 'coarse') this.previewActivity.coarseSeq = this.seq;
    else this.previewActivity.fineSeq = this.seq;
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

  /** Activity ownership follows the same document, revision and request
   * boundaries as accepted frames. Old replies may still paint during a drag,
   * but cannot finish the newer revision's wait or record its duration. */
  private activityFor(revision: number | null, seq: number | null) {
    const pending = this.previewActivity;
    if (!pending || pending.documentLoad !== this.documentLoad
      || pending.revision !== useDesignStore.getState().designRevision
      || (revision !== null && revision !== pending.revision)
      || (seq !== null && (seq < pending.firstSeq || seq > this.seq))) return null;
    return pending;
  }

  /** A failed coarse request leaves the wait running while its fine request is
   * still scheduled or in flight: that answer can still draw the design. */
  private previewFailed(revision: number | null, seq: number | null): void {
    const pending = this.activityFor(revision, seq);
    if (!pending) return;
    const fineOutstanding = pending.fineSeq === null ? this.fineTimer !== null : seq !== null && pending.fineSeq > seq;
    if (seq !== null && seq === pending.coarseSeq && fineOutstanding) return;
    this.endPreviewActivity(false);
  }

  private endPreviewActivity(completed: boolean): void {
    this.previewActivity?.handle.end(completed);
    this.previewActivity = null;
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

/** Defer hidden parametric geometry until the design is viewed or edited. */
export function observeLivePreview(control: Pick<PreviewSocketManager, 'start' | 'stop'> = previewSocket): () => void {
  let active = true;
  let editedInCad = false;
  const apply = () => queueMicrotask(() => {
    if (!active) return;
    if (viewerPreferences.getSnapshot().liveUpdate
      && (workspaceModeStore.getSnapshot().mode === 'parametric' || editedInCad)) control.start();
    else control.stop();
  });
  apply();
  const stops = [viewerPreferences.subscribe(apply), workspaceModeStore.subscribe(apply), subscribeRevision(event => {
    // Linked CAD projects still edit WG's waveguide fields. Their first edit
    // needs the normal server field validation, but restoring a document does not.
    if (event.reason === 'load') editedInCad = false;
    else if (workspaceModeStore.getSnapshot().mode === 'cad') editedInCad = true;
    apply();
  })];
  return () => {
    active = false;
    stops.forEach(stop => stop());
    control.stop();
  };
}
