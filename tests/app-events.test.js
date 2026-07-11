import test from 'node:test';
import assert from 'node:assert/strict';

import { GlobalState } from '../src/state.js';
import { handleUndoRedoShortcut } from '../src/app/events.js';

function createKeyboardEvent({
  key,
  target = null,
  ctrlKey = false,
  metaKey = false,
  shiftKey = false,
}) {
  return {
    key,
    target,
    ctrlKey,
    metaKey,
    shiftKey,
    prevented: false,
    preventDefault() {
      this.prevented = true;
    },
  };
}

test('global undo shortcuts support shifted redo and leave editable targets alone', () => {
  const undoDescriptor = Object.getOwnPropertyDescriptor(GlobalState, 'undo');
  const redoDescriptor = Object.getOwnPropertyDescriptor(GlobalState, 'redo');
  let undoCalls = 0;
  let redoCalls = 0;
  GlobalState.undo = () => {
    undoCalls += 1;
  };
  GlobalState.redo = () => {
    redoCalls += 1;
  };

  try {
    const undoEvent = createKeyboardEvent({ key: 'z', metaKey: true });
    assert.equal(handleUndoRedoShortcut(undoEvent), true);
    assert.equal(undoCalls, 1);
    assert.equal(redoCalls, 0);
    assert.equal(undoEvent.prevented, true);

    const redoEvent = createKeyboardEvent({ key: 'Z', ctrlKey: true, shiftKey: true });
    assert.equal(handleUndoRedoShortcut(redoEvent), true);
    assert.equal(undoCalls, 1);
    assert.equal(redoCalls, 1);
    assert.equal(redoEvent.prevented, true);

    const inputEvent = createKeyboardEvent({
      key: 'z',
      metaKey: true,
      target: { tagName: 'INPUT' },
    });
    assert.equal(handleUndoRedoShortcut(inputEvent), false);
    assert.equal(undoCalls, 1);
    assert.equal(inputEvent.prevented, false);

    const contentEditableEvent = createKeyboardEvent({
      key: 'Z',
      ctrlKey: true,
      shiftKey: true,
      target: { isContentEditable: true },
    });
    assert.equal(handleUndoRedoShortcut(contentEditableEvent), false);
    assert.equal(redoCalls, 1);
    assert.equal(contentEditableEvent.prevented, false);
  } finally {
    if (undoDescriptor) {
      Object.defineProperty(GlobalState, 'undo', undoDescriptor);
    } else {
      delete GlobalState.undo;
    }
    if (redoDescriptor) {
      Object.defineProperty(GlobalState, 'redo', redoDescriptor);
    } else {
      delete GlobalState.redo;
    }
  }
});
