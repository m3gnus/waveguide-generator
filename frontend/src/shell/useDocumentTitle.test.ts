import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { APP_NAME, documentTitle, useDocumentTitle } from './useDocumentTitle';

describe('document title', () => {
  // index.html used to hardcode one specific .cfg filename, so every window
  // claimed that document was open whatever was actually on screen.
  it('names the app alone when no document is open', () => {
    expect(documentTitle('')).toBe(APP_NAME);
    expect(documentTitle('   ')).toBe(APP_NAME);
  });

  it('leads with the document, so it survives a truncated tab', () => {
    expect(documentTitle('tritonia_mk2.cfg')).toBe(`tritonia_mk2.cfg — ${APP_NAME}`);
  });
});

describe('the window title', () => {
  let container: HTMLDivElement;
  let root: Root;

  function Title() {
    useDocumentTitle();
    return null;
  }

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore();
    resetDocumentStore();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  // WG has no Save, so there is no saved state for a bullet to mark: the
  // title names the design, and nothing about it changes with an edit.
  it('carries no bullet after an edit or a rename', () => {
    useDocumentStore.getState().setDesignName('horn');
    useDocumentStore.getState().markSaved(useDesignStore.getState().designRevision);
    act(() => root.render(createElement(Title)));
    expect(document.title).toBe(`horn — ${APP_NAME}`);

    act(() => useDesignStore.getState().updateField('R', 321));
    expect(document.title).toBe(`horn — ${APP_NAME}`);

    act(() => useDesignStore.getState().undo());
    act(() => useDocumentStore.getState().setDesignName('renamed horn'));
    expect(document.title).toBe(`renamed horn — ${APP_NAME}`);
  });
});
