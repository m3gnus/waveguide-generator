"""The live CAD Link session between WG and its Fusion add-in.

An additive transport beside the v3 files (docs/reference/CADLINK-LIVE-PROTOCOL.md):

- ``endpoint``: ``wg-endpoint.json``, where this start serves and its secret;
- ``proof``: the mutual registration proofs;
- ``registry``: the in-memory sessions of one start, per data directory;
- ``api``: the ``/api/cadlink/live`` routes and their check order;
- ``heartbeat``: the heartbeat posted over HTTP;
- ``deliveries``: WG-bound deliveries over HTTP;
- ``requests``: Fusion-bound requests: long poll, claim, progress, completion;
- ``wake``: wakes a long poll when a request may have appeared.

Deliberately imports nothing here: ``server.cadlink.addin_update`` reads the
registry, and ``api`` imports ``addin_update``.
"""
