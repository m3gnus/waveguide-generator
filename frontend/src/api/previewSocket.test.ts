import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { PreviewSocketManager, type WebSocketLike } from './previewSocket';

class MockSocket implements WebSocketLike {
  binaryType = '';
  readyState = 1;
  onopen: WebSocketLike['onopen'] = null;
  onmessage: WebSocketLike['onmessage'] = null;
  onerror: WebSocketLike['onerror'] = null;
  onclose: WebSocketLike['onclose'] = null;
  sent: string[] = [];
  send(data: string) { this.sent.push(data); }
  close() { this.readyState = 3; this.onclose?.({}); }
  message(data: unknown) { this.onmessage?.({ data }); }
}

function fixture(): ArrayBuffer {
  const bytes = readFileSync('../shared/frame-fixtures/good/minimal-preview.bin');
  const buffer = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(buffer).set(bytes);
  return buffer;
}

describe('preview socket state machine', () => {
  beforeEach(() => { vi.useFakeTimers(); resetDesignStore(); });
  afterEach(() => vi.useRealTimers());

  it('accepts one hello, echoes its epoch, and sends the current full design', () => {
    const sockets: MockSocket[] = [];
    const manager = new PreviewSocketManager(() => { const socket = new MockSocket(); sockets.push(socket); return socket; }, 'ws://test/ws/preview');
    manager.start();
    sockets[0].message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    sockets[0].message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    expect(manager.getSnapshot().connection).toBe('connected');
    expect(sockets[0].sent).toHaveLength(1);
    expect(JSON.parse(sockets[0].sent[0])).toMatchObject({ kind: 'preview', epoch: 3, seq: 1, designRevision: 1, lod: 'fine' });
    manager.stop();
  });

  it('drops stale-revision frames and accepts current epoch/current revision only', () => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    useDesignStore.setState({ designRevision: 57 });
    manager.start();
    socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    useDesignStore.setState({ designRevision: 58 });
    socket.message(fixture());
    expect(manager.getSnapshot().frame).toBeNull();
    expect(manager.getSnapshot().stale).toBe(true);
    useDesignStore.setState({ designRevision: 57 });
    socket.message(fixture());
    expect(manager.getSnapshot().displayedRevision).toBe(57);
    expect(manager.getSnapshot().stale).toBe(false);
    manager.stop();
  });

  it('reconnects with backoff and resends the latest state after the new hello', () => {
    const sockets: MockSocket[] = [];
    const manager = new PreviewSocketManager(() => { const socket = new MockSocket(); sockets.push(socket); return socket; }, 'ws://test/ws/preview');
    manager.start();
    sockets[0].message(JSON.stringify({ v: 1, kind: 'hello', epoch: 7, heartbeatSec: 15 }));
    useDesignStore.getState().updateField('a', 46);
    sockets[0].close();
    vi.advanceTimersByTime(250);
    expect(sockets).toHaveLength(2);
    sockets[1].message(JSON.stringify({ v: 1, kind: 'hello', epoch: 8, heartbeatSec: 15 }));
    expect(JSON.parse(sockets[1].sent[0])).toMatchObject({ epoch: 8, seq: 1, designRevision: 2, lod: 'fine' });
    manager.stop();
  });
});
