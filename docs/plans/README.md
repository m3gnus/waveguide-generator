# Design plans and implementation records

Reviewed for planning status on 2026-10-16. These documents preserve public
contracts, accepted decisions and proposals. Their paths stay stable for
existing links. A completed design is not a new implementation task, and
source implementation is not installed or hardware qualification. Dated
verification applies only to the artifact and environment it names.

| Document | Classification | Remaining work or reading guidance |
|---|---|---|
| [Standalone application](STANDALONE-APP.md) | Implemented design; historical verification | Current installation and update contracts are linked at the top. Graphical Linux installation is a future idea. |
| [Engine selection contract](ENGINE-SELECTION-CONTRACT.md) | Enacted decision | C1–C4 complete; retain the implementation differences and migration behavior. |
| [External STEP isolation](STEP-PARSER-ISOLATION.md) | Implemented boundary and accepted gate | Retain the documented enforcement approximations and dated platform qualification gaps. |
| [Crossovers and driver library](CADLINK-CROSSOVER-DRIVERS.md) | Implemented slices 1–5 | Slice 6 optimisation and add-in parity remain proposed. Bundled and user libraries coexist. |
| [CAD Link FEM regions](CADLINK-FEM-REGIONS.md) | Implemented contract plus qualification follow-ups | F10/F10a are implemented; F11 has scoped CPU references; F12 live Fusion and GPU qualification remain distinct. F9 storage efficiency is a future improvement. |
| [Per-channel rear loads](DRIVER-REAR-LOAD.md) | Proposal partly overlapped by later implementation | Sealed BEAT rear loading and meshed FEM chambers exist. Lossy/vented unification still needs a bounded design and dependency check. |

For current product behavior, start with the [documentation index](../README.md),
[CAD operations](../architecture/CAD-OPERATIONS.md),
[install guide](../INSTALL.md) and [release guide](../RELEASING.md).
Future work should identify a concrete user need and acceptance check before
becoming an implementation commitment.
