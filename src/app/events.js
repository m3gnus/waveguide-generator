import { GlobalState, ImportedMeshState } from '../state.js';
import { AppEvents } from '../events.js';
import { parseMSH } from '../import/mshParser.js';
import { appUiFileOps } from './uiAdapters.js';

export function setupEventListeners(app) {
  // Bind all button events using a helper method
  bindButtonEvents(app);

  // live-update and display-mode are now inside the settings modal (created on demand).
  // Use document-level event delegation so the handlers fire regardless of when the
  // elements are inserted into the DOM.
  const renderBtn = document.getElementById('render-btn');

  document.addEventListener('change', (e) => {
    if (e.target && e.target.id === 'live-update') {
      if (renderBtn) renderBtn.classList.toggle('is-hidden', e.target.checked);
    }
    if (e.target && e.target.id === 'display-mode') {
      app.requestRender();
    }
  });

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
    { id: 'export-config-btn', handler: () => app.exportMWGConfig(), type: 'click' },
    { id: 'choose-folder-btn', handler: () => app.uiCoordinator.chooseOutputFolder(), type: 'click' },
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
    {
      id: 'settings-btn',
      handler: () => app.uiCoordinator.openSettings({
        viewerRuntime: {
          getControls: () => app.controls || null,
          getDomElement: () => app.renderer?.domElement || null
        }
      }),
      type: 'click'
    }
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

  // check-updates-btn lives inside the dynamically-created settings modal.
  // Use document-level delegation so it works regardless of when the modal is opened.
  // Pass the clicked element directly so checkForUpdates can disable it without a
  // secondary getElementById lookup.
  document.addEventListener('click', (e) => {
    if (e.target && e.target.id === 'check-updates-btn') {
      app.checkForUpdates(e.target);
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

  // Return to Parametric button
  const returnBtn = document.getElementById('return-parametric-btn');
  if (returnBtn) {
    returnBtn.addEventListener('click', () => {
      ImportedMeshState.active = false;
      ImportedMeshState.filename = null;
      ImportedMeshState.vertices = null;
      ImportedMeshState.indices = null;
      ImportedMeshState.physicalTags = null;
      ImportedMeshState.physicalNames = null;
      const banner = document.getElementById('imported-mesh-banner');
      if (banner) banner.classList.add('is-hidden');
      AppEvents.emit('state:updated', GlobalState.get(), { source: 'return-to-parametric' });
    });
    console.log('Bound return-to-parametric handler');
  }

  // Mesh import handling
  const importMeshBtn = document.getElementById('import-mesh-btn');
  const meshInput = document.getElementById('mesh-upload');
  if (importMeshBtn && meshInput) {
    importMeshBtn.addEventListener('click', () => meshInput.click());
    meshInput.addEventListener('change', (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (evt) => {
        try {
          const result = parseMSH(evt.target.result);
          ImportedMeshState.active = true;
          ImportedMeshState.filename = file.name;
          ImportedMeshState.vertices = result.vertices;
          ImportedMeshState.indices = result.indices;
          ImportedMeshState.physicalTags = result.physicalTags;
          ImportedMeshState.physicalNames = result.physicalNames;
          appUiFileOps.setExportFields(appUiFileOps.deriveExportFieldsFromFileName(file.name));
          const banner = document.getElementById('imported-mesh-banner');
          const filenameSpan = document.getElementById('imported-mesh-filename');
          if (banner) {
            banner.classList.remove('is-hidden');
            if (filenameSpan) filenameSpan.textContent = `Imported: ${file.name}`;
          }
          AppEvents.emit('mesh:imported', ImportedMeshState);
        } catch (err) {
          console.error('MSH import failed:', err);
          const statsEl = document.getElementById('stats');
          if (statsEl) {
            statsEl.innerText = `Import failed: ${err.message}`;
          }
        }
      };
      reader.readAsText(file);
      meshInput.value = ''; // reset for re-import
    });
    console.log('Bound mesh import handlers');
  } else {
    if (!importMeshBtn) console.warn('Element import-mesh-btn not found');
    if (!meshInput) console.warn('Element mesh-upload not found');
  }
}
