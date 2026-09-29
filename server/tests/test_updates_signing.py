"""Ed25519 verification and the signed SHA256SUMS manifest (RFC 8032, section 7.1)."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from server.updates import ed25519, manifest

ROOT = Path(__file__).resolve().parents[2]

# RFC 8032 section 7.1 test vectors 1-3: (secret, public, message, signature).
RFC_VECTORS = [
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
    (
        "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
    ),
]


def _compress(point) -> bytes:
    zinv = ed25519._inv(point[2])
    x, y = point[0] * zinv % ed25519._P, point[1] * zinv % ed25519._P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _keypair(seed: bytes) -> tuple[bytes, bytes, int, bytes]:
    """Test-side signing helper (the shipped module only verifies)."""
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return seed, _compress(ed25519._mul(a, ed25519._G)), a, h[32:]


def _sign(seed: bytes, message: bytes) -> bytes:
    _, public, a, prefix = _keypair(seed)
    r = ed25519._challenge(prefix + message)
    r_bytes = _compress(ed25519._mul(r, ed25519._G))
    k = ed25519._challenge(r_bytes + public + message)
    return r_bytes + ((r + k * a) % ed25519._Q).to_bytes(32, "little")


@pytest.mark.parametrize("secret,public,message,signature", RFC_VECTORS)
def test_rfc8032_vectors_verify_and_the_test_signer_reproduces_them(
    secret: str, public: str, message: str, signature: str
) -> None:
    pub, msg, sig = (bytes.fromhex(x) for x in (public, message, signature))
    assert ed25519.verify(pub, msg, sig)
    assert _keypair(bytes.fromhex(secret))[1] == pub
    assert _sign(bytes.fromhex(secret), msg) == sig


def test_a_tampered_message_signature_or_key_is_rejected() -> None:
    _, public, message, signature = (bytes.fromhex(x) for x in RFC_VECTORS[1])
    assert ed25519.verify(public, message, signature)
    assert not ed25519.verify(public, message + b"x", signature)
    assert not ed25519.verify(public, message, signature[:-1] + b"\x01")
    assert not ed25519.verify(bytes.fromhex(RFC_VECTORS[0][1]), message, signature)


def test_malformed_lengths_and_an_out_of_range_s_are_rejected() -> None:
    public, message, signature = (bytes.fromhex(RFC_VECTORS[1][i]) for i in (1, 2, 3))
    assert not ed25519.verify(public[:31], message, signature)
    assert not ed25519.verify(public, message, signature[:63])
    s_plus_q = (int.from_bytes(signature[32:], "little") + ed25519._Q).to_bytes(32, "little")
    assert not ed25519.verify(public, message, signature[:32] + s_plus_q)
    assert not ed25519.verify(b"\xff" * 32, message, signature)  # y >= p


SEED = bytes(range(32))
PUBLIC_HEX = _keypair(SEED)[1].hex()
TAG = "v9.9.9"


def _release(tmp_path: Path, tag: str = TAG) -> tuple[bytes, bytes, Path]:
    files = tmp_path / "files"
    files.mkdir()
    paths = []
    for name in ("a-setup.exe", "b.dmg", "c.tar.gz"):
        (files / name).write_bytes(f"payload {name}".encode())
        paths.append(files / name)
    data = manifest.build_manifest(tag, paths)
    return data, _sign(SEED, data), files


def test_a_signed_manifest_verifies_and_yields_checksums(tmp_path: Path) -> None:
    data, sig, files = _release(tmp_path)
    assert data.startswith(b"# version v9.9.9\n")
    entries = manifest.verify_manifest(data, sig, TAG, public_key_hex=PUBLIC_HEX)
    assert set(entries) == {"a-setup.exe", "b.dmg", "c.tar.gz"}
    for name in entries:
        manifest.verify_file(entries, name, files / name)


def test_a_wrong_version_is_refused_even_with_a_valid_signature(tmp_path: Path) -> None:
    data, sig, _ = _release(tmp_path)
    with pytest.raises(manifest.ManifestError, match="not v9.9.8"):
        manifest.verify_manifest(data, sig, "v9.9.8", public_key_hex=PUBLIC_HEX)


def test_an_edited_manifest_or_a_different_key_fails_the_signature(tmp_path: Path) -> None:
    data, sig, _ = _release(tmp_path)
    with pytest.raises(manifest.ManifestError, match="signature"):
        manifest.verify_manifest(data.replace(b"a-setup", b"z-setup"), sig, TAG, public_key_hex=PUBLIC_HEX)
    other = _keypair(bytes(reversed(range(32))))[1].hex()
    with pytest.raises(manifest.ManifestError, match="signature"):
        manifest.verify_manifest(data, sig, TAG, public_key_hex=other)


def test_a_file_that_differs_or_is_unlisted_is_refused(tmp_path: Path) -> None:
    data, sig, files = _release(tmp_path)
    entries = manifest.verify_manifest(data, sig, TAG, public_key_hex=PUBLIC_HEX)
    (files / "b.dmg").write_bytes(b"swapped")
    with pytest.raises(manifest.ManifestError, match="does not match"):
        manifest.verify_file(entries, "b.dmg", files / "b.dmg")
    with pytest.raises(manifest.ManifestError, match="not listed"):
        manifest.verify_file(entries, "other.zip", files / "b.dmg")


@pytest.mark.parametrize(
    "text",
    [
        b"",
        b"# version v1\n# version v1\n",
        b"# something else\n",
        b"# version v1\nnot a checksum line\n",
        b"# version v1\n" + b"0" * 64 + b"  ../evil\n",
        b"# version v1\n" + b"0" * 64 + b"  a\n" + b"0" * 64 + b"  a\n",
        b"\xff\xfe",
    ],
)
def test_malformed_manifests_are_refused(text: bytes) -> None:
    with pytest.raises(manifest.ManifestError):
        manifest.parse_manifest(text)


def test_the_embedded_key_is_a_placeholder_until_the_owner_pastes_the_real_one(
    tmp_path: Path,
) -> None:
    data, sig, _ = _release(tmp_path)
    if manifest.is_placeholder_key(manifest.UPDATE_SIGNING_PUBLIC_KEY_HEX):
        with pytest.raises(manifest.ManifestError, match="no update signing key"):
            manifest.verify_manifest(data, sig, TAG)
    else:
        assert len(bytes.fromhex(manifest.UPDATE_SIGNING_PUBLIC_KEY_HEX)) == 32


def _script(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "release_manifest.py"), *map(str, args)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_release_script_writes_and_verifies_on_a_bare_runner(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    (files / "x.dmg").write_bytes(b"x")
    out = tmp_path / "SHA256SUMS"
    assert _script("write", "--tag", TAG, "--out", out, files / "x.dmg").returncode == 0
    sig = tmp_path / "SHA256SUMS.sig"
    sig.write_bytes(_sign(SEED, out.read_bytes()))
    ok = _script("verify", "--tag", TAG, "--manifest", out, "--sig", sig, "--dir", files, "--public-key-hex", PUBLIC_HEX)
    assert ok.returncode == 0, ok.stderr
    (files / "x.dmg").write_bytes(b"changed")
    bad = _script("verify", "--tag", TAG, "--manifest", out, "--sig", sig, "--dir", files, "--public-key-hex", PUBLIC_HEX)
    assert bad.returncode == 1 and "does not match" in bad.stderr


def test_the_script_refuses_a_secret_whose_key_is_not_the_embedded_one() -> None:
    result = _script("pubkey-matches", PUBLIC_HEX)
    assert result.returncode == 1
    assert result.stderr.strip()
