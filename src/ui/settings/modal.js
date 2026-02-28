/**
 * Settings modal — popup with sections for Viewer, Simulation Basic,
 * Simulation Advanced, and System.
 *
 * Interaction style mirrors the View Results popup: backdrop click or ESC closes.
 */

// DOM IDs of controls that now live in Settings (used by events.js wiring)
export const SETTINGS_CONTROL_IDS = {
  liveUpdate: 'live-update',
  displayMode: 'display-mode',
  downloadSimMesh: 'download-sim-mesh',
  checkUpdates: 'check-updates-btn',
};

// In-memory settings state so preferences survive modal close/reopen
const _state = {
  liveUpdate: true,
  displayMode: 'standard',
  downloadSimMesh: false,
};

/**
 * Get the current live-update preference.
 * Returns the DOM value when modal is open, otherwise the stored value.
 */
export function getLiveUpdateEnabled() {
  const el = document.getElementById('live-update');
  if (el) return el.checked;
  return _state.liveUpdate;
}

/**
 * Get the current display-mode value.
 */
export function getDisplayMode() {
  const el = document.getElementById('display-mode');
  if (el) return el.value;
  return _state.displayMode;
}

/**
 * Get the current download-sim-mesh preference.
 */
export function getDownloadSimMeshEnabled() {
  const el = document.getElementById('download-sim-mesh');
  if (el) return el.checked;
  return _state.downloadSimMesh;
}

/**
 * Open the settings modal. Creates it on-demand and appends to document.body.
 * Returns the backdrop element so callers can await removal if needed.
 */
export function openSettingsModal() {
  // Prevent duplicate modals
  const existing = document.getElementById('settings-modal-backdrop');
  if (existing) {
    existing.focus();
    return existing;
  }

  const backdrop = _buildModal();
  document.body.appendChild(backdrop);

  // Focus the dialog for keyboard access
  const dialog = backdrop.querySelector('[role="dialog"]');
  if (dialog) dialog.focus();

  return backdrop;
}

// ---------------------------------------------------------------------------
// Internal builders
// ---------------------------------------------------------------------------

function _buildModal() {
  const backdrop = document.createElement('div');
  backdrop.id = 'settings-modal-backdrop';
  backdrop.className = 'settings-modal-backdrop';

  const dialog = document.createElement('div');
  dialog.className = 'settings-modal-dialog';
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');
  dialog.setAttribute('aria-label', 'Settings');
  dialog.setAttribute('tabindex', '-1');

  // Header
  const header = document.createElement('div');
  header.className = 'settings-modal-header';

  const title = document.createElement('h2');
  title.className = 'settings-modal-title';
  title.textContent = 'Settings';
  header.appendChild(title);

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'settings-modal-close';
  closeBtn.textContent = '\u00d7';
  closeBtn.title = 'Close (Escape)';
  closeBtn.setAttribute('aria-label', 'Close settings');
  header.appendChild(closeBtn);

  dialog.appendChild(header);

  // Body: sidebar nav + content area
  const body = document.createElement('div');
  body.className = 'settings-modal-body';

  const nav = _buildNav();
  const content = _buildContent();

  body.appendChild(nav);
  body.appendChild(content);
  dialog.appendChild(body);
  backdrop.appendChild(dialog);

  // --- Persist state changes from within the modal ---
  backdrop.addEventListener('change', (event) => {
    const t = event.target;
    if (!t) return;
    if (t.id === 'live-update') _state.liveUpdate = t.checked;
    if (t.id === 'display-mode') _state.displayMode = t.value;
    if (t.id === 'download-sim-mesh') _state.downloadSimMesh = t.checked;
  });

  // --- Close handlers ---
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    window.removeEventListener('keydown', onKeyDown);
    backdrop.remove();
  };

  const onKeyDown = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
    }
  };

  closeBtn.addEventListener('click', close);
  backdrop.addEventListener('click', (event) => {
    if (event.target === backdrop) close();
  });
  window.addEventListener('keydown', onKeyDown);

  // --- Section nav tab switching ---
  const sectionBtns = nav.querySelectorAll('.settings-nav-btn');
  const sections = content.querySelectorAll('.settings-section');

  sectionBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.section;
      sectionBtns.forEach((b) => {
        b.classList.toggle('active', b.dataset.section === target);
        b.setAttribute('aria-selected', b.dataset.section === target ? 'true' : 'false');
      });
      sections.forEach((sec) => {
        sec.hidden = sec.id !== `settings-section-${target}`;
      });
    });
  });

  return backdrop;
}

function _buildNav() {
  const nav = document.createElement('nav');
  nav.className = 'settings-modal-nav';
  nav.setAttribute('aria-label', 'Settings sections');

  const items = [
    { key: 'viewer', label: 'Viewer' },
    { key: 'sim-basic', label: 'Simulation Basic' },
    { key: 'sim-advanced', label: 'Simulation Advanced' },
    { key: 'system', label: 'System' },
  ];

  items.forEach((item, i) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'settings-nav-btn' + (i === 0 ? ' active' : '');
    btn.dataset.section = item.key;
    btn.textContent = item.label;
    btn.setAttribute('role', 'tab');
    btn.setAttribute('aria-selected', i === 0 ? 'true' : 'false');
    nav.appendChild(btn);
  });

  return nav;
}

