# Git workflow policy location

This file is a pointer, not a policy copy. The authoritative workspace rules
live in the separate `hornlab-policy` checkout:

- `hornlab-policy/AGENTS.md`: collaboration conventions.
- `hornlab-policy/GIT-WORKFLOW.md`: Git and GitHub safety policy.

Locate that checkout under the workspace root, check freshness, then read both
files before Git or GitHub work:

```bash
git -C <workspace>/hornlab-policy fetch -q origin &&
git -C <workspace>/hornlab-policy rev-list --count HEAD..origin/main
```

Zero means current; otherwise update the policy checkout and read it again.
If the checkout is unavailable, obtain the maintained policy before any Git
or GitHub write. Do not reconstruct rules from citations or historical copies.

Existing references to sections of `GIT-WORKFLOW.md` refer to that authoritative
document. Historical section numbers can differ; use its current rule text.
