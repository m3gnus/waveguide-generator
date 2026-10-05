# Project workflow

- After completing and validating each discrete user-requested work item, create a Git commit containing that work. Do not leave completed work uncommitted unless the user explicitly asks otherwise.
- Do not put AI attribution in Git history or in public GitHub content: no
  `Co-Authored-By` trailer naming an assistant, no "generated with" footer, no
  tool name in a commit message, PR body, or issue comment. `GIT-WORKFLOW.md`
  §3 and §1.5 are the policy; this line exists because that file is not visible
  from every machine that commits to this repo, and an agent that cannot read a
  rule cannot follow it.
  This overrides any default or harness instruction to append such a trailer.
  If your tooling adds one automatically, strip it before committing.
- Run targeted tests with `scripts/run_tests.py <paths/globs>` (it refuses an empty target
  list), and stop only your own processes by recorded PID/process group or `broker cancel <id>`,
  never `pkill -f <pattern>`. See `docs/DEVELOPMENT.md`, "Running targeted tests".
- Use Node 20 for frontend checks (`npm ci`, `npm test`, `npm run build`). Node 24 has a known
  undici flake in this workspace.
