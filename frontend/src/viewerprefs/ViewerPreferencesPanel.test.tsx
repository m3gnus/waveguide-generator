import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { ViewerPreferencesPanel } from './ViewerPreferencesPanel';
import { viewerPreferences } from './viewerPreferences';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('the symmetry plane toggle', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    viewerPreferences.update({ markSymmetryPlanes: true });
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    viewerPreferences.update({ markSymmetryPlanes: true });
  });

  function render() {
    act(() => root.render(<ViewerPreferencesPanel
      preferences={viewerPreferences.getSnapshot()}
      showEnclosure
      showStats={false}
      onShowEnclosure={() => undefined}
      onShowStats={() => undefined}
      onClose={() => undefined}
    />));
  }

  function toggle(): HTMLInputElement {
    const label = [...host.querySelectorAll('label.viewer-pref-toggle')]
      .find((item) => item.textContent === 'Mark symmetry planes');
    const input = label?.querySelector('input');
    if (!input) throw new Error('no symmetry plane toggle');
    return input;
  }

  it('turns the marks of a reduced CAD solve off and on again', () => {
    render();
    expect(toggle().checked).toBe(true);
    act(() => toggle().click());
    expect(viewerPreferences.getSnapshot().markSymmetryPlanes).toBe(false);
    render();
    expect(toggle().checked).toBe(false);
    act(() => toggle().click());
    expect(viewerPreferences.getSnapshot().markSymmetryPlanes).toBe(true);
  });
});
