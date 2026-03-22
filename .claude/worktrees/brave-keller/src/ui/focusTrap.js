const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function getFocusableElements(root) {
  if (!root || typeof root.querySelectorAll !== "function") return [];
  return Array.from(root.querySelectorAll(FOCUSABLE_SELECTOR)).filter(
    (el) => el.offsetParent !== null && !el.hasAttribute("hidden"),
  );
}

export function trapFocus(container, { initialFocus } = {}) {
  if (!container) return () => {};

  const focusable = getFocusableElements(container);
  const firstFocusable = focusable[0];
  const lastFocusable = focusable[focusable.length - 1];

  const target = initialFocus || firstFocusable || container;
  if (target && typeof target.focus === "function") {
    const raf =
      typeof requestAnimationFrame === "function"
        ? requestAnimationFrame
        : (fn) => fn();
    raf(() => target.focus());
  }

  function onKeyDown(event) {
    if (event.key !== "Tab") return;

    const currentFocusable = getFocusableElements(container);
    if (currentFocusable.length === 0) {
      event.preventDefault();
      return;
    }

    const currentFirst = currentFocusable[0];
    const currentLast = currentFocusable[currentFocusable.length - 1];

    if (event.shiftKey) {
      if (
        document.activeElement === currentFirst ||
        document.activeElement === container
      ) {
        event.preventDefault();
        currentLast.focus();
      }
    } else {
      if (document.activeElement === currentLast) {
        event.preventDefault();
        currentFirst.focus();
      }
    }
  }

  container.addEventListener("keydown", onKeyDown);

  return function release() {
    container.removeEventListener("keydown", onKeyDown);
  };
}
