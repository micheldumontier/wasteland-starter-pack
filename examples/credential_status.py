"""Ask the registrar whether a credential is still valid, and fail closed.

A verified signature proves a credential was issued. It says nothing about
whether it has since been revoked, and revocation is normally the control you
can rely on being prompt. This implements the receiving half of the relay
`credential-status` contract: the registrar returns an issuer-signed statement,
and this town verifies that statement against the issuer key in its *own* trust
store -- never a key that arrived with the response -- then refuses unless the
answer is a fresh, signed `active`.

Every failure is a refusal. Unknown, suspended, revoked, stale, malformed,
unsigned, mismatched or unreachable all mean no data.
"""

import os
import time
from datetime import datetime, timedelta, timezone

from wasteland.client import RemoteError, request
from wasteland.protocol import envelope

from .camelot_trust import PROOF_TYPE, TrustError, _b64, _check_signature, _issuer_keys

OPERATION = "credential-status"
ACCEPTABLE = "active"
DEFAULT_MAX_AGE = 60      # seconds; a statement older than this is stale
DEFAULT_SKEW = 30         # tolerated clock skew for a future-dated statement
REQUIRED = ("credential", "issuer", "status", "as_of")


class StatusError(ValueError):
    """The credential's status could not be established as currently active."""


def status_town():
    return os.environ.get("WASTELAND_MIMIC_STATUS_TOWN", "camelot")


def max_age():
    try:
        return int(os.environ.get("WASTELAND_MIMIC_STATUS_MAX_AGE", DEFAULT_MAX_AGE))
    except ValueError:
        return DEFAULT_MAX_AGE


def _moment(value, field):
    if not isinstance(value, str) or not value:
        raise StatusError(f"status statement has no {field}")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise StatusError(f"status statement {field} is not an ISO-8601 timestamp") from None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def relay_asker(config, timeout=30):
    """Send `credential-status` over the relay and return the reply body."""

    def ask(town, body):
        missing = [key for key in ("name", "hub", "token") if not config.get(key)]
        if missing:
            # A misconfigured town must refuse, not crash past the gate.
            raise StatusError(
                "cannot reach the registrar: town configuration lacks "
                + ", ".join(missing)
            )
        message = envelope(config["name"], town, "", OPERATION, body=body)
        try:
            request(config["hub"], "/v1/messages", token=config["token"], data=message)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                answer = request(
                    config["hub"], "/v1/messages/" + message["id"], token=config["token"]
                )
                if answer.get("replies"):
                    return answer["replies"][-1]["body"]
                time.sleep(0.5)
        except (RemoteError, OSError) as error:
            raise StatusError(f"registrar unreachable: {error}") from None
        raise StatusError(f"{town} did not answer {OPERATION} within {timeout}s")

    return ask


def asker_for(config):
    """How the handler obtains a transport. Replaced in tests with a stub."""
    return relay_asker(config)


def check(credential, *, record, ask, now=None, age=None):
    """Establish that this credential is currently active, or raise StatusError."""
    now = now or datetime.now(timezone.utc)
    age = max_age() if age is None else age

    issuer = credential.get("issuer")
    issuer = issuer.get("id") if isinstance(issuer, dict) else issuer
    reference = credential.get("credentialStatus")
    body = {
        "operation": OPERATION,
        "credential": credential.get("id"),
        "status_id": reference.get("id") if isinstance(reference, dict) else None,
    }
    reply = ask(status_town(), body)
    if not isinstance(reply, dict):
        raise StatusError("registrar returned no status statement")
    if reply.get("ok") is False:
        raise StatusError(f"registrar refused: {str(reply.get('error'))[:120]}")
    statement = reply.get("statement", reply)
    if not isinstance(statement, dict):
        raise StatusError("status statement is not an object")
    missing = [field for field in REQUIRED if not statement.get(field)]
    if missing:
        raise StatusError("status statement is missing: " + ", ".join(missing))

    # The key comes from our trust store. A statement that carries its own key,
    # or names an issuer we do not trust, establishes nothing.
    keys = _issuer_keys(record)
    if statement["issuer"] != issuer:
        raise StatusError("status statement is from a different issuer than the credential")
    if issuer not in keys:
        raise StatusError(f"no trusted key for issuer {str(issuer)[:80]}")
    proof = statement.get("proof")
    proof = proof[0] if isinstance(proof, list) and proof else proof
    if not isinstance(proof, dict) or proof.get("type") != PROOF_TYPE:
        raise StatusError("status statement is not signed with the expected proof type")
    if proof.get("verificationMethod", "").split("#", 1)[0] != issuer:
        raise StatusError("status statement verificationMethod does not belong to the issuer")
    try:
        verified, _ = _check_signature(statement, _b64(keys[issuer]))
    except TrustError as error:
        raise StatusError(f"status statement signature is unusable: {error}") from None
    if not verified:
        raise StatusError("status statement signature does not verify")

    if statement["credential"] != credential.get("id"):
        raise StatusError("status statement is about a different credential")
    if statement["status"] != ACCEPTABLE:
        raise StatusError(f"credential status is {str(statement['status'])[:40]}, not active")

    as_of = _moment(statement["as_of"], "as_of")
    if as_of > now + timedelta(seconds=DEFAULT_SKEW):
        raise StatusError("status statement is dated in the future")
    if now - as_of > timedelta(seconds=age):
        raise StatusError(
            f"status statement is {int((now - as_of).total_seconds())}s old, "
            f"older than the {age}s this town accepts"
        )
    if statement.get("valid_until"):
        valid_until = _moment(statement["valid_until"], "valid_until")
        if valid_until <= now:
            raise StatusError(f"status statement expired at {statement['valid_until']}")

    return {
        "status": ACCEPTABLE,
        "checked": True,
        "as_of": statement["as_of"],
        "age_seconds": int((now - as_of).total_seconds()),
        "max_age_seconds": age,
        "issuer": issuer,
        "signature_checked": True,
        "key_source": {"source": record.get("source"), "fetched": record.get("fetched")},
    }
