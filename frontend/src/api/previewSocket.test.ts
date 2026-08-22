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

  it('does not accept a hello from an unsupported protocol version', () => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    manager.start();
    socket.message(JSON.stringify({ v: 2, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    expect(manager.getSnapshot()).toMatchObject({ connection: 'connecting', error: 'Unsupported preview protocol message' });
    expect(socket.sent).toHaveLength(0);
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

  it('rebuilds the preview after New design rewinds the revision', () => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    useDesignStore.setState({ designRevision: 57 });
    manager.start();
    socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    socket.message(fixture());
    expect(manager.getSnapshot().displayedRevision).toBe(57);

    // New design rewinds the counter to 1. The rendered revision is a floor
    // against late frames inside one editing stream; carried across a document
    // load it rejects every frame the new document will ever produce.
    resetDesignStore();
    for (let i = 0; i < 9; i += 1) useDesignStore.getState().updateField('a', 40 + i);
    expect(useDesignStore.getState().designRevision).toBe(10);
    socket.message(fixtureAtRevision(10));

    expect(manager.getSnapshot().displayedRevision).toBe(10);
    expect(manager.getSnapshot().stale).toBe(false);
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
    socket.message(JSON.stringify({ v: 1, kind: 'error', epoch: 3, designRevision: 57, code: 'internal', message: 'inconsistent local orientation' }));
    // Discarding this used to leave the viewport reading STALE with no reason.
    expect(manager.getSnapshot().error).toBe('inconsistent local orientation');
    expect(manager.getSnapshot().errorFields).toBeNull();
    expect(manager.getSnapshot().errorRevision).toBe(57);
    expect(manager.getSnapshot().stale).toBe(true);
    manager.stop();
  });

  it('preserves validated field errors and keeps unknown keys globally actionable', () => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    manager.start();
    socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    socket.message(JSON.stringify({
      v: 1,
      kind: 'error',
      epoch: 3,
      designRevision: 1,
      code: 'validation',
      fields: { 'morph.corner_radius': 'raise the radius', future_field: 'future detail', malformed: 42 },
    }));

    expect(manager.getSnapshot()).toMatchObject({
      error: 'raise the radius',
      errorRevision: 1,
      errorFields: { 'morph.corner_radius': 'raise the radius', future_field: 'future detail' },
    });
    manager.stop();
  });

  it('does not resurrect a field error after a newer lane has superseded it', () => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    manager.start();
    socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    socket.message(JSON.stringify({ v: 1, kind: 'error', epoch: 3, seq: 3, designRevision: 3, code: 'validation', fields: { 'morph.corner_radius': 'new failure' } }));
    socket.message(JSON.stringify({ v: 1, kind: 'error', epoch: 3, seq: 2, designRevision: 2, code: 'validation', fields: { 'morph.corner_radius': 'late old failure' } }));

    expect(manager.getSnapshot()).toMatchObject({
      error: 'new failure',
      errorRevision: 3,
      errorFields: { 'morph.corner_radius': 'new failure' },
    });
    manager.stop();
  });

  it('does not resurrect a same-revision field error after a newer request succeeded', () => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    useDesignStore.setState({ designRevision: 57 });
    manager.start();
    socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    socket.message(fixture()); // request seq 412 succeeded
    socket.message(JSON.stringify({ v: 1, kind: 'error', epoch: 3, seq: 411, designRevision: 57, code: 'validation', fields: { 'morph.corner_radius': 'late coarse failure' } }));

    expect(manager.getSnapshot().error).toBeNull();
    expect(manager.getSnapshot().errorFields).toBeNull();
    manager.stop();
  });

  it('clears the error once a frame for the current revision arrives', () => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    useDesignStore.setState({ designRevision: 57 });
    manager.start();
    socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    socket.message(JSON.stringify({ v: 1, kind: 'error', epoch: 3, designRevision: 57, code: 'validation', message: 'bad expression' }));
    expect(manager.getSnapshot().error).toBe('bad expression');
    socket.message(fixture());
    expect(manager.getSnapshot().error).toBeNull();
    expect(manager.getSnapshot().errorFields).toBeNull();
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

  // Curvature sections are built on the dense canonical master and cost about
  // a third of a fine frame; only the curvature heatmap reads them. Every
  // request says whether the viewport is on that mode, so the other seven
  // modes never pay for it.
  it('asks for curvature only while the heatmap is the display mode', () => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    manager.start();
    socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    vi.advanceTimersByTime(140);
    const requests = () => socket.sent.map((message) => JSON.parse(message));
    expect(requests().every(({ curvature }) => curvature === false)).toBe(true);

    // Switching on re-requests at once: the frame on screen has no curvature
    // in it, and no edit is coming to trigger another build.
    const before = socket.sent.length;
    manager.setCurvatureWanted(true);
    expect(socket.sent).toHaveLength(before + 1);
    expect(requests()[before]).toMatchObject({ lod: 'fine', curvature: true });

    // Re-asserting the same mode is not an edit and must not cost a build.
    manager.setCurvatureWanted(true);
    expect(socket.sent).toHaveLength(before + 1);

    // Switching off changes nothing on screen, so it spends no request; the
    // next frame simply stops carrying the sections.
    manager.setCurvatureWanted(false);
    expect(socket.sent).toHaveLength(before + 1);
    useDesignStore.setState({ designRevision: 58 });
    manager.refresh();
    expect(requests()[before + 1]).toMatchObject({ lod: 'fine', curvature: false });
    manager.stop();
  });
});
