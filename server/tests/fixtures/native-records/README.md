# Frozen native source-contour records

Two prepared-mesh records with `producer: "native"`, as WG 0.3.6/0.3.7 published them,
each with its retained solver mesh (gzipped). `test_native_record_fixture.py` places
them in a fresh registry and checks that they still admit, refuse a contradicting
excitation and dispatch to Metal. WG no longer has the ingestion that wrote them
(removed in f61e1ece3), so these files are frozen: never regenerate or edit them to
make a test pass. A change that breaks them breaks users' saved records.

- `single-channel/`: a flat 24 mm piston in a 30 mm mouth, 20 mm horn, one channel
  `motor` with `{"piston": 1}`.
- `shared-assembly/`: a flat 4 mm horn source and a flat 8 mm woofer on one baffle,
  channels `hf` (normal) and `lf` (axial).

## How they were made

At commit 6d9bcc9d2 (the last with `server/cadlink/intake/native_source.py`), in a
scratch detached worktree, with that tree on `PYTHONPATH` and the 8d38e959e gate venv:

```
python generate.py out      # ingests both cases, writes out/<case>/{record.json,solve.msh}
python freeze.py out <this folder>
```

The cases are the flat ones of the deleted `test_native_source_contour.py` and
`test_shared_source_assembly.py`, with coarser mesh sizes to keep the files small.
`freeze.py` changes only the two store paths, which named the throwaway data folder:
they become `<bundle>` and `<bundle>/solve.msh`, and the test points them at its own
copy. Every other field, including `report_sha256` and `solve_model_sha256`, is as
the ingestion sealed it (WG does not re-verify `report_sha256` on read).

### generate.py

```python
"""Generate the frozen native-record fixtures at 6d9bcc9d2 (run from that tree)."""
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sys
import tempfile

from hornlab_mesher.assembly_artifact import export_assembly
from hornlab_mesher.contour_artifact import export_contour
from hornlab_mesher.source_assembly import SourceAssembly
from hornlab_mesher.source_contour import ContourDrive, flat
from server.cadlink.intake.native_source import ingest_native_source, read_native_source
from server.cadlink.store import CadLinkStore

out = Path(sys.argv[1])


def ingest(artifact, root, sizes):
    store = CadLinkStore(root / "registry.sqlite")
    try:
        store.initialize()
        return ingest_native_source(artifact, store=store, data_dir=root, sizes=sizes)
    finally:
        store.close()


def save(name, record):
    target = out / name
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(record["mesh_store_path"], target / "solve.msh")
    (target / "record.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    # Single channel: the flat case of the deleted test_native_source_contour.
    model = flat(24)
    moving = [s.id for s in model.segments if s.role == "moving"]
    export_contour(model, ContourDrive("motor", tuple((m, 1) for m in moving)), tmp / "single",
                   mouth_radius_mm=30, length_mm=20, mesh_size_mm=2)
    _, model, _, _ = read_native_source(tmp / "single")
    save("single-channel", ingest(tmp / "single", tmp / "a", {
        "rigid_size_mm": 10, "transition_mm": 8,
        "source_size_mm": {s.id: 4 for s in model.segments if s.role == "moving"},
    }))
    # Shared horn + woofer: the flat case of the deleted test_shared_source_assembly.
    horn = replace(flat(4), physical_source_id="horn", rim_id="horn.rim")
    woofer = replace(flat(8), physical_source_id="woofer", rim_id="woofer.rim")
    assembly = SourceAssembly(horn, woofer, 70, 90, 40, 7, (-13, 17), 24, 10, (9, -15), 8)
    drives = [
        ContourDrive("hf", tuple((s.id, 1) for s in horn.segments if s.role == "moving")),
        ContourDrive("lf", tuple((s.id, 1) for s in woofer.segments if s.role == "moving"), "axial"),
    ]
    export_assembly(assembly, drives, tmp / "shared", mesh_size_mm=3)
    manifest, _, _, _ = read_native_source(tmp / "shared")
    save("shared-assembly", ingest(tmp / "shared", tmp / "b", {
        "rigid_size_mm": 8, "transition_mm": 6,
        "source_size_mm": {p["id"]: 4 for p in manifest["patches"] if p["role"] == "moving"},
    }))
```

### freeze.py

```python
"""Turn out/<name>/{record.json,solve.msh} into the checked-in fixture form."""
import gzip
import json
from pathlib import Path
import sys

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
for case in sorted(p for p in src.iterdir() if p.is_dir()):
    record = json.loads((case / "record.json").read_text())
    assert record["mesh_store_path"] == record["bundle_store_path"] + "/solve.msh"
    record["bundle_store_path"] = "<bundle>"
    record["mesh_store_path"] = "<bundle>/solve.msh"
    target = dst / case.name
    target.mkdir(parents=True, exist_ok=True)
    (target / "record.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    with open(target / "solve.msh.gz", "wb") as raw, gzip.GzipFile(
        filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0
    ) as zipped:
        zipped.write((case / "solve.msh").read_bytes())
```
