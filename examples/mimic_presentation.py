"""Holder binding: prove the requester controls the key the credential names.

Verifying a credential shows it is genuine. It does not show who is presenting
it. This module closes that gap with a single-use challenge:

1. the requester asks for a challenge, which is issued to their relay identity;
2. they sign a binding over that challenge, the credential and the exact query,
   using the private key whose public half the issuer put in the credential;
3. this town verifies that signature against ``credentialSubject.publicKey`` and
   spends the challenge, so the same presentation cannot be replayed.

The binding covers the query, so a captured presentation cannot be re-aimed at a
different one.
"""

import base64
import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wasteland.protocol import canonical

from . import ed25519

PROOF_TYPE = "PangenomeTownEd25519Jcs2026"
DEFAULT_STORE = ".town/mimic-challenges.sqlite"
CHALLENGE_SECONDS = 300


class PresentationError(ValueError):
    """The presentation did not bind this requester to this credential."""


def b64decode(value):
    if not isinstance(value, str):
        raise PresentationError("expected a base64url string")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError):
        raise PresentationError("value is not valid base64url") from None


def b64encode(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def subject_key(credential):
    """The public key the issuer bound to the subject, inside the signed document."""
    subject = credential.get("credentialSubject")
    if not isinstance(subject, dict):
        raise PresentationError("credential carries no credentialSubject")
    declared = subject.get("publicKey")
    if not isinstance(declared, str) or not declared:
        raise PresentationError(
            "credential does not bind a public key to its subject, so the holder "
            "cannot be checked; ask the issuer for a credential that does"
        )
    scheme, _, encoded = declared.partition(":")
    if scheme != "ed25519" or not encoded:
        raise PresentationError(f"unsupported subject key type: {scheme[:20]}")
    key = b64decode(encoded)
    if len(key) != 32:
        raise PresentationError("subject key is not a 32-byte Ed25519 key")
    return key


def binding(*, challenge, created, credential_id, query, subject, town):
    """The exact bytes both sides sign over. Order-independent by construction."""
    return canonical(
        {
            "challenge": challenge,
            "created": created,
            "credential": credential_id,
            "query_digest": hashlib.sha256(canonical(query).encode()).hexdigest(),
            "subject": subject,
            "town": town,
        }
    ).encode()


class ChallengeStore:
    """Single-use challenges, bound to the relay identity that asked for one."""

    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("WASTELAND_MIMIC_CHALLENGES", DEFAULT_STORE))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS challenges ("
            "challenge TEXT PRIMARY KEY, requester TEXT NOT NULL,"
            " expires TEXT NOT NULL, spent INTEGER NOT NULL DEFAULT 0)"
        )
        self.db.commit()
        self.path.chmod(0o600)

    def issue(self, requester, seconds=CHALLENGE_SECONDS):
        challenge = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        self.db.execute(
            "INSERT INTO challenges(challenge, requester, expires) VALUES(?,?,?)",
            (challenge, requester, expires.isoformat()),
        )
        self.db.commit()
        self._prune()
        return {"challenge": challenge, "expires": expires.isoformat(), "seconds": seconds}

    def spend(self, challenge, requester):
        """Consume a challenge, or explain why it cannot be. Never reusable."""
        if not isinstance(challenge, str) or not challenge:
            raise PresentationError("presentation carries no challenge")
        row = self.db.execute(
            "SELECT requester, expires, spent FROM challenges WHERE challenge=?",
            (challenge,),
        ).fetchone()
        if row is None:
            raise PresentationError("unknown challenge; request a fresh one")
        holder, expires, spent = row
        if spent:
            raise PresentationError("challenge already used; presentations are single-use")
        if holder != requester:
            raise PresentationError("challenge was issued to a different requester")
        if datetime.fromisoformat(expires) < datetime.now(timezone.utc):
            raise PresentationError("challenge expired; request a fresh one")
        # Marked spent before the answer is computed, so a retry cannot replay it.
        self.db.execute("UPDATE challenges SET spent=1 WHERE challenge=?", (challenge,))
        self.db.commit()
        return True

    def _prune(self):
        self.db.execute(
            "DELETE FROM challenges WHERE expires < ?",
            ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),),
        )
        self.db.commit()

    def close(self):
        self.db.close()


def verify_presentation(presentation, *, credential, query, requester, town, store):
    """Check the holder signed this exact request, then spend the challenge."""
    if not isinstance(presentation, dict):
        raise PresentationError(
            "this operation requires a 'presentation' proving you hold the credential; "
            "request a challenge with operation 'mimic-challenge'"
        )
    if presentation.get("type") != PROOF_TYPE:
        raise PresentationError(f"unsupported presentation type: {str(presentation.get('type'))[:40]}")
    created = presentation.get("created")
    if not isinstance(created, str) or not created:
        raise PresentationError("presentation has no created timestamp")
    challenge = presentation.get("challenge")
    subject = credential.get("credentialSubject", {}).get("id")
    credential_id = credential.get("id")

    key = subject_key(credential)
    signature = b64decode(presentation.get("proofValue"))
    if len(signature) != 64:
        raise PresentationError("presentation signature is not 64 bytes")

    message = binding(
        challenge=challenge, created=created, credential_id=credential_id,
        query=query, subject=subject, town=town,
    )
    if not ed25519.verify(signature, message, key):
        raise PresentationError(
            "presentation signature does not verify against the subject key bound "
            "in the credential; the presenter does not hold this credential"
        )
    # Only spend a challenge once the signature is good, so a bad attempt cannot
    # burn a valid requester's challenge.
    store.spend(challenge, requester)
    return {
        "holder_binding": "verified",
        "bound_to_subject": subject,
        "challenge_spent": True,
        "binding_covers": ["challenge", "credential", "query", "town", "created"],
        "replay": "single-use challenge, bound to the relay identity that requested it",
    }
