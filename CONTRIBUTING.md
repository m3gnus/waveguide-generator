# Contributing

## Development Setup

1. Install Node dependencies:
   `npm ci`
2. (Optional) Set up Python backend environment:
   `python3 -m venv .venv && ./.venv/bin/pip install -r server/requirements.txt`
3. Start app:
   `npm start`

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
