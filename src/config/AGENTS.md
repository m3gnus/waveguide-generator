# Config Module — AI Agent Context

## Purpose

Parse, validate, and serialize MWG configuration files. Handles both block-format (R-OSSE) and flat dot-notation (OSSE) configs.

## Files

| File | Purpose | Complexity |
|------|---------|------------|
| `index.js` | Public API exports | Simple |
| `parser.js` | MWG config file parser | Medium |
| `schema.js` | Parameter schema definitions | Medium |
| `validator.js` | Parameter validation | Simple |
| `defaults.js` | Default parameter values | Simple |

## Public API

```javascript
import {
  MWGConfigParser,        // Parse MWG config files
  generateMWGConfigContent, // Export to MWG format
  PARAM_SCHEMA,          // Parameter definitions
  validateParams,        // Validate parameters
  getDefaults            // Get default values
} from './config/index.js';
```

## MWG Config Format

Two formats are supported:

**Block format (R-OSSE):**
```
[R-OSSE]
R = 140 * (1 - 0.5 * cos(p))
a = 25 * (1 - 0.3 * cos(p))
a0 = 15.5
```

**Dot notation (OSSE):**
```
OSSE.L = 120
OSSE.a = 48.5 - 10 * cos(p)
OSSE.a0 = 15.5
```

## Key Concepts

- **Expression parameters**: Can contain math (e.g., `140 * (1 - 0.5 * cos(p))`)
- **Number parameters**: Fixed numeric values
- **Schema**: Defines type, min, max, default for each parameter
- **Round-trip**: Parse → modify → export should preserve format

## For Simple Changes

1. Add parameter default → modify `defaults.js`
2. Change validation → modify `validator.js`
3. Update schema → modify `schema.js`

## For Complex Changes

Before modifying the parser:
1. Read existing parsing logic in `parser.js`
2. Test with example configs in `example scripts/`
3. Ensure round-trip works (parse → export → parse = same)

## Testing

```bash
npm test -- --grep "config"
```

## Example Usage

```javascript
// Parse config file
const config = MWGConfigParser.parse(fileContent);
// Returns: { modelType, params, morph, enclosure, mesh, ... }

// Validate parameters
const result = validateParams(params, 'R-OSSE');
// Returns: { valid: true/false, issues: [...] }

// Export config
const content = generateMWGConfigContent(params);
// Returns: MWG-format string
```

## Common Issues

- **Expression parsing**: Use `parseExpression()` from geometry module
- **Block detection**: Look for `[ModelName]` headers
- **Comment handling**: Lines starting with `#` or `;`
