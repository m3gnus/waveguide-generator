import { GlobalState } from '../state.js';
import { selectOutputFolder } from '../ui/fileOps.js';

export function setupEventListeners(app) {
  // Bind all button events using a helper method
  bindButtonEvents(app);

  // Hide folder selection button if not supported by the browser
  if (!window.showDirectoryPicker) {
    const folderRow = document.getElementById('output-folder-row');
    if (folderRow) folderRow.style.display = 'none';
  }

  // Undo/Redo keys
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'z') {
      e.preventDefault();
      if (e.shiftKey) {
        GlobalState.redo();
      } else {
        GlobalState.undo();
      }
    }
  });
}

export function bindButtonEvents(app) {
  const buttonBindings = [
    { id: 'render-btn', handler: () => app.requestRender(), type: 'click' },
    { id: 'export-btn', handler: () => app.exportSTL(), type: 'click' },
    { id: 'export-config-btn', handler: () => app.exportMWGConfig(), type: 'click' },
    { id: 'choose-folder-btn', handler: selectOutputFolder, type: 'click' },
    { id: 'display-mode', handler: () => app.requestRender(), type: 'change' },
    { id: 'zoom-in', handler: () => app.zoom(0.8), type: 'click' },
    { id: 'zoom-out', handler: () => app.zoom(1.2), type: 'click' },
    { id: 'camera-toggle', handler: () => app.toggleCamera(), type: 'click' },
    {
      id: 'zoom-reset',
      handler: () => {
        if (app.controls) app.controls.reset();
      },
      type: 'click'
    },
    { id: 'focus-horn', handler: () => app.focusOnModel(), type: 'click' },
    { id: 'export-csv-btn', handler: () => app.exportProfileCSV(), type: 'click' },
    { id: 'export-geo-btn', handler: () => app.exportGmshGeo(), type: 'click' },
    { id: 'export-msh-btn', handler: () => app.exportMSH(), type: 'click' },
    { id: 'export-abec-btn', handler: () => app.exportABECProject(), type: 'click' },
    { id: 'check-updates-btn', handler: () => app.checkForUpdates(), type: 'click' }
  ];

  buttonBindings.forEach(({ id, handler, type }) => {
    const element = document.getElementById(id);
    if (element) {
      element.addEventListener(type, handler);
      console.log(`Bound ${type} listener to ${id}`);
    } else {
      console.warn(`Element ${id} not found in DOM - ${type} listener not attached`);
    }
  });

  // Special handling for file upload
  const loadBtn = document.getElementById('load-config-btn');
  const fileInput = document.getElementById('config-upload');
  if (loadBtn && fileInput) {
    loadBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', (e) => app.handleFileUpload(e));
    console.log('Bound file upload handlers');
  } else {
    if (!loadBtn) console.warn('Element load-config-btn not found');
    if (!fileInput) console.warn('Element config-upload not found');
  }
}
