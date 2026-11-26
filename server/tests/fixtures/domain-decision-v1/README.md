# Frozen `cad-domain-decision-v1` decisions

`full-horn-d514e2655.json` is the decision WG `d514e2655` (the last build that
sealed `cad-domain-decision-v1`) wrote for the unlinked round horn of
`test_cadlink_domain_automatic._horn` (`_bundle(..., "full", _horn, HORN_THROAT)`),
ingested in that build's own tree. `domain_decision.v1_projection` of this
build's decision for the same model must reproduce it field for field
(`test_domain_record.py`); only the identity hashes of the bundle bytes
(snapshot, mesh, observations) differ between runs.
