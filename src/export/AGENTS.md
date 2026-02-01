# Export Module — AI Agent Context

## Purpose

Generate output files in various formats from horn geometry and parameters.

## Files

| File | Purpose | Complexity |
|------|---------|------------|
| `index.js` | Public API exports | Simple |
| `stl.js` | Binary/ASCII STL export | Simple |
| `athConfig.js` | ATH config file export | Medium |
| `csv.js` | CSV profile data export | Simple |
| `msh.js` | Gmsh .geo file export | Medium |
| `profiles.js` | Profile extraction utilities | Simple |

## Public API

```javascript
import {
  exportSTL,            // Export STL mesh
  exportATHConfig,      // Export ATH config file
  exportCSV,            // Export profile CSV
  exportGmsh,           // Export Gmsh .geo file
  getProfileData        // Extract profile coordinates
} from './export/index.js';
```

## Output Formats

| Format | Extension | Use Case |
|--------|-----------|----------|
| STL | `.stl` | 3D printing, CAD import |
| ATH Config | `.txt` | ATH software compatibility |
| CSV | `.csv` | Profile data for analysis |
| Gmsh | `.geo` | BEM mesh generation |

## For Simple Changes

1. Change STL format → modify `stl.js`
2. Add CSV column → modify `csv.js`
3. Update config format → modify `athConfig.js`

## For Complex Changes

Before adding a new export format:
1. Create new file (e.g., `newFormat.js`)
2. Export function from `index.js`
3. Add button in `index.html`
4. Wire up in `src/main.js`

## Example Usage

```javascript
// Export STL
const stlBlob = exportSTL(mesh, { binary: true });
downloadFile(stlBlob, 'horn.stl');

// Export ATH config
const configText = exportATHConfig(params);
downloadFile(new Blob([configText]), 'horn.txt');

// Export CSV
const csvText = exportCSV(profileData);
downloadFile(new Blob([csvText]), 'profile.csv');
```

## Gmsh Export Details

The Gmsh exporter generates `.geo` files with:
- Point definitions for profile vertices
- Spline curves connecting points
- Surface definitions
- Physical groups for boundary conditions (throat, horn wall, mouth)

## Key DOM Elements

- `#export-btn` — STL export
- `#export-config-btn` — ATH config export
- `#export-csv-btn` — CSV export
- `#export-geo-btn` — Gmsh export
- `#export-prefix` — Filename prefix
- `#export-counter` — Auto-increment counter
