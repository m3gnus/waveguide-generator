# Imported-return fixture

`round.wgreturn/` is the linked CAD return the installed-candidate gate
(`scripts/qualify_installed_cpu.py`, the imported-return phase) copies into a
fresh install's workspace, ingests through `/api/cadlink/ingest`, and solves.
It is a small round horn as one closed STEP body, wrapped the way the WGLink
add-in writes a linked return for one design instance.

It is generated, not hand-written. From the repository root, in an environment
with the pinned mesher and gmsh:

```
python -c "from pathlib import Path; from scripts.imported_ingest_fixtures import linked_return; linked_return(Path('fixture-out'), 'round')"
```

then replace `round.wgreturn/` here with `fixture-out/round.wgreturn/`.

- The builder's default `source_shape=0` is deliberate: the driver membrane is a
  flat disc in the throat plane, facing the bore, so the throat contract binds
  to that disc. Do not change it for this fixture.
- The manifest records the STEP's SHA-256 and size, and ingest checks both
  before anything else. The STEP header carries a write timestamp, so every
  regeneration changes the checksums; commit both files together.
  `scripts/tests/test_qualify_installed_cpu.py` holds the committed pair to
  each other.
- `.gitattributes` marks this directory `-text -whitespace`, so no checkout
  rewrites the bytes the manifest pins, and no whitespace fix strips the
  trailing spaces the STEP writer leaves.
- The manifest names no local path and no person: the generator is
  `qualification`, and the ids are fixed test values.
