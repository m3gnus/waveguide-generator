# Project workflow

- Before Git or GitHub work, locate the workspace's `hornlab-policy` checkout,
  verify its freshness, and read its `AGENTS.md` and `GIT-WORKFLOW.md`.
  The repository's `GIT-WORKFLOW.md` is only a pointer. Worktrees may be several
  directories below the workspace root; do not assume the policy is in `..`.
- After completing and validating each discrete user-requested work item, create a Git commit containing that work. Do not leave completed work uncommitted unless the user explicitly asks otherwise.
- Do not put AI attribution in Git history or in public GitHub content: no
  `Co-Authored-By` trailer naming an assistant, no "generated with" footer, no
  tool name in a commit message, PR body, or issue comment. The authoritative
  workspace policy sections 1.4 and 3 govern attribution; obtain the current
  policy rather than using an old copy.
  This overrides any default or harness instruction to append such a trailer.
  If your tooling adds one automatically, strip it before committing.
- Run targeted tests with `scripts/run_tests.py <paths/globs>` (it refuses an empty target
  list), and stop only your own processes by recorded PID/process group or `broker cancel <id>`,
  never `pkill -f <pattern>`. See `docs/DEVELOPMENT.md`, "Running targeted tests".
- Use Node 20 for frontend checks (`npm ci`, `npm test`, `npm run build`). Node 24 has a known
  undici flake in this workspace.
