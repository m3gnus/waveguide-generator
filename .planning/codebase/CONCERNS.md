# Codebase Concerns

**Analysis Date:** 2026-02-25

## Tech Debt

**Dual meshing paths increase cognitive load:**
- Issue: OCC parameterized meshing and legacy `.geo -> .msh` pipeline coexist
- Why: Backward compatibility and tooling/tests still rely on legacy path
- Impact: Higher risk of behavior drift or incorrect assumptions in docs/features
- Fix approach: Continue converging workflows on OCC path and explicitly scope legacy usage

**Broad frontend-backend contract surface:**
- Issue: Surface tags, symmetry handling, and mesh semantics are distributed across many modules
- Why: Pipeline evolved across frontend geometry and backend solver validation layers
- Impact: Small local edits can cause cross-stack regressions
- Fix approach: Preserve required parity-test map and tighten contract fixtures over time

## Known Fragile Areas

**Contract-critical files listed in `AGENTS.md`:**
- Why fragile: They anchor payload/tag semantics and export/solve behavior
- Common failures: Tag mismatches, missing source triangles, interface-tag drift, export parity regressions
- Safe modification: Change with paired frontend/backend validation and required targeted tests

**Backend dependency matrix gates (`server/solver/deps.py`):**
- Why fragile: Runtime support tightly constrained (`python`, `gmsh`, `bempp` ranges)
- Common failures: Environment version mismatches causing `503` paths
- Safe modification: Update matrix, runtime checks, and related tests in one change

## Security Considerations

**Open CORS policy in backend (`allow_origins=["*"]`):**
- Risk: Broad cross-origin access in default config
- Current mitigation: Local/developer-focused runtime assumptions
- Recommendation: Introduce environment-based restrictive CORS for shared/staging deployments

**Update endpoint shells out to git commands:**
- Risk: Operational reliability and repo-state assumptions (remote presence/network)
- Current mitigation: Error mapping to controlled API responses
- Recommendation: Keep command input fixed and maintain tight tests around failure modes

## Performance Bottlenecks

**Solver runtime cost at scale:**
- Problem: BEM solve and directivity operations are numerically heavy
- Evidence: Existing roadmap and dedicated acceleration plans in docs
- Cause: CPU/OpenCL runtime constraints and mesh complexity growth
- Improvement path: Follow acceleration roadmap (FMM/device policy/OpenCL maturity)

**Mesh resolution sensitivity:**
- Problem: Higher axial/angular resolution can materially increase meshing/solve time
- Cause: Triangle growth and more expensive operator assembly
- Improvement path: Keep adaptive resolution controls and profile representative scenarios

## Test Coverage Gaps / Operational Risks

**Runtime-specific behavior remains environment dependent:**
- What's risky: Some behaviors only surface with actual gmsh/bempp/OpenCL combinations
- Priority: High for deployment confidence
- Mitigation: Maintain dependency runtime tests and smoke checks against target environments

---

*Concerns audit: 2026-02-25*
*Update as known risks are mitigated or new constraints appear*
