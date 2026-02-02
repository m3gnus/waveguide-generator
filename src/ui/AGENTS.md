# UI Module — AI Agent Context

## Purpose

User interface components for parameter controls, simulation, and file operations.

## Files

| File | Purpose | Complexity |
|------|---------|------------|
| `paramPanel.js` | Dynamic parameter controls | Medium |
| `simulationPanel.js` | BEM simulation UI | Medium |
| `fileOps.js` | File load/save operations | Simple |

## Public API

```javascript
import {
  ParamPanel,          // Parameter control panel
  SimulationPanel,     // Simulation controls
  loadConfigFile,      // Load MWG config
  saveConfigFile       // Save MWG config
} from './ui/';
```

## Parameter Panel

The parameter panel is generated dynamically from the schema:

```javascript
// Creates sliders, inputs, expressions based on schema
const panel = new ParamPanel('#param-container');
panel.buildForModel('R-OSSE');
```

## Key DOM Elements

| Element | Purpose |
|---------|---------|
| `#ui-panel` | Main UI panel container |
| `#param-container` | Parameter controls container |
| `#render-btn` | Update geometry button |
| `#load-config-btn` | Load config file |
| `#config-upload` | Hidden file input |
| `#display-mode` | Display mode dropdown |
| `#stats` | Geometry statistics display |

## For Simple Changes

1. Add UI element → modify HTML + wire up in `main.js`
2. Change param behavior → modify `paramPanel.js`
3. Update file handling → modify `fileOps.js`

## For Complex Changes

Before adding new UI features:
1. Follow existing pattern (event-driven)
2. Use CSS variables for theming
3. Test with different screen sizes

## Event Integration

```javascript
// Emit when parameter changes
AppEvents.emit('ui:paramChanged', { name, value });

// Listen for state updates
AppEvents.on('state:updated', (state) => {
  updateUIFromState(state);
});
```

## CSS Variables

The UI uses CSS custom properties for theming:

```css
--bg-color: #1a1a1a;
--panel-bg: #2a2a2a;
--text-color: #ffffff;
--accent-color: #4a9eff;
--border-color: #444;
```

## UI Patterns

1. **Sliders**: For numeric parameters with min/max
2. **Expression inputs**: For math expressions (R, a, b, s)
3. **Dropdowns**: For discrete choices (model type, display mode)
4. **Buttons**: For actions (render, export, load)

## Simulation Panel

The simulation panel provides:
- Frequency range inputs
- Simulation type selector
- Run/cancel buttons
- Progress indicator
- Results display link
