import { readFileSync } from 'node:fs';
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { LiveDimensions } from '../design/LiveDimensions';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { PreviewSocketManager, previewSocket, type WebSocketLike } from './previewSocket';

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

/**
 * The same fixture with arbitrary header fields overridden, rebuilding the
 * length-prefixed, 8-byte-aligned framing so the header text can change size.
 * Unlike `fixtureAtRevision`, this can set `seq` to a value that does not
 * share the fixture's own digit count.
 */
function fixtureWithHeader(overrides: Record<string, unknown>): ArrayBuffer {
  const buffer = fixture();
  const view = new DataView(buffer);
  const headerLength = view.getUint32(4, true);
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 8, headerLength))) as Record<string, unknown>;
  Object.assign(header, overrides);
  const headerText = new TextEncoder().encode(JSON.stringify(header));
  const payloadBase = Math.ceil((8 + headerLength) / 8) * 8;
  const payload = new Uint8Array(buffer, payloadBase);
  const newHeaderEnd = 8 + headerText.byteLength;
  const newPayloadBase = Math.ceil(newHeaderEnd / 8) * 8;
  const out = new ArrayBuffer(newPayloadBase + payload.byteLength);
  const bytes = new Uint8Array(out);
  bytes.set(new Uint8Array(buffer, 0, 4), 0);
  new DataView(out).setUint32(4, headerText.byteLength, true);
  bytes.set(headerText, 8);
  bytes.set(payload, newPayloadBase);
  return out;
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

  // The revision-only floor above is not enough on its own: a frame the
  // *replaced* document's own in-flight request answers can still arrive
  // after New, and nothing about its revision says it belongs to the wrong
  // document. Accepting it both shows the old horn and re-creates the same
  // floor problem for every frame the new document will ever produce.
  it('drops a late frame from the design New already replaced, and never lets it block the new design', () => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    useDesignStore.setState({ designRevision: 57 });
    manager.start();
    socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    socket.message(fixture());
    expect(manager.getSnapshot().displayedRevision).toBe(57);
    const frameBeforeReset = manager.getSnapshot().frame;

    // New design rewinds the revision counter. The request sent for the old
    // design (seq 1, above) is still outstanding; its answer arrives now.
    resetDesignStore();
    socket.message(fixtureWithHeader({ seq: 1, designRevision: 57 }));

    // The late frame must never be shown: it belongs to the document New
    // replaced, not the one now on screen.
    expect(manager.getSnapshot().displayedRevision).toBeNull();
    // The viewport keeps the previous document's frame until the new one
    // arrives; only its readouts are dropped.
    expect(manager.getSnapshot().frame).toBe(frameBeforeReset);
    // An older mesher has no card state to reset; keep shared notifications unchanged.
    expect(manager.getSnapshot().awaitingDocumentFrame).toBeUndefined();
    expect(manager.getSnapshot().lastCanonicalDimensions ?? null).toBeNull();

    for (let i = 0; i < 9; i += 1) useDesignStore.getState().updateField('a', 40 + i);
    expect(useDesignStore.getState().designRevision).toBe(10);
    // seq 2 is the coarse request `resetDesignStore()` sent for the new
    // document; its answer, at the settled revision, must render normally.
    socket.message(fixtureWithHeader({ seq: 2, designRevision: 10 }));

    expect(manager.getSnapshot().displayedRevision).toBe(10);
    expect(manager.getSnapshot().stale).toBe(false);
    expect(manager.getSnapshot().frame).not.toBe(frameBeforeReset);
    expect(manager.getSnapshot().frame?.header.designRevision).toBe(10);
    expect(manager.getSnapshot().awaitingDocumentFrame).toBeUndefined();
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

it('keeps canonical dimensions attached to the newest displayed design despite late frames and errors', () => {
  const metadata = JSON.parse(new TextDecoder().decode(readFileSync('../shared/preview-fixtures/c2-dimensions-metadata.json')));
  resetDesignStore(); useDesignStore.setState({ designRevision: 58 });
  const socket = new MockSocket();
  const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
  manager.start(); socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
  socket.message(fixtureWithHeader({ designRevision: 58, seq: 2, previewMetadata: metadata }));
  const accepted = manager.getSnapshot().frame;
  const staleMetadata = structuredClone(metadata);
  staleMetadata.dimensions_mm.mouth_opening = [10, 20];
  socket.message(fixtureWithHeader({ designRevision: 57, seq: 1, previewMetadata: staleMetadata }));
  socket.message(JSON.stringify({ v: 1, kind: 'error', epoch: 3, seq: 1, designRevision: 57, code: 'INVALID' }));
  expect(manager.getSnapshot().frame).toBe(accepted);
  expect(manager.getSnapshot().frame?.header.previewMetadata).toEqual(metadata);
  expect(manager.getSnapshot().displayedRevision).toBe(58);
  expect(manager.getSnapshot().error).toBeNull();
  manager.stop(); resetDesignStore();
});

it('drops the readouts but keeps the frame when New replaces a document at the same revision number', () => {
  const metadata = JSON.parse(new TextDecoder().decode(readFileSync('../shared/preview-fixtures/c2-dimensions-metadata.json')));
  resetDesignStore();
  const socket = new MockSocket();
  const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
  manager.start(); socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
  socket.message(fixtureWithHeader({ seq: 1, designRevision: 1, previewMetadata: metadata }));
  const previous = manager.getSnapshot().frame;
  expect(manager.getSnapshot().stale).toBe(false);
  resetDesignStore();
  expect(manager.getSnapshot().stale).toBe(true);
  expect(manager.getSnapshot().displayedRevision).toBeNull();
  socket.message(fixtureWithHeader({ seq: 1, designRevision: 1, previewMetadata: metadata }));
  // The viewport keeps the previous document's frame until the new one
    // arrives; only its readouts are dropped.
    expect(manager.getSnapshot().frame).toBe(previous);
    expect(manager.getSnapshot().awaitingDocumentFrame).toBe(true);
    expect(manager.getSnapshot().lastCanonicalDimensions ?? null).toBeNull();
  expect(manager.getSnapshot().stale).toBe(true);
  socket.message(fixtureWithHeader({ seq: 2, designRevision: 1, previewMetadata: metadata }));
  expect(manager.getSnapshot().displayedRevision).toBe(1);
  expect(manager.getSnapshot().stale).toBe(false);
  manager.stop(); resetDesignStore();
});

it('preserves base old-mesher error floors across New and late outcomes', () => {
  resetDesignStore(); useDesignStore.setState({ designRevision: 58 });
  const socket = new MockSocket();
  const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
  manager.start(); socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
  socket.message(fixtureWithHeader({ seq: 1, designRevision: 58 }));
  socket.message(JSON.stringify({ v: 1, kind: 'error', epoch: 3, seq: 1, designRevision: 59, code: 'INVALID', message: 'Previous document failure' }));
  expect(manager.getSnapshot().errorRevision).toBe(59);
  resetDesignStore();
  expect(manager.getSnapshot()).toMatchObject({ error: 'Previous document failure', errorRevision: 59, lastValidRevision: 58, stale: true });
  socket.message(JSON.stringify({ v: 1, kind: 'error', epoch: 3, seq: 1, designRevision: 59, code: 'INVALID', message: 'Late old failure' }));
  expect(manager.getSnapshot().error).toBe('Late old failure');
  socket.message(JSON.stringify({ v: 1, kind: 'error', epoch: 3, seq: 2, designRevision: 1, code: 'INVALID', message: 'New document failure' }));
  expect(manager.getSnapshot()).toMatchObject({ errorRevision: 59, error: 'Late old failure' });
  socket.message(fixtureWithHeader({ seq: 2, designRevision: 1 }));
  expect(manager.getSnapshot()).toMatchObject({ error: 'Late old failure', errorRevision: 59, stale: false, displayedRevision: 1, lastValidRevision: 1 });
  socket.message(fixtureWithHeader({ seq: 3, designRevision: 60 }));
  expect(manager.getSnapshot()).toMatchObject({ error: null, errorRevision: null });
  manager.stop(); resetDesignStore();
});


describe('rendered document dimension transitions', () => {
  beforeEach(() => {
    vi.useFakeTimers(); resetDesignStore();
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(() => { vi.restoreAllMocks(); resetDesignStore(); vi.useRealTimers(); });

  it.each(['New', 'Open'] as const)('hides previous document values after %s, including failed previews', (action) => {
    const socket = new MockSocket();
    const manager = new PreviewSocketManager(() => socket, 'ws://test/ws/preview');
    vi.spyOn(previewSocket, 'subscribe').mockImplementation(manager.subscribe);
    vi.spyOn(previewSocket, 'getSnapshot').mockImplementation(manager.getSnapshot);
    const host = document.createElement('div'); document.body.append(host);
    const root = createRoot(host);
    const oldDocument = structuredClone(useDesignStore.getState().design); oldDocument.scale = 2;
    useDesignStore.getState().loadDesign(oldDocument);
    manager.start(); socket.message(JSON.stringify({ v: 1, kind: 'hello', epoch: 3, heartbeatSec: 15 }));
    act(() => root.render(createElement(LiveDimensions)));
    const firstRevision = useDesignStore.getState().designRevision;
    act(() => socket.message(fixtureWithHeader({ seq: 1, designRevision: firstRevision,
      previewMetadata: { dimensions_status: 'current', dimensions_mm: { mouth_opening: [400, 200], horn_overall: [410, 210, 200] } } })));
    expect(host.textContent).toContain('400.0 × 200.0 mm');
    const newDocument = structuredClone(oldDocument); newDocument.scale = 3;
    act(() => {
      if (action === 'New') resetDesignStore();
      else useDesignStore.getState().replaceDesign(newDocument);
    });
    expect(host.querySelector('[aria-label="Design dimensions"]')).toBeNull();
    const revision = useDesignStore.getState().designRevision;
    act(() => socket.message(JSON.stringify({ v: 1, kind: 'error', epoch: 3, seq: 2,
      designRevision: revision, code: 'INVALID', message: 'New document failed' })));
    expect(host.textContent).toContain('Dimensions unavailable');
    expect(host.textContent).not.toContain('400.0');
    // Late previous-document frames cannot resurrect the hidden readouts.
    act(() => socket.message(fixtureWithHeader({ seq: 1, designRevision: firstRevision,
      previewMetadata: { dimensions_status: 'current', dimensions_mm: { mouth_opening: [400, 200] } } })));
    expect(host.textContent).toContain('Dimensions unavailable');
    act(() => socket.message(fixtureWithHeader({ seq: 3, designRevision: revision,
      previewMetadata: { dimensions_status: 'pending', dimensions_mm: null } })));
    expect(host.textContent).toContain('Updating dimensions');
    expect(host.textContent).not.toContain('400.0');
    const mouth: [number, number] = action === 'New' ? [300, 150] : [600, 300];
    act(() => socket.message(fixtureWithHeader({ seq: 4, designRevision: revision, lod: 'fine',
      previewMetadata: { dimensions_status: 'current', dimensions_mm: { mouth_opening: mouth, horn_overall: [610, 310, 300] } } })));
    expect(host.textContent).toContain(mouth.map((value) => value.toFixed(1)).join(' × ') + ' mm');
    expect(host.textContent).toContain('Current preview');
    expect(host.textContent).not.toContain('400.0');
    // A coarse frame after another edit retains only this document's values.
    act(() => useDesignStore.getState().updateField('a', 46));
    const editedRevision = useDesignStore.getState().designRevision;
    act(() => socket.message(fixtureWithHeader({ seq: 5, designRevision: editedRevision, lod: 'coarse',
      previewMetadata: { dimensions_status: 'pending', dimensions_mm: null } })));
    expect(host.textContent).toContain('Updating dimensions');
    expect(host.textContent).toContain(mouth.map((value) => value.toFixed(1)).join(' × ') + ' mm');
    act(() => socket.message(fixtureWithHeader({ seq: 6, designRevision: editedRevision, lod: 'fine',
      previewMetadata: { dimensions_status: 'unavailable', dimensions_mm: null, dimensions_error: 'Cannot resolve' } })));
    expect(host.textContent).toContain('unavailable');
    expect(host.textContent).not.toContain(mouth[0].toFixed(1));
    act(() => root.unmount()); manager.stop(); host.remove();
  });
});
