# Canonical JSON byte fixtures

Captured from the original code before migration. `goldens.json` records the
baseline Git revision, the pinned protocol hash, each original source expression,
and exact UTF-8 bytes (hex) plus SHA-256 for every successful case. Rejection
cases record their exception type. Expected outputs must not be regenerated
from the shared owner. Input recipes are in `test_canonical_json.py`.

The inventory was checked with `git grep` and an AST scan, including multiline
calls: seven former helpers, 18 server inline sites, and three script inline
sites. Unsorted wire serializers and indented human-readable JSON are outside
this identity dedupe. The pinned WGLink protocol remains byte-for-byte unchanged.

All profiles use sorted keys, compact comma/colon separators, standard-library
float formatting, UTF-8 byte encoding, and no trailing newline. `ascii_json`
permits NaN/infinity; `ascii_strict_json` rejects them; `utf8_strict_json` also
leaves Unicode unescaped; `ascii_string_json` permits non-finite numbers and uses
`default=str`. The normalized bytes variant retains dataclass, enum, mapping,
tuple, and numpy-like normalization. Byte variants encode the named text profile.
The public exports normalization function remains a compatibility alias.

Each inline golden tests the live serializer expression after payload preparation.
Existing caller tests cover envelope construction and persisted identities.
The installer qualification script loads the owner by file to retain its ability
to run with only the standard library, bypassing the contracts package initializer.

| Current site | Former helper / containing function | Shared variant |
| --- | --- | --- |
| `scripts/beat_conformance/legacy_compiled_inputs.py:100` | `verify_contract` | `canonical_ascii_json` |
| `scripts/qualify_installer_artifact.py:352` | `entries_digest` | `canonical_ascii_json` |
| `scripts/verify_model_identity.py:125` | `canonical_bytes` | `canonical_ascii_strict_json` |
| `server/cadlink/intake/returns_folder.py:174` | `return_inventory [1]` | `canonical_ascii_strict_json` |
| `server/cadlink/intake/returns_folder.py:182` | `return_inventory [2]` | `canonical_ascii_strict_json` |
| `server/cadlink/domain_decision.py:65` | `_canonical` | `canonical_normalized_bytes` |
| `server/cadlink/fusion_outcomes.py:192` | `_log_last_request` | `canonical_ascii_json` |
| `server/cadlink/fusion_status.py:345` | `_fingerprint_hash` | `canonical_ascii_json` |
| `server/cadlink/ingest.py:19` | `_canonical` | `canonical_normalized_bytes` |
| `server/cadlink/native_source.py:16` | `_canonical` | `canonical_ascii_strict_bytes` |
| `server/cadlink/onshape/return_leg.py:21` | `_canonical` | `canonical_ascii_strict_bytes` |
| `server/cadlink/operations.py:14` | `canonical_json` | `canonical_utf8_strict_json` |
| `server/cadlink/step_evidence.py:21` | `_canonical` | `canonical_ascii_strict_bytes` |
| `server/charts/api.py:157` | `_key` | `canonical_ascii_json` |
| `server/contracts/source_contour.py:48` | `validate_contour_request` | `canonical_ascii_strict_json` |
| `server/design/textcfg.py:75` | `_semantic_fingerprint` | `canonical_ascii_json` |
| `server/engines/dryrun.py:19` | `_canonical_seed` | `canonical_ascii_json` |
| `server/exports/geometry_identity.py:17` | `geometry_hash` | `canonical_ascii_strict_json` |
| `server/integration/api.py:140` | `design_schema_text` | `canonical_ascii_strict_json` |
| `server/integration/provenance.py:12` | `_canonical_json` | `canonical_utf8_strict_bytes` |
| `server/jobs/models.py:1135` | `SolveRequest.validate_client_metadata` | `canonical_utf8_strict_json` |
| `server/mesh/builder.py:815` | `_solver_mesh_cache_key` | `canonical_ascii_strict_json` |
| `server/mesh/imported.py:1733` | `_geometry_fingerprint` | `canonical_ascii_strict_json` |
| `server/preview/core.py:861` | `PreviewProtocol._compute_frame` | `canonical_ascii_strict_json` |
| `server/solver/beat_runtime/registry.py` | `key_id` | inline (BEAT host import set; same bytes as `canonical_ascii_strict_json`) |
| `server/solver/combine.py:97` | `serialize_channel_bases` | `canonical_ascii_json` |
| `server/solver/field_plane.py:860` | `FieldPlaneService._synthesis_revision` | `canonical_ascii_strict_json` |
| `server/solver/symmetry.py:161` | `_resolution_cache_key` | `canonical_ascii_string_json` |
