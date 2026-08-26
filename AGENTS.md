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
