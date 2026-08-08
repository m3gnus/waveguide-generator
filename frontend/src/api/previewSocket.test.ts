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

/** The same fixture with a different two-digit revision patched into its header. */
function fixtureAtRevision(revision: number): ArrayBuffer {
  const buffer = fixture();
  const headerLength = new DataView(buffer).getUint32(4, true);
  const header = new Uint8Array(buffer, 8, headerLength);
  const text = new TextDecoder().decode(header);
  const replaced = text.replace('"designRevision":57', `"designRevision":${revision}`);
  if (replaced.length !== text.length) throw new Error('revision must stay two digits');
  header.set(new TextEncoder().encode(replaced));
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
    expect(JSON.parse(sockets[0].sent[0])).toMatchObject({ kind: 'preview', epoch: 3, seq: 1, designRevision: 1, lod: 'coarse' });
    vi.advanceTimersByTime(140);
    expect(JSON.parse(sockets[0].sent[1])).toMatchObject({ kind: 'preview', epoch: 3, seq: 2, designRevision: 1, lod: 'fine' });
    manager.stop();
  });

  it('drops frames from a stale epoch', () => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    useDesignStore.setState({ designRevision: 57 });
    manager.start();
    socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 9, heartbeatSec: 15 }));
    // The fixture belongs to epoch 3: a frame from a pre-reconnect socket.
    socket.message(fixture());
    expect(manager.getSnapshot().frame).toBeNull();
    expect(manager.getSnapshot().stale).toBe(true);
    manager.stop();
  });

  // The rule this replaces rendered a frame only while its revision still
  // equalled the store's, which no gesture can satisfy: the store commits a
  // revision per pointermove while the server needs 96-192 ms to build even a
  // coarse preview. Measured against the real mesher, a 2.2 s drag produced 21
  // valid coarse frames and the client accepted none of them.
  it('renders a frame that lags the live design, and says it is stale', () => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    useDesignStore.setState({ designRevision: 57 });
    manager.start();
    socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    useDesignStore.setState({ designRevision: 71 });
    socket.message(fixture());
    expect(manager.getSnapshot().frame).not.toBeNull();
    expect(manager.getSnapshot().displayedRevision).toBe(57);
    expect(manager.getSnapshot().stale).toBe(true);
    manager.stop();
  });

  it('reports a frame that caught up with the design as current', () => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    useDesignStore.setState({ designRevision: 57 });
    manager.start();
    socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    socket.message(fixture());
    expect(manager.getSnapshot().displayedRevision).toBe(57);
    expect(manager.getSnapshot().stale).toBe(false);
    manager.stop();
  });

  it('never renders geometry that an undo has already superseded', () => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    useDesignStore.setState({ designRevision: 57 });
    manager.start();
    socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    // An in-flight request for revision 57 is answered after the user undoes.
    // Unlike a drag, the design that comes back is not an earlier point on the
    // same gesture -- it is the state the user just rejected.
    useDesignStore.getState().updateField('a', 46);
    useDesignStore.getState().undo();
    socket.message(fixture());
    expect(manager.getSnapshot().frame).toBeNull();
    expect(manager.getSnapshot().stale).toBe(true);
    manager.stop();
  });

  it('does not go backwards when an older frame arrives after a newer one', () => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    useDesignStore.setState({ designRevision: 57 });
    manager.start();
    socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    socket.message(fixture());
    const displayed = manager.getSnapshot().frame;
    socket.message(fixtureAtRevision(56));
    expect(manager.getSnapshot().frame).toBe(displayed);
    expect(manager.getSnapshot().displayedRevision).toBe(57);
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
    expect(JSON.parse(sockets[1].sent[0])).toMatchObject({ epoch: 8, seq: 1, designRevision: 2, lod: 'coarse' });
    manager.stop();
  });

  it('keeps coarse previews flowing but waits for edit inactivity before fine work', () => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    manager.start();
    socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    vi.advanceTimersByTime(140);
    socket.sent.length = 0;

    useDesignStore.getState().updateField('a', 46);
    vi.advanceTimersByTime(70);
    useDesignStore.getState().updateField('a', 47);
    vi.advanceTimersByTime(139);
    expect(socket.sent.map((message) => JSON.parse(message)).filter(({ lod }) => lod === 'fine')).toHaveLength(0);
    vi.advanceTimersByTime(1);
    const fine = socket.sent.map((message) => JSON.parse(message)).filter(({ lod }) => lod === 'fine');
    expect(fine).toHaveLength(1);
    expect(fine[0]).toMatchObject({ designRevision: 3, lod: 'fine' });
    manager.stop();
  });

  it('keeps an error raised for an edit the user has already moved past', () => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    useDesignStore.setState({ designRevision: 57 });
    manager.start();
    socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    useDesignStore.setState({ designRevision: 58 });
    socket.message(JSON.stringify({ kind: 'error', epoch: 3, designRevision: 57, code: 'internal', message: 'inconsistent local orientation' }));
    // Discarding this used to leave the viewport reading STALE with no reason.
    expect(manager.getSnapshot().error).toBe('inconsistent local orientation');
    expect(manager.getSnapshot().errorRevision).toBe(57);
    expect(manager.getSnapshot().stale).toBe(true);
    manager.stop();
  });

  it('clears the error once a frame for the current revision arrives', () => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    useDesignStore.setState({ designRevision: 57 });
    manager.start();
    socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    socket.message(JSON.stringify({ kind: 'error', epoch: 3, designRevision: 57, code: 'validation', message: 'bad expression' }));
    expect(manager.getSnapshot().error).toBe('bad expression');
    socket.message(fixture());
    expect(manager.getSnapshot().error).toBeNull();
    expect(manager.getSnapshot().errorRevision).toBeNull();
    manager.stop();
  });

  it('refresh re-requests the current design at full detail without an edit', () => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    useDesignStore.setState({ designRevision: 57 });
    manager.start();
    socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    expect(socket.sent).toHaveLength(1);
    manager.refresh();
    expect(socket.sent).toHaveLength(2);
    expect(JSON.parse(socket.sent[1])).toMatchObject({ kind: 'preview', epoch: 3, designRevision: 57, lod: 'fine' });
    manager.stop();
  });

  // Every subscriber reads the snapshot through useSyncExternalStore, which
  // compares by identity, so one notification is one re-render of the whole
  // viewport subtree. A drag commits a revision per pointermove and each one
  // only ever sets `stale`, which goes true on the first and stays true.
  it('notifies once for a drag, not once per pointermove', () => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    useDesignStore.setState({ designRevision: 57 });
    manager.start();
    socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    socket.message(fixture());
    expect(manager.getSnapshot().stale).toBe(false);

    let notifications = 0;
    const unsubscribe = manager.subscribe(() => { notifications += 1; });
    for (let move = 0; move < 60; move += 1) useDesignStore.getState().updateField('a', 40 + move);
    unsubscribe();

    expect(useDesignStore.getState().designRevision).toBe(117);
    expect(notifications).toBe(1);
    expect(manager.getSnapshot().stale).toBe(true);
    manager.stop();
  });

  it('refresh on a stopped socket restarts it rather than sending into the void', () => {
    const sockets: MockSocket[] = [];
    const manager = new PreviewSocketManager(() => { const socket = new MockSocket(); sockets.push(socket); return socket; }, 'ws://test/ws/preview');
    manager.refresh();
    expect(sockets).toHaveLength(1);
    manager.stop();
  });
});
