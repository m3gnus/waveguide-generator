# External Integrations

**Analysis Date:** 2026-02-25

## APIs & External Services

**Simulation/meshing stack (local runtime dependencies):**
- Gmsh Python API - OCC geometry meshing and legacy `.geo -> .msh`
  - Integration method: in-process Python API (`server/solver/waveguide_builder.py`, `server/solver/gmsh_geo_mesher.py`)
  - Fallback: system `gmsh` CLI for legacy mesher path when Python package missing
- bempp-cl - BEM solve runtime for `/api/solve`
  - Integration method: Python package import and operator assembly in solver modules
  - Device policy: `auto|opencl_cpu|opencl_gpu|numba` selection (`server/solver/device_interface.py`)

**Remote service checks:**
- GitHub remote `origin` - update status endpoint checks upstream repository state
  - Integration method: git subprocess calls in `server/services/update_service.py`
  - Expected remote: `https://github.com/m3gnus/waveguide-generator.git`

## Data Storage

**Databases:**
- Local backend persistence layer for jobs (`server/db.py`, `server/services/job_runtime.py`)
- No third-party hosted SQL/NoSQL integration present in current codebase map

**File Storage:**
- Local filesystem for generated artifacts and diagnostics output (`scripts/diagnostics/out/`)
- Export bundles assembled client-side with `jszip`; no cloud object storage integration found

**Caching:**
- In-memory job cache/queue in backend runtime (`jobs`, `job_queue`, `running_jobs`)

## Authentication & Identity

**Auth Provider:**
- None implemented for API routes in current backend
- CORS is open (`allow_origins=["*"]`) in `server/app.py`

## Monitoring & Observability

**Error tracking:**
- No external SaaS tracker (Sentry, Datadog, etc.) configured in repository

**Logs:**
- Python stdlib logging for backend (`logging.basicConfig` in `server/app.py`)
- Console logging for frontend tooling scripts

## CI/CD & Deployment

**Hosting:**
- Local dev run scripts are primary documented flow (`npm start`, `server/start.sh`)
- No deployment infrastructure manifests (Docker/K8s/Terraform) found in root

**CI Pipeline:**
- GitHub metadata exists (`.github/`), but integration contracts in this map are primarily local test commands

## Environment Configuration

**Development:**
- Frontend expects backend on `http://localhost:8000`
- Backend runtime managed via `.venv` and `server/requirements.txt`
- Optional OpenCL CPU setup helper: `scripts/setup-opencl-backend.sh`

**Production/operations:**
- Dependency matrix guardrails exposed through `/health`
- Runtime readiness gates enforced before solve/mesh operations

## Webhooks & Callbacks

**Incoming:**
- No third-party webhook endpoints identified

**Outgoing:**
- No outbound webhook publisher identified

---

*Integration audit: 2026-02-25*
*Update when adding/removing external services or hosted dependencies*
