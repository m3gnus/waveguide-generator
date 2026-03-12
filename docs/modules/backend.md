# Backend Contract

## Scope

Primary files:

- `server/app.py`
- `server/api/*`
- `server/services/*`
- `server/solver/*`

## Responsibilities

- Expose mesh, solve, job, and diagnostics HTTP routes.
- Validate requests and runtime dependency state.
- Run OCC meshing and BEM solves.
- Persist job/task artifacts used by frontend task history.

## Stable Runtime Facts

- `POST /api/mesh/build` is the supported OCC-authored mesh endpoint.
- `POST /api/solve` requires canonical mesh shape plus valid OCC adaptive options when that strategy is used.
- Backend dependency/runtime truth lives in `server/solver/deps.py`.
- The backend runs as a standalone headless FastAPI service; the browser UI is optional and not required for backend startup.
- Maintained docs must match the current dependency matrix and supported fallback behavior.

## Operational References

- `server/README.md`
- `docs/PROJECT_DOCUMENTATION.md`
- `tests/TESTING.md`
