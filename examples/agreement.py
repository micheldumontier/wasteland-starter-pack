"""Counterpart assent: the requester undertakes obligations to *this* town.

A data use agreement signed with a third party binds the signer to that party,
not to us, and a signed copy of one is personal data we have no reason to hold.
So we do not collect one. Instead the requester signs our own short undertaking,
which incorporates those terms by reference, using the same key the credential
binds to them.

What is stored is a digest, a version, a subject identifier, a signature and a
timestamp — no document belonging to anyone else.
"""

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from wasteland.protocol import canonical

from . import ed25519
from .mimic_presentation import PresentationError, b64decode

DEFAULT_AGREEMENT = Path(__file__).parent / "agreements" / "zerzura-1.0.txt"
DEFAULT_STORE = ".town/mimic-assent.sqlite"
VERSION = "1.0"


class AssentError(ValueError):
    """The undertaking was missing, unsigned, or given for another version."""


def agreement_path():
    return Path(os.environ.get("WASTELAND_MIMIC_AGREEMENT", DEFAULT_AGREEMENT))


def agreement(path=None):
    """The current undertaking: its text, version and digest."""
    path = Path(path) if path else agreement_path()
    if not path.exists():
        raise AssentError(f"no agreement document at {path}")
    raw = path.read_bytes()
    return {
        "version": os.environ.get("WASTELAND_MIMIC_AGREEMENT_VERSION", VERSION),
        "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "text": raw.decode(),
    }


def binding(*, digest, version, subject, town, created):
    """The bytes the requester signs to give the undertaking."""
    return canonical(
        {
            "agreement": digest,
            "created": created,
            "subject": subject,
            "town": town,
            "version": version,
        }
    ).encode()


class AssentStore:
    """Recorded undertakings, one per subject per agreement digest."""

    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("WASTELAND_MIMIC_ASSENT", DEFAULT_STORE))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS assent ("
            "subject TEXT NOT NULL, digest TEXT NOT NULL, version TEXT NOT NULL,"
            " created TEXT NOT NULL, recorded TEXT NOT NULL, signature TEXT NOT NULL,"
            " credential TEXT, PRIMARY KEY (subject, digest))"
        )
        self.db.commit()
        self.path.chmod(0o600)

    def record(self, *, subject, digest, version, created, signature, credential):
        self.db.execute(
            "INSERT OR REPLACE INTO assent VALUES(?,?,?,?,?,?,?)",
            (subject, digest, version, created,
             datetime.now(timezone.utc).isoformat(), signature, credential),
        )
        self.db.commit()

    def given(self, subject, digest):
        row = self.db.execute(
            "SELECT version, created, recorded FROM assent WHERE subject=? AND digest=?",
            (subject, digest),
        ).fetchone()
        if row is None:
            return None
        return {"version": row[0], "signed": row[1], "recorded": row[2]}

    def close(self):
        self.db.close()


def verify_assent(assent, *, credential, town, current, store):
    """Check the subject signed *this* version, and record it."""
    from .mimic_presentation import subject_key

    if not isinstance(assent, dict):
        raise AssentError(
            "this town requires a signed undertaking before it answers; fetch it "
            "with operation 'mimic-agreement' and sign its digest"
        )
    digest, version = assent.get("agreement"), assent.get("version")
    if digest != current["digest"]:
        raise AssentError(
            f"undertaking is for a different agreement; this town requires "
            f"{current['version']} ({current['digest']})"
        )
    created = assent.get("created")
    if not isinstance(created, str) or not created:
        raise AssentError("undertaking has no created timestamp")

    subject = credential.get("credentialSubject", {}).get("id")
    try:
        key = subject_key(credential)
    except PresentationError as error:
        raise AssentError(str(error)) from None

    signature = b64decode(assent.get("proofValue"))
    if len(signature) != 64:
        raise AssentError("undertaking signature is not 64 bytes")
    message = binding(
        digest=digest, version=version, subject=subject, town=town, created=created
    )
    if not ed25519.verify(signature, message, key):
        raise AssentError(
            "undertaking is not signed by the key bound to the credential subject"
        )
    store.record(
        subject=subject, digest=digest, version=version, created=created,
        signature=assent["proofValue"], credential=credential.get("id"),
    )
    return {
        "assent": "recorded",
        "agreement_version": version,
        "agreement_digest": digest,
        "signed_at": created,
    }


def require(subject, current, store):
    """The gate: an undertaking for the current agreement must already exist."""
    given = store.given(subject, current["digest"])
    if given is None:
        raise AssentError(
            f"no undertaking on file for this subject and agreement "
            f"{current['version']}; send operation 'dua-assent' first"
        )
    return {"assent": "on-file", **given}
