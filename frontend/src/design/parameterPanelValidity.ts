// NumberField drafts are local to the inputs. Observe each mounted panel's
// public validity state, never the viewport or the rest of the document.
const panels = new Map<HTMLElement, boolean>();
const listeners = new Set<() => void>();
let invalid = false;

function publish() {
  const next = [...panels.values()].some(Boolean);
  if (next === invalid) return;
  invalid = next;
  listeners.forEach((listener) => listener());
}

export const parameterPanelValidity = {
  getSnapshot: () => invalid,
  subscribe: (listener: () => void) => {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
};

/** Stable React ref; cleanup also handles tab switches and panel replacement. */
export function registerParameterPanel(panel: HTMLDivElement | null) {
  if (!panel) return;
  const update = () => {
    panels.set(panel, Boolean(panel.querySelector('[aria-invalid="true"]')));
    publish();
  };
  const observer = new MutationObserver(update);
  observer.observe(panel, { subtree: true, childList: true, attributes: true, attributeFilter: ['aria-invalid'] });
  update();
  return () => {
    observer.disconnect();
    panels.delete(panel);
    publish();
  };
}