function _buildContent() {
  const content = document.createElement('div');
  content.className = 'settings-modal-content';

  content.appendChild(_buildViewerSection());
  content.appendChild(_buildSimBasicSection());
  content.appendChild(_buildSimAdvancedSection());
  content.appendChild(_buildSystemSection());

  return content;
}

// ---------------------------------------------------------------------------
// Section builders — controls are the actual interactive elements
// ---------------------------------------------------------------------------

function _buildViewerSection() {
  const sec = document.createElement('div');
  sec.id = 'settings-section-viewer';
  sec.className = 'settings-section';
  sec.setAttribute('role', 'tabpanel');

  _appendSectionHeading(sec, 'Viewer', 'Viewport display and rendering preferences.');

  // Real-time Updates control
  _appendInlineRow(sec, {
    labelText: 'Real-time Updates',
    labelFor: 'live-update',
    controlHtml: `<input type="checkbox" id="live-update"${_state.liveUpdate ? ' checked' : ''}>`,
  });

  // Display Mode control
  const modeOptions = [
    { value: 'standard', label: 'Standard (Metal)' },
    { value: 'zebra', label: 'Zebra Stripes' },
    { value: 'grid', label: 'Grid / Wireframe' },
    { value: 'curvature', label: 'Curvature Map' },
  ];
  const modeOptionsHtml = modeOptions.map(
    (o) => `<option value="${o.value}"${_state.displayMode === o.value ? ' selected' : ''}>${o.label}</option>`
  ).join('');
  _appendInlineRow(sec, {
    labelText: 'Display Mode',
    labelFor: 'display-mode',
    controlHtml: `<select id="display-mode">${modeOptionsHtml}</select>`,
  });

  return sec;
}

function _buildSimBasicSection() {
  const sec = document.createElement('div');
  sec.id = 'settings-section-sim-basic';
  sec.className = 'settings-section';
  sec.hidden = true;
  sec.setAttribute('role', 'tabpanel');

  _appendSectionHeading(
    sec,
    'Simulation Basic',
    'BEM solver and meshing startup options.'
  );

  // Download simulation mesh on start
  _appendInlineRow(sec, {
    labelText: 'Download simulation mesh on start',
    labelFor: 'download-sim-mesh',
    controlHtml: `<input type="checkbox" id="download-sim-mesh"${_state.downloadSimMesh ? ' checked' : ''}>`,
  });

  return sec;
}

function _buildSimAdvancedSection() {
  const sec = document.createElement('div');
  sec.id = 'settings-section-sim-advanced';
  sec.className = 'settings-section';
  sec.hidden = true;
  sec.setAttribute('role', 'tabpanel');

  _appendSectionHeading(
    sec,
    'Simulation Advanced',
    'Expert BEM solver tuning and mesh quality controls. Additional options available in future releases.'
  );

  const placeholder = document.createElement('p');
  placeholder.className = 'settings-placeholder-text';
  placeholder.textContent = 'Advanced solver controls will appear here in a future update.';
  sec.appendChild(placeholder);

  return sec;
}

function _buildSystemSection() {
  const sec = document.createElement('div');
  sec.id = 'settings-section-system';
  sec.className = 'settings-section';
  sec.hidden = true;
  sec.setAttribute('role', 'tabpanel');

  _appendSectionHeading(sec, 'System', 'Application updates and system information.');

  const updateRow = document.createElement('div');
  updateRow.className = 'settings-action-row';

  const updateBtn = document.createElement('button');
  updateBtn.type = 'button';
  updateBtn.id = 'check-updates-btn';
  updateBtn.className = 'secondary';
  updateBtn.textContent = 'Check for App Updates';
  updateRow.appendChild(updateBtn);

  const updateHelp = document.createElement('p');
  updateHelp.className = 'settings-action-help';
  updateHelp.textContent =
    'Queries the backend for the latest commit on the default remote branch and reports whether the local copy is behind, ahead, or up to date.';
  updateRow.appendChild(updateHelp);

  sec.appendChild(updateRow);

  return sec;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function _appendSectionHeading(parent, title, helpText) {
  const h = document.createElement('h3');
  h.className = 'settings-section-title';
  h.textContent = title;
  parent.appendChild(h);

  if (helpText) {
    const p = document.createElement('p');
    p.className = 'settings-section-help';
    p.textContent = helpText;
    parent.appendChild(p);
  }
}

function _appendInlineRow(parent, { labelText, labelFor, controlHtml }) {
  const row = document.createElement('div');
  row.className = 'settings-control-row';

  const label = document.createElement('label');
  label.setAttribute('for', labelFor);
  label.textContent = labelText;
  row.appendChild(label);

  const wrapper = document.createElement('div');
  wrapper.className = 'settings-control-value';
  wrapper.innerHTML = controlHtml;
  row.appendChild(wrapper);

  parent.appendChild(row);
}
