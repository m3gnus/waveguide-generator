#!/usr/bin/env python3
"""Release-workflow helper for the signed SHA256SUMS manifest.

Runs on a bare release runner, so it loads ``server/updates/{ed25519,manifest}.py``
by path instead of importing the ``server`` package (whose ``__init__`` chain pulls
in the web framework).

    write   --tag T --out FILE FILE...   manifest bytes with the version line
    pubkey-matches HEX                   HEX must be the active, accepted key
    verify  --tag T --manifest M --sig S --dir D [--public-key-hex HEX]
                                         signature, version == tag, and every
                                         listed file in D against its checksum
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

_UPDATES = Path(__file__).resolve().parents[1] / "server" / "updates"


def _load():
    sys.path.insert(0, str(_UPDATES))
    spec = importlib.util.spec_from_file_location("wg_update_manifest", _UPDATES / "manifest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    manifest = _load()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    write = sub.add_parser("write")
    write.add_argument("--tag", required=True)
    write.add_argument("--out", type=Path, required=True)
    write.add_argument("files", nargs="+", type=Path)
    match = sub.add_parser("pubkey-matches")
    match.add_argument("public_key_hex")
    verify = sub.add_parser("verify")
    verify.add_argument("--tag", required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--sig", type=Path, required=True)
    verify.add_argument("--dir", type=Path, required=True)
    verify.add_argument("--public-key-hex", help="restrict verification to one compiled accepted key")
    args = parser.parse_args(argv)

    try:
        if args.command == "write":
            args.out.write_bytes(manifest.build_manifest(args.tag, args.files))
        elif args.command == "pubkey-matches":
            embedded = manifest.UPDATE_SIGNING_PUBLIC_KEY_HEX
            if manifest.is_placeholder_key(embedded):
                raise manifest.ManifestError(
                    "the embedded public key is still the placeholder; paste the "
                    "key from docs/reference/UPDATE-SIGNING.md step 3 and land it first"
                )
            try:
                decoded = bytes.fromhex(embedded)
            except ValueError as exc:
                raise manifest.ManifestError("the active signing key is not hex") from exc
            if len(decoded) != 32:
                raise manifest.ManifestError("the active signing key has an invalid length")
            if embedded not in manifest.UPDATE_SIGNING_PUBLIC_KEYS_HEX:
                raise manifest.ManifestError("the active signing key is not in the compiled accepted keys")
            if args.public_key_hex.lower() != embedded:
                raise manifest.ManifestError(
                    "the signing secret's public key is not the key embedded in this commit"
                )
        else:
            if args.public_key_hex is not None:
                args.public_key_hex = args.public_key_hex.lower()
                if args.public_key_hex not in manifest.UPDATE_SIGNING_PUBLIC_KEYS_HEX:
                    raise manifest.ManifestError("the verification key is not in the compiled accepted keys")
            # Always apply compiled trust, including malformed-key refusals.
            data, signature = args.manifest.read_bytes(), args.sig.read_bytes()
            entries = manifest.verify_manifest(data, signature, args.tag)
            if args.public_key_hex is not None:
                manifest.verify_manifest(
                    data, signature, args.tag,
                    public_key_hex=args.public_key_hex,
                )
            for name in entries:
                manifest.verify_file(entries, name, args.dir / name)
    except (manifest.ManifestError, OSError) as exc:
        print(f"release_manifest: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
