# Development Roadmap — ATH Horn Design Platform

## Completed Phases

### Phase 0: Stabilization & Module Extraction ✅

- [x] Create `src/` directory structure
- [x] Extract geometry functions to modules
- [x] Extract config parser
- [x] Extract Three.js viewer
- [x] Extract export functions
- [x] Create event bus
- [x] Verify app behavior identical to before

### Phase 1: Config Robustness & Schema System ✅

- [x] Define complete `PARAM_SCHEMA`
- [x] Add parameter validation
- [x] Add defaults system
- [x] Config round-trip tests
- [x] Test against example configs
- [x] Add undo/redo system
- [x] Add localStorage auto-save

### Phase 2: Enhanced Geometry & OS-GOS ✅

- [x] Implement OS-GOS horn model
- [x] Improve morphing (TargetWidth, TargetHeight, Rate curves)
- [x] Add `Mesh.SubdomainSlices` support
- [x] Add variable mesh density (ThroatResolution, MouthResolution)
- [x] Cross-section profile view
- [x] Dimension annotations

### Phase 3: Export Suite ✅

- [x] Gmsh .geo export with Physical Surface tags
- [x] CSV profile export
- [x] ATH config round-trip
- [x] Batch export functionality

### Phase 4: BEM Solver Integration ⚠️ (60%)

- [x] Create Python backend (FastAPI)
- [x] Basic bempp-cl integration
- [x] HTTP client in browser
- [x] Mesh conversion pipeline
- [x] Mock solver for testing
- [ ] **TODO**: Real BEM boundary conditions
- [ ] **TODO**: Mesh quality validation
- [ ] **TODO**: Result validation vs ABEC
- [ ] **TODO**: Frequency-dependent mesh refinement

### Phase 5: Optimization & Batch Processing ✅

- [x] Parameter sweep generator
- [x] Batch solve queue
- [x] Acoustic quality scoring
- [x] Optimization engine (grid, random, coordinate descent)
- [x] Parameter space management
- [x] Result storage

### Phase 6: Production Readiness ✅

- [x] Workflow state machine
- [x] Preset management
- [x] Validation framework
- [x] Module structure cleanup

### Phase 7: AI-Assisted Design 🔄 (20%)

- [x] Module structure (knowledge, surrogate, optimization, insights)
- [x] Stub implementations
- [x] API design
- [ ] **TODO**: Knowledge capture with real data
- [ ] **TODO**: Surrogate model training
- [ ] **TODO**: Bayesian optimization implementation
- [ ] **TODO**: Insight generation from real metrics

---

## Upcoming Work

### Priority 1: Complete BEM Solver (Phase 4)

**Goal**: Get real physics working in the BEM solver

Tasks:
1. Fix bempp-cl boundary condition setup
2. Validate mesh quality before simulation
3. Compare results against ABEC references
4. Implement frequency-dependent mesh refinement

**Blocked by**: Nothing (can start immediately)

### Priority 2: Results Visualization

**Goal**: Better display of simulation results

Tasks:
1. Polar plot (SVG) for directivity
2. Frequency response chart
3. Sonogram/directivity map
4. Impedance plot

**Blocked by**: Working BEM solver (for real data)

### Priority 3: AI Module Implementation (Phase 7)

**Goal**: Implement actual AI-assisted design features

Tasks:
1. Store design knowledge from completed simulations
2. Train simple surrogate model (polynomial regression first)
3. Implement Bayesian optimization with acquisition function
4. Generate insights from sensitivity analysis

**Blocked by**: Working BEM solver (for training data)

---

## Future Enhancements (Not Scheduled)

- ABEC project file export
- OBJ export for CAD
- Mobile-responsive UI
- Multi-horn comparison view
- Cloud preset sync
- Real-time collaboration

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0-alpha-7.5 | 2026-01 | Phase 6 complete, Phase 7 stubs |
| 1.0.0-alpha-7.0 | 2026-01 | AI module structure |
| 1.0.0-alpha-6.0 | 2025-12 | Production readiness |
| 1.0.0-alpha-5.0 | 2025-11 | Optimization engine |
| 1.0.0-alpha-4.0 | 2025-10 | BEM solver basics |
| 1.0.0-alpha-3.0 | 2025-09 | Export suite |
| 1.0.0-alpha-2.0 | 2025-08 | OS-GOS, morphing |
| 1.0.0-alpha-1.0 | 2025-07 | Config schema |
| 1.0.0-alpha-0.0 | 2025-06 | Module extraction |
