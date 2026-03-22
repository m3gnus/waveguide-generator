export function setupPanelSizing(app) {
  app.uiPanel = document.getElementById("ui-panel");
  app.uiPanelResizer = document.getElementById("ui-panel-resizer");
  if (!app.uiPanel || !app.uiPanelResizer) return;

  if (window.matchMedia("(max-width: 768px)").matches) {
    return;
  }

  const rootStyles = getComputedStyle(document.documentElement);
  app.panelDefaultWidth =
    parseFloat(rootStyles.getPropertyValue("--panel-default-width")) || 350;
  app.panelMinWidth =
    parseFloat(rootStyles.getPropertyValue("--panel-min-width")) || 280;
  app.panelMaxWidth =
    parseFloat(rootStyles.getPropertyValue("--panel-max-width")) || 520;
  app.userResizedPanel = false;
  app.panelAutoSizeFrame = null;

  app.uiPanel.style.width = `${app.panelDefaultWidth}px`;

  app.uiPanelResizer.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    app.isResizingPanel = true;
    app.userResizedPanel = true;
    app.panelResizeStartX = event.clientX;
    app.panelResizeStartWidth = app.uiPanel.getBoundingClientRect().width;
    app.uiPanelResizer.setPointerCapture(event.pointerId);
    document.body.style.cursor = "col-resize";
  });

  app.uiPanelResizer.addEventListener("pointermove", (event) => {
    if (!app.isResizingPanel) return;
    const delta = event.clientX - app.panelResizeStartX;
    setPanelWidth(app, app.panelResizeStartWidth + delta);
  });

  const stopResize = (event) => {
    if (!app.isResizingPanel) return;
    app.isResizingPanel = false;
    if (event?.pointerId !== undefined) {
      app.uiPanelResizer.releasePointerCapture(event.pointerId);
    }
    document.body.style.cursor = "";
  };

  app.uiPanelResizer.addEventListener("pointerup", stopResize);
  app.uiPanelResizer.addEventListener("pointercancel", stopResize);

  app.uiPanel.addEventListener("input", () => schedulePanelAutoSize(app));
  app.uiPanel.addEventListener(
    "toggle",
    () => schedulePanelAutoSize(app),
    true,
  );
  window.addEventListener("resize", () => schedulePanelAutoSize(app));

  schedulePanelAutoSize(app);
}

export function setPanelWidth(app, width) {
  if (!app.uiPanel) return;
  const clamped = clampPanelWidth(app, width);
  const current = app.uiPanel.getBoundingClientRect().width;
  if (Math.abs(clamped - current) < 1) return;
  app.uiPanel.style.width = `${clamped}px`;
  app.onResize();
}

export function schedulePanelAutoSize(app) {
  if (app.userResizedPanel || !app.uiPanel) return;
  if (app.panelAutoSizeFrame) {
    cancelAnimationFrame(app.panelAutoSizeFrame);
  }
  app.panelAutoSizeFrame = requestAnimationFrame(() => {
    app.panelAutoSizeFrame = null;
    autoSizePanel(app);
  });
}

export function autoSizePanel(app) {
  if (app.userResizedPanel || !app.uiPanel) return;
  const activeTab = app.uiPanel.querySelector(".tab-content.active");
  const contentWidth = Math.max(
    app.uiPanel.scrollWidth,
    activeTab ? activeTab.scrollWidth : 0,
  );
  const target = clampPanelWidth(app, contentWidth);
  setPanelWidth(app, target);
}

function clampPanelWidth(app, width) {
  const max = Math.min(app.panelMaxWidth, window.innerWidth * 0.7);
  return Math.max(app.panelMinWidth, Math.min(max, width));
}

export function setupRightPanelSizing(app) {
  app.actionsPanel = document.getElementById("actions-panel");
  app.actionsPanelResizer = document.getElementById("actions-panel-resizer");
  if (!app.actionsPanel || !app.actionsPanelResizer) return;

  if (window.matchMedia("(max-width: 768px)").matches) {
    return;
  }

  const rootStyles = getComputedStyle(document.documentElement);
  app.rightPanelDefaultWidth =
    parseFloat(rootStyles.getPropertyValue("--panel-default-width")) || 340;
  app.rightPanelMinWidth =
    parseFloat(rootStyles.getPropertyValue("--panel-min-width")) || 280;
  app.rightPanelMaxWidth =
    parseFloat(rootStyles.getPropertyValue("--panel-max-width")) || 520;

  app.actionsPanelResizer.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    app.isResizingRightPanel = true;
    app.rightPanelResizeStartX = event.clientX;
    app.rightPanelResizeStartWidth =
      app.actionsPanel.getBoundingClientRect().width;
    app.actionsPanelResizer.setPointerCapture(event.pointerId);
    document.body.style.cursor = "col-resize";
  });

  app.actionsPanelResizer.addEventListener("pointermove", (event) => {
    if (!app.isResizingRightPanel) return;
    // Dragging left (negative delta) = wider right panel
    const delta = event.clientX - app.rightPanelResizeStartX;
    const newWidth = app.rightPanelResizeStartWidth - delta;
    const maxW = Math.min(app.rightPanelMaxWidth, window.innerWidth * 0.5);
    const clamped = Math.max(app.rightPanelMinWidth, Math.min(maxW, newWidth));
    app.actionsPanel.style.width = `${clamped}px`;
    app.onResize();
  });

  const stopRightResize = (event) => {
    if (!app.isResizingRightPanel) return;
    app.isResizingRightPanel = false;
    if (event?.pointerId !== undefined) {
      app.actionsPanelResizer.releasePointerCapture(event.pointerId);
    }
    document.body.style.cursor = "";
  };

  app.actionsPanelResizer.addEventListener("pointerup", stopRightResize);
  app.actionsPanelResizer.addEventListener("pointercancel", stopRightResize);
}
