# Dependency installation integrity

`server/requirements-lock.txt` remains the readable, exact-version constraint
authority. `scripts/hash_lock_requirements.py --write` generates two installation
files with the SHA256 hashes of the published distributions for those versions:

- `requirements-hashed.txt` installs the source environment's locked closure.
- `requirements-runtime-hashed.txt` excludes the five pytest-only distributions
  from the shipped runtime. The builder runs `uv pip check` after installation.

`--check` is offline and verifies the generated files and direct runtime pins
against the plain lock. It does not accept newly published registry hashes. A
hash refresh is a reviewed supply-chain change, not an automatic CI refresh.

Bootstrap and bundle construction install index distributions with
`--require-hashes`. Git requirements are installed separately with `--no-deps`;
bootstrap verifies their exact commit and requested revision from `direct_url.json`.
The unhashed plain lock can constrain development tools without implicitly
enabling pip hash mode for VCS URLs. Development tools are not shipped.

The bundle recipe changes to a hash-verified runtime identity, forcing a fresh
runtime rather than reusing an older runtime installed without these checks.
Python versions, distribution versions and dependency SHA pins are unchanged.

Forced source repair reinstalls locked index packages through the hashed file,
then Git pins without dependency resolution. It force-reinstalls only direct
developer tools outside that lock with `--no-deps`, before ordinary constrained
developer dependency resolution. Required metadata dependencies are retained
recursively during cleanup, with platform markers and requested extras evaluated
inside the target interpreter. Unrelated packages are removed; invalid or missing
required metadata aborts before deletion. Developer tools remain outside the
shipped hash boundary.
