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

interface DroppedMessage { kind: 'dropped'; epoch?: number; seq: number }
interface ErrorMessage {
  kind: 'error';
  epoch?: number;
  designRevision: number;
  code: string;
  message?: string;
  fields?: Record<string, string>;
}

const OPEN = 1;
const defaultFactory: WebSocketFactory = (url) => new WebSocket(url) as unknown as WebSocketLike;

function previewUrl(): string {
  if (typeof window === 'undefined') return 'ws://localhost/ws/preview';
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/ws/preview`;
}

export class PreviewSocketManager {
  private socket: WebSocketLike | null = null;
  private seq = 0;
  private helloSeen = false;
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private debounceTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatMs = 30_000;
  private maxFrameBytes: number | undefined;
  private queuedRevision: number | null = null;
  private stopped = true;
  private readonly listeners = new Set<() => void>();
  private unsubscribeRevision: (() => void) | null = null;
  private unregisterTimer: (() => void) | null = null;
  private snapshot: PreviewSnapshot = {
    connection: 'idle', epoch: null, frame: null, displayedRevision: null,
    lastValidRevision: null, stale: true, dropped: 0, error: null,
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
    this.unregisterTimer = registerRevisionTimer(() => this.cancelDebounce());
    this.connect(false);
  }

  stop(): void {
    this.stopped = true;
    this.cancelDebounce();
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
    this.update({ connection: reconnecting ? 'reconnecting' : 'connecting', error: null });
    const socket = this.factory(this.url);
    this.socket = socket;
    this.seq = 0;
    this.helloSeen = false;
    socket.binaryType = 'arraybuffer';
    socket.onopen = () => undefined;
    socket.onmessage = (event) => this.onMessage(socket, event.data);
    socket.onerror = () => this.update({ error: 'Preview connection error' });
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
      this.update({ error: 'Malformed preview control message' });
      return;
    }
    if (message.kind === 'hello') {
      if (this.helloSeen) return;
      this.helloSeen = true;
      this.reconnectAttempt = 0;
      this.heartbeatMs = Math.max(250, message.heartbeatSec * 2_000);
      this.maxFrameBytes = message.limits?.maxFrameBytes;
      this.update({ connection: 'connected', epoch: message.epoch, error: null });
      this.armHeartbeat();
      this.sendCurrent('fine');
      return;
    }
    if ('epoch' in message && message.epoch !== undefined && message.epoch !== this.snapshot.epoch) return;
    if (message.kind === 'dropped') {
      this.update({ dropped: this.snapshot.dropped + 1 });
      return;
    }
    if (message.kind === 'error' && message.designRevision === useDesignStore.getState().designRevision) {
      const detail = message.message ?? Object.values(message.fields ?? {})[0] ?? message.code;
      this.update({ error: detail, stale: true });
    }
  }

  private onFrame(buffer: ArrayBuffer): void {
    let frame: DecodedFrame;
    try {
      frame = decodeFrame(buffer, this.maxFrameBytes);
    } catch (error) {
      this.update({ error: error instanceof Error ? error.message : String(error) });
      return;
    }
    const { header } = frame;
    const revision = useDesignStore.getState().designRevision;
    if (header.epoch !== this.snapshot.epoch || header.designRevision !== revision) {
      this.update({ stale: true });
      return;
    }
    this.update({
      frame,
      displayedRevision: revision,
      lastValidRevision: revision,
      stale: false,
      error: null,
    });
  }

  private onRevision(event: RevisionEvent): void {
    this.update({ stale: event.revision !== this.snapshot.displayedRevision });
    if (event.immediate) {
      this.cancelDebounce();
      this.sendCurrent('fine');
      return;
    }
    this.queuedRevision = event.revision;
    if (!this.debounceTimer) {
      this.sendCurrent('coarse');
      this.debounceTimer = setTimeout(() => {
        this.debounceTimer = null;
        if (this.queuedRevision !== null) this.sendCurrent('fine');
        this.queuedRevision = null;
      }, 33);
    }
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

  private cancelDebounce(): void {
    if (this.debounceTimer) clearTimeout(this.debounceTimer);
    this.debounceTimer = null;
    this.queuedRevision = null;
  }

  private clearTimer(kind: 'heartbeat' | 'reconnect'): void {
    const timer = kind === 'heartbeat' ? this.heartbeatTimer : this.reconnectTimer;
    if (timer) clearTimeout(timer);
    if (kind === 'heartbeat') this.heartbeatTimer = null;
    else this.reconnectTimer = null;
  }

  private update(patch: Partial<PreviewSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    this.listeners.forEach((listener) => listener());
  }
}

export const previewSocket = new PreviewSocketManager();
