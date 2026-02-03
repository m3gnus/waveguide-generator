export function setupSmoothingListener(panel) {
  const smoothingSelect = document.getElementById('smoothing-select');
  if (smoothingSelect) {
    smoothingSelect.addEventListener('change', (e) => {
      panel.currentSmoothing = e.target.value;
      if (panel.lastResults) {
        panel.displayResults(panel.lastResults);
      }
    });
  }
}

export function setupKeyboardShortcuts(panel) {
  document.addEventListener('keydown', (e) => {
    // Check if Ctrl (or Cmd on Mac) + Shift is pressed
    const isModifier = (e.ctrlKey || e.metaKey) && e.shiftKey;

    if (isModifier) {
      let smoothingType = null;

      switch (e.key) {
        case '1':
          smoothingType = '1/1';
          break;
        case '2':
          smoothingType = '1/2';
          break;
        case '3':
          smoothingType = '1/3';
          break;
        case '6':
          smoothingType = '1/6';
          break;
        case '7':
          smoothingType = '1/12';
          break;
        case '8':
          smoothingType = '1/24';
          break;
        case '9':
          smoothingType = '1/48';
          break;
        case 'X':
        case 'x':
          smoothingType = 'variable';
          break;
        case 'Y':
        case 'y':
          smoothingType = 'psychoacoustic';
          break;
        case 'Z':
        case 'z':
          smoothingType = 'erb';
          break;
        default:
          break;
      }

      if (smoothingType) {
        e.preventDefault();

        // Toggle: if already selected, remove smoothing
        if (panel.currentSmoothing === smoothingType) {
          smoothingType = 'none';
        }

        panel.currentSmoothing = smoothingType;
        const smoothingSelect = document.getElementById('smoothing-select');
        if (smoothingSelect) {
          smoothingSelect.value = smoothingType;
        }

        if (panel.lastResults) {
          panel.displayResults(panel.lastResults);
        }
      }
    }

    // Ctrl+0 to remove smoothing
    if ((e.ctrlKey || e.metaKey) && e.key === '0' && !e.shiftKey) {
      e.preventDefault();
      panel.currentSmoothing = 'none';
      const smoothingSelect = document.getElementById('smoothing-select');
      if (smoothingSelect) {
        smoothingSelect.value = 'none';
      }

      if (panel.lastResults) {
        panel.displayResults(panel.lastResults);
      }
    }
  });
}
