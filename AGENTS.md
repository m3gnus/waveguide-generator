# AI Agent Instructions - ATH Horn Design Platform

> This file provides context for AI agents working on this project. For tool-specific configs, see `.claude/`, `.gemini/`, `.cline/`, etc.

## Quick Context

**What is this?** A browser-based acoustic horn design and simulation platform.

**Tech stack:** JavaScript (ES modules), Three.js, Python/FastAPI backend, bempp-cl BEM solver.

**Current version:** 1.0.0-alpha-7.5

## Agent Roles (Recommended)

This project benefits from specialized agent roles:

| Role | Responsibility | Suggested Use |
|------|----------------|---------------|
| **Architect** | Design systems, plan implementations | Complex features, refactoring |
| **Implementer** | Write code following plans | After architect creates plan |
| **Reviewer** | Check code, find issues | After implementation, fresh context |
| **Quick-fix** | Simple tasks, formatting | Local/lightweight agents |

## Domain Documentation

Each `src/` subfolder has its own `AGENTS.md` with domain-specific context:

- `src/geometry/AGENTS.md` — Horn math, mesh generation
- `src/config/AGENTS.md` — ATH config parsing, validation
- `src/viewer/AGENTS.md` — Three.js 3D visualization
- `src/export/AGENTS.md` — File format exports
- `src/solver/AGENTS.md` — BEM acoustic simulation
- `src/ui/AGENTS.md` — User interface components
- `src/optimization/AGENTS.md` — Parameter optimization
- `src/ai/AGENTS.md` — AI-assisted design features
- `src/workflow/AGENTS.md` — Workflow state machine
- `src/presets/AGENTS.md` — Preset management
- `src/validation/AGENTS.md` — Reference validation

## For Less Capable Agents (Local LLMs)

If you're a smaller/local model:

1. **Focus on one file at a time** — Don't try to understand the whole system
2. **Read the domain AGENTS.md first** — It has focused context
3. **Follow existing patterns** — Copy similar code nearby
4. **Ask for clarification** — Better to ask than guess
5. **Make small changes** — Don't refactor unless asked

## For More Capable Agents

If you're a powerful model (Claude, GPT-4, etc.):

1. **Read ARCHITECTURE.md** for full system understanding
2. **Check plan/ folder** for current project status
3. **Use the event bus pattern** — Don't bypass with direct imports
4. **Keep files under 300 lines** — Split if approaching limit
5. **Add JSDoc comments** to public functions

## Key Files

| File | Purpose |
|------|---------|
| `ARCHITECTURE.md` | Detailed architecture (1100+ lines) |
| `AI_GUIDANCE.md` | Phase 7 AI system documentation |
| `AGENT_INSTRUCTIONS.md` | Quick start guide |
| `plan/STATUS.md` | Current project status |
| `plan/ROADMAP.md` | What's done and what's next |

## Running the Project

```bash
# Start dev server
npm run dev

# Run tests
npm test
npm run test:e2e

# Start BEM backend (optional)
cd server && python app.py
```

## Communication Pattern

All modules communicate via the event bus:

```javascript
import { AppEvents } from './events.js';

// Emit
AppEvents.emit('geometry:updated', { mesh, params });

// Listen
AppEvents.on('geometry:updated', (data) => { ... });
```

## State Management

```javascript
import { GlobalState } from './state.js';

GlobalState.get();                    // Get current state
GlobalState.update({ r0: 15 }, 'R-OSSE');  // Update state
GlobalState.undo();                   // Undo last change
```

## Before Making Changes

1. Read the relevant domain `AGENTS.md`
2. Check `plan/STATUS.md` for current focus
3. Run existing tests to ensure they pass
4. Make changes incrementally
5. Run tests after changes
