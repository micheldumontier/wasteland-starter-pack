"""Verify Camelot-issued credentials against Camelot's published issuer keys.

Keys are fetched from Camelot over the relay and cached locally, so that message
handling never blocks on the network. Refresh before serving:

    python3 -m examples.camelot_trust --state .town --refresh

The signature check is real: a credential that does not verify is refused. What
it establishes is narrower than it looks, so read "Limits" in
docs/mimic-service.md before relying on it.
"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from wasteland.protocol import canonical

from . import ed25519

PROOF_TYPE = "PangenomeTownEd25519Jcs2026"
# An issuer is acceptable only if some other issuer in the trust store verifiably
# accredits it, or it is named here as a root. A prefix match is not enough: every
# issuer a registrar publishes shares its prefix, including deliberately
# unaccredited test issuers that must not be trusted for real access.
DEFAULT_ROOTS = "https://w3id.org/academic-wasteland/camelot/issuers/ethics-council"
DEFAULT_CACHE = ".town/camelot-trust.json"
ALGORITHM = "Ed25519 over JCS-canonical JSON without the proof block"


class TrustError(ValueError):
    """The credential could not be verified."""


def roots():
    raw = os.environ.get("WASTELAND_CAMELOT_ROOT_ISSUERS", DEFAULT_ROOTS)
    return tuple(root.strip() for root in raw.split(",") if root.strip())


def cache_path():
    return Path(os.environ.get("WASTELAND_CAMELOT_TRUST", DEFAULT_CACHE))


def _b64(value):
    import base64

    scheme, _, encoded = value.partition(":")
    if scheme != "ed25519" or not encoded:
        raise TrustError(f"unsupported public key encoding: {scheme[:20]}")
    return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))


def refresh(client, town="camelot", timeout=90, path=None):
    """Ask Camelot for its issuers and cache them. Returns the stored record."""
    message_id = client.ask(town, operation="issuers")
    replies = client.wait(message_id, timeout)
    body = replies[-1]["body"]
    if not body.get("issuers"):
        raise TrustError(f"{town} returned no issuers")
    record = {
        "fetched": datetime.now(timezone.utc).isoformat(),
        "source": f"{town} via relay {client.config['hub']}",
        "issuers": body["issuers"],
    }
    path = Path(path) if path else cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2))
    path.chmod(0o600)
    return record


def load(path=None):
    path = Path(path) if path else cache_path()
    if not path.exists():
        raise TrustError(
            "no Camelot trust store; run: python3 -m examples.camelot_trust --refresh"
        )
    return json.loads(path.read_text())


def _issuer_keys(record):
    return {
        issuer["id"]: issuer["publicKey"]
        for issuer in record["issuers"]
        if isinstance(issuer.get("publicKey"), str)
    }


def _check_signature(credential, key):
    proof = credential.get("proof")
    proof = proof[0] if isinstance(proof, list) and proof else proof
    if not isinstance(proof, dict):
        raise TrustError("credential carries no proof")
    if proof.get("type") != PROOF_TYPE:
        raise TrustError(f"unsupported proof type: {str(proof.get('type'))[:40]}")
    value = proof.get("proofValue")
    if not isinstance(value, str):
        raise TrustError("proof has no proofValue")
    import base64

    try:
        signature = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError):
        raise TrustError("proofValue is not base64url") from None
    document = {k: v for k, v in credential.items() if k != "proof"}
    return ed25519.verify(signature, canonical(document).encode(), key), proof


def verify_credential(credential, record=None, path=None):
    """Verify a credential's signature against Camelot's published keys.

    Raises TrustError when it does not verify; returns the verdict when it does.
    """
    record = record or load(path)
    keys = _issuer_keys(record)

    issuer = credential.get("issuer")
    issuer = issuer.get("id") if isinstance(issuer, dict) else issuer
    if not isinstance(issuer, str) or not issuer:
        raise TrustError("credential has no issuer")

    proof = credential.get("proof")
    proof = proof[0] if isinstance(proof, list) and proof else proof
    method = (proof or {}).get("verificationMethod")
    if not isinstance(method, str) or not method:
        raise TrustError("proof has no verificationMethod")
    # The signing key must belong to the issuer the credential names, or a
    # credential signed by one issuer could be attributed to another.
    if method.split("#", 1)[0] != issuer:
        raise TrustError("verificationMethod does not belong to the stated issuer")
    if issuer not in keys:
        raise TrustError(f"unknown issuer for this trust store: {issuer[:80]}")

    verified, proof = _check_signature(credential, _b64(keys[issuer]))
    if not verified:
        raise TrustError("signature does not verify against the issuer's published key")

    accredited = _accreditation(issuer, record, keys)
    if accredited is None and issuer not in roots():
        raise TrustError(
            f"issuer {issuer} is not accredited by any issuer this town roots its "
            "trust in, and is not itself a configured root; a registrar publishing "
            "an issuer is not the same as vouching for it"
        )

    return {
        "verified": True,
        "signature_checked": True,
        "algorithm": ALGORITHM,
        "proof_type": proof.get("type"),
        "issuer": issuer,
        "verification_method": method,
        "issuer_accredited_by": accredited or f"{issuer} (configured root)",
        "key_source": {"source": record.get("source"), "fetched": record.get("fetched")},
    }


def _accreditation(issuer, record, keys):
    """Whether Camelot's own council vouches for this issuer, checked by signature."""
    for entry in record["issuers"]:
        if entry["id"] != issuer:
            continue
        for accreditation in entry.get("accreditations", []):
            council = accreditation.get("issuer")
            if council in keys:
                try:
                    verified, _ = _check_signature(accreditation, _b64(keys[council]))
                except TrustError:
                    continue
                if verified:
                    return council
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default=".town")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--town", default="camelot")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.refresh:
        from wasteland.client import Client

        record = refresh(Client(args.state), args.town, path=args.out)
    else:
        record = load(args.out)
    keys = _issuer_keys(record)
    print(f"{len(keys)} issuer key(s), fetched {record['fetched']}")
    for issuer in sorted(keys):
        vouched = _accreditation(issuer, record, keys)
        print(f"  {issuer}\n    accredited by: {vouched or '(self-standing)'}")


if __name__ == "__main__":
    main()
