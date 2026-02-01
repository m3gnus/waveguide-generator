# Presets Module — AI Agent Context

## Purpose

Manage horn design presets for quick starting points and reproducibility.

## Files

| File | Purpose | Complexity |
|------|---------|------------|
| `index.js` | Public API, preset storage | Medium |

## Public API

```javascript
import {
  loadPreset,          // Load preset by name
  savePreset,          // Save current design as preset
  listPresets,         // Get all available presets
  deletePreset,        // Remove preset
  importPresets,       // Import from JSON
  exportPresets        // Export to JSON
} from './presets/index.js';
```

## Preset Structure

```javascript
{
  name: "Studio Monitor Horn",
  description: "60x40 degree coverage, smooth response",
  modelType: "OSSE",
  params: {
    L: 120,
    a: "48.5 - 10 * cos(p)",
    a0: 15.5,
    // ...
  },
  mesh: {
    subdomain: 8,
    throatRes: 12,
    mouthRes: 24
  },
  solver: {
    frequencyStart: 500,
    frequencyEnd: 20000,
    numFrequencies: 50
  },
  metadata: {
    author: "Magnus",
    created: "2026-01-30",
    tags: ["studio", "monitor", "60x40"]
  }
}
```

## Built-in Presets

| Name | Model | Coverage | Use Case |
|------|-------|----------|----------|
| Default OSSE | OSSE | Variable | General purpose |
| Default R-OSSE | R-OSSE | Variable | Curved profile |
| CD Horn 60x40 | OSSE | 60°x40° | Studio monitors |
| PA Horn 90x40 | R-OSSE | 90°x40° | PA systems |

## For Simple Changes

1. Add built-in preset → add to presets array
2. Change preset fields → modify schema
3. Update storage → modify save/load functions

## Storage

Presets are stored in:
- **Built-in**: Hardcoded in module
- **User**: localStorage (browser)
- **Shared**: JSON files (import/export)

## Example Usage

```javascript
// Load a preset
const preset = loadPreset("CD Horn 60x40");
GlobalState.update(preset.params, preset.modelType);

// Save current design as preset
savePreset({
  name: "My Custom Horn",
  description: "Optimized for my application",
  params: GlobalState.get().params,
  // ...
});

// Export for sharing
const json = exportPresets(["My Custom Horn"]);
downloadFile(new Blob([json]), "my-presets.json");
```

## Event Integration

```javascript
// Preset loaded
AppEvents.emit('preset:loaded', { name, params });

// Preset saved
AppEvents.emit('preset:saved', { name });
```
