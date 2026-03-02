---
phase: quick
plan: 11
type: execute
autonomous: true
wave: 1
depends_on: []
---

<objective>
Set default mesh resolution values to throat: 6, mouth: 15, front: 25, back: 40, rear: 40 across frontend defaults and backend request defaults so new sessions and omitted fields use the same baseline.
</objective>

<context>
- Mesh resolution defaults are currently inconsistent across UI schema, config defaults, payload fallbacks, and backend model defaults.
- User requested explicit new defaults for five values.
- We should keep changes scoped to defaults only, with parity tests for payload and backend validation contracts.
</context>

<tasks>
  <task id="1" type="auto">
    <files>
      - src/config/schema.js
      - src/config/index.js
      - src/solver/waveguidePayload.js
      - src/app/exports.js
      - server/models.py
    </files>
    <action>
      Update default/fallback mesh resolution values to throat=6, mouth=15, rear=40, front=25, back=40 in all default providers used by app export and backend request parsing.
    </action>
    <verify>
      <automated>node --test tests/waveguide-payload.test.js tests/export-gmsh-pipeline.test.js</automated>
      <automated>cd server && python3 -m unittest tests.test_api_validation</automated>
    </verify>
    <done>
      New default mesh resolution values are consistently applied for new params and missing request fields.
    </done>
  </task>

  <task id="2" type="auto">
    <files>
      - .planning/quick/11-set-the-default-mesh-resolution-to-throa/11-SUMMARY.md
      - .planning/STATE.md
    </files>
    <action>
      Record the quick-task outcome in summary and append an entry to STATE.md quick tasks table with commit hash.
    </action>
    <verify>
      <automated>rg -n "Quick Task 11|set-the-default-mesh-resolution-to-throa|Completed quick task 11" .planning/quick/11-set-the-default-mesh-resolution-to-throa/11-SUMMARY.md .planning/STATE.md</automated>
    </verify>
    <done>
      Planning/state artifacts reflect completion and are ready for the docs quick-task commit.
    </done>
  </task>
</tasks>

<success_criteria>
- Frontend defaults match requested values.
- Backend request defaults match requested values.
- Targeted JS and server tests pass.
- Quick task summary and STATE.md are updated.
</success_criteria>
