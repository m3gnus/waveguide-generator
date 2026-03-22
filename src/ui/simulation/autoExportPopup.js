import { trapFocus } from '../focusTrap.js';
import {
  SIMULATION_EXPORT_FORMAT_IDS,
  getCurrentSimulationManagementSettings,
  saveSimulationManagementSettings,
} from '../settings/simulationManagementSettings.js';
import { selectOutputFolder } from '../fileOps.js';
import { subscribeFolderWorkspace } from '../workspace/folderWorkspace.js';

const FORMAT_LABELS = new Map([
  ['png', 'Chart Images (PNG)'],
  ['csv', 'Frequency Data CSV'],
  ['json', 'Full Results JSON'],
  ['txt', 'Summary Text Report'],
  ['polar_csv', 'Polar Directivity CSV'],
  ['impedance_csv', 'Impedance CSV'],
  ['vacs', 'ABEC Spectrum (VACS)'],
  ['stl', 'Waveguide STL'],
  ['fusion_csv', 'Fusion 360 CSV Curves'],
]);

export function openAutoExportPopup() {
  const settings = getCurrentSimulationManagementSettings();

  const backdrop = document.createElement('div');
  backdrop.className = 'ui-choice-backdrop';

  const dialog = document.createElement('div');
  dialog.className = 'ui-choice-dialog auto-export-dialog';
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');
  dialog.setAttribute('aria-label', 'Auto Export Settings');

  // Header
  const header = document.createElement('div');
  header.className = 'auto-export-header';

  const title = document.createElement('h4');
  title.className = 'ui-choice-title';
  title.textContent = 'Auto Export';
  header.appendChild(title);

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'auto-export-close';
  closeBtn.textContent = '\u00d7';
  closeBtn.title = 'Close (Escape)';
  header.appendChild(closeBtn);

  dialog.appendChild(header);

  // Body
  const body = document.createElement('div');
  body.className = 'auto-export-body';

  // Auto-export toggle
  const toggleRow = document.createElement('label');
  toggleRow.className = 'auto-export-toggle-row';
  const toggleCheckbox = document.createElement('input');
  toggleCheckbox.type = 'checkbox';
  toggleCheckbox.id = 'simmanage-auto-export';
  toggleCheckbox.checked = settings.autoExportOnComplete;
  toggleRow.appendChild(toggleCheckbox);
  const toggleLabel = document.createElement('span');
  toggleLabel.textContent = 'Auto-export on complete';
  toggleRow.appendChild(toggleLabel);
  body.appendChild(toggleRow);

  // Formats section
  const formatsLabel = document.createElement('div');
  formatsLabel.className = 'auto-export-section-label';
  formatsLabel.textContent = 'Export Formats';
  body.appendChild(formatsLabel);

  const formatsGrid = document.createElement('div');
  formatsGrid.className = 'auto-export-formats-grid';

  for (const formatId of SIMULATION_EXPORT_FORMAT_IDS) {
    const option = document.createElement('label');
    option.className = 'auto-export-format-option';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `simmanage-format-${formatId}`;
    checkbox.setAttribute('data-sim-management-format', formatId);
    checkbox.checked = settings.selectedFormats.includes(formatId);
    option.appendChild(checkbox);
    const text = document.createElement('span');
    text.textContent = FORMAT_LABELS.get(formatId) || formatId;
    option.appendChild(text);
    formatsGrid.appendChild(option);
  }

  body.appendChild(formatsGrid);

  // Output folder section
  const folderLabel = document.createElement('div');
  folderLabel.className = 'auto-export-section-label';
  folderLabel.textContent = 'Output Folder';
  body.appendChild(folderLabel);

  const folderRow = document.createElement('div');
  folderRow.className = 'auto-export-folder-row';

  const folderName = document.createElement('span');
  folderName.className = 'auto-export-folder-name';
  folderName.textContent = 'No folder selected';
  folderRow.appendChild(folderName);

  const chooseFolderBtn = document.createElement('button');
  chooseFolderBtn.type = 'button';
  chooseFolderBtn.className = 'secondary button-compact';
  chooseFolderBtn.textContent = 'Choose Folder';
  chooseFolderBtn.addEventListener('click', () => selectOutputFolder());
  folderRow.appendChild(chooseFolderBtn);

  body.appendChild(folderRow);
  dialog.appendChild(body);
  backdrop.appendChild(dialog);

  // Folder workspace subscription — updates label live
  const unsubscribe = subscribeFolderWorkspace(({ label }) => {
    folderName.textContent = label || 'No folder selected';
    const hasSelection = label && label !== 'No folder selected';
    chooseFolderBtn.textContent = hasSelection ? 'Change Folder' : 'Choose Folder';
  });

  // Close logic
  let releaseFocus;
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    window.removeEventListener('keydown', onKeyDown);
    if (releaseFocus) releaseFocus();
    unsubscribe();
    persistState();
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

  document.body.appendChild(backdrop);
  releaseFocus = trapFocus(dialog, { initialFocus: closeBtn });
}

function persistState() {
  const current = getCurrentSimulationManagementSettings();
  const autoExportEl = document.getElementById('simmanage-auto-export');
  const formatEls = Array.from(
    document.querySelectorAll('input[data-sim-management-format]'),
  );

  const selectedFormats = formatEls
    .filter((el) => el.checked)
    .map((el) => el.getAttribute('data-sim-management-format'))
    .filter(Boolean);

  saveSimulationManagementSettings({
    ...current,
    autoExportOnComplete: autoExportEl ? autoExportEl.checked : current.autoExportOnComplete,
    selectedFormats: selectedFormats.length > 0 ? selectedFormats : current.selectedFormats,
  });
}
