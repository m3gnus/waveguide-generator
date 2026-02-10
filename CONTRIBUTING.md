# Contributing

## Development Setup

1. Install all dependencies:
   ```bash
   bash install/install.sh
   ```
   Or manually:
   ```bash
   npm ci
   python3 -m venv .venv && .venv/bin/pip install -r server/requirements.txt
   ```

2. Start the app:
   ```bash
   npm start
   ```

## Branches and Commits

- Create focused branches from `main`.
- Keep commits small and descriptive.
- Reference related issue numbers when relevant.

## Test Requirements

Before opening a pull request, run:

```bash
npm test
npm run test:server
npm run build
```

## Pull Requests

- Explain what changed and why.
- Include screenshots/GIFs for UI changes.
- Note any solver/backend behavior changes.
- Call out follow-up work explicitly.
