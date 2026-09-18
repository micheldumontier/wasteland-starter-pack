"""Zerzura's gated, aggregate-only MIMIC service.

Run with:

    python3 -m wasteland work --handler examples.mimic_handler:handle

Operations
    describe         what this town is for, and how to approach it; open
    mimic-schema     the public query contract and dataset descriptor; open
    mimic-agreement  this town's data use undertaking, with its digest; open
    mimic-challenge  a single-use challenge to bind a presentation; open
    dua-assent       give the undertaking, signed by the credential subject
    mimic-aggregate  a structured query; needs a credential, presentation and assent

A requester must present a verifiable credential. Its signature is verified
against the issuer keys Camelot publishes, using ``examples.camelot_trust``;
a credential that does not verify is refused and no data is computed. Refresh
the trust store before serving:

    python3 -m examples.camelot_trust --state .town --refresh

The requester must also prove they hold the credential, by signing a single-use
challenge with the private key the issuer bound to the credential's subject.
Ask for one with operation ``mimic-challenge``. The signed binding covers the
query, so a captured presentation cannot be re-aimed at a different question.

What this does not establish: PhysioNet is never consulted, and Camelot's keys
arrive over the same relay that carries these messages.
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from wasteland.client import RemoteError, default_handler

from . import agreement as undertaking
from . import camelot_trust
from . import credential_status
from . import mimic_budget as budget
from . import mimic_presentation as presentation
from . import mimic_service as service

DEFAULT_TRUSTED_ISSUERS = "https://w3id.org/academic-wasteland/camelot/"
MAX_CREDENTIAL_BYTES = 16_384

KEY_TRUST_NOTE = (
    "Issuer keys are fetched from Camelot over the same relay that carries these "
    "messages, so trust in them is trust in the relay. Pin them out of band if that "
    "matters. PhysioNet itself is never consulted: what is established is that an "
    "issuer Camelot lists asserted this, and that the requester holds the key it named."
)


class CredentialError(ValueError):
    """The presented credential was missing or structurally unusable."""


def _required_roles():
    """Roles a credential must assert. Unset means any verified credential passes."""
    raw = os.environ.get("WASTELAND_MIMIC_REQUIRED_ROLES", "")
    return tuple(role.strip() for role in raw.split(",") if role.strip())


def _trusted_issuers():
    raw = os.environ.get("WASTELAND_MIMIC_TRUSTED_ISSUERS", DEFAULT_TRUSTED_ISSUERS)
    return tuple(prefix.strip() for prefix in raw.split(",") if prefix.strip())


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise CredentialError(f"credential {field} must be a non-empty string")
    return value.strip()


def _expiry(credential):
    """Report the stated validity window. Unsigned, so this is a claim, not a fact."""
    stated = credential.get("validUntil") or credential.get("expirationDate")
    if not isinstance(stated, str):
        return None, False
    try:
        expires = datetime.fromisoformat(stated.replace("Z", "+00:00"))
    except ValueError:
        raise CredentialError("credential validUntil is not an ISO-8601 timestamp")
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return stated, expires < datetime.now(timezone.utc)


def check_credential(credential, record=None):
    """Structural checks, then a real signature check against Camelot's keys."""
    if credential is None:
        raise CredentialError(
            "this operation requires a presented credential; supply 'credential'"
        )
    if not isinstance(credential, dict):
        raise CredentialError("credential must be an object")
    if len(json.dumps(credential)) > MAX_CREDENTIAL_BYTES:
        raise CredentialError("credential is too large")

    types = credential.get("type")
    types = [types] if isinstance(types, str) else types
    if not isinstance(types, list) or "VerifiableCredential" not in types:
        raise CredentialError("credential type must include VerifiableCredential")

    issuer = credential.get("issuer")
    issuer = issuer.get("id") if isinstance(issuer, dict) else issuer
    issuer = _text(issuer, "issuer")
    trusted = _trusted_issuers()
    if not issuer.startswith(trusted):
        raise CredentialError(
            "credential issuer is not among this town's accepted issuers: "
            + ", ".join(trusted)
        )

    subject = credential.get("credentialSubject")
    if not isinstance(subject, dict):
        raise CredentialError("credential must carry a credentialSubject object")
    subject_id = _text(subject.get("id"), "credentialSubject.id")

    proof = credential.get("proof")
    proof = proof[0] if isinstance(proof, list) and proof else proof
    if not isinstance(proof, dict):
        raise CredentialError("credential must carry a proof")
    proof_type = _text(proof.get("type"), "proof.type")
    method = _text(proof.get("verificationMethod"), "proof.verificationMethod")
    _text(proof.get("proofValue"), "proof.proofValue")

    stated_expiry, expired = _expiry(credential)
    if expired:
        raise CredentialError(f"credential states it expired at {stated_expiry}")

    # Cryptographic check against Camelot's published keys. Fails closed.
    try:
        verdict = camelot_trust.verify_credential(credential, record=record)
    except camelot_trust.TrustError as error:
        raise CredentialError(str(error)) from None

    roles = subject.get("roles") if isinstance(subject.get("roles"), list) else []
    required = _required_roles()
    if required and not set(required) & set(roles):
        raise CredentialError(
            "credential does not assert a required role: " + ", ".join(required)
        )

    return {
        "presented": True,
        "verified": True,
        "signature_checked": True,
        "status": "verified",
        "algorithm": verdict["algorithm"],
        "issuer": issuer,
        "issuer_accredited_by": verdict["issuer_accredited_by"],
        "subject": subject_id,
        "roles": roles or None,
        "satisfied_required_roles": list(_required_roles()) or None,
        "credential_id": credential.get("id") if isinstance(credential.get("id"), str) else None,
        "proof_type": proof_type,
        "verification_method": method,
        "states_valid_until": stated_expiry,
        "key_source": verdict["key_source"],
        "warning": KEY_TRUST_NOTE,
    }


def _trust_store_status():
    try:
        record = camelot_trust.load()
    except camelot_trust.TrustError as error:
        return {"available": False, "error": str(error)}
    return {
        "available": True,
        "issuers": len(record["issuers"]),
        "source": record.get("source"),
        "fetched": record.get("fetched"),
    }


def _audit(requester, message_id, check, operation, query, outcome):
    """Append-only local record. Never leaves this machine."""
    path = Path(os.environ.get("WASTELAND_MIMIC_AUDIT", ".town/mimic-audit.sqlite"))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS requests ("
            "ts TEXT, requester TEXT, message_id TEXT, issuer TEXT, subject TEXT,"
            " credential_id TEXT, verified INTEGER, operation TEXT, query TEXT, outcome TEXT)"
        )
        db.execute(
            "INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                requester,
                message_id,
                (check or {}).get("issuer"),
                (check or {}).get("subject"),
                (check or {}).get("credential_id"),
                1 if (check or {}).get("verified") else 0,
                operation,
                json.dumps(query)[:4000] if query is not None else None,
                outcome[:200],
            ),
        )
        db.commit()
    finally:
        db.close()
    path.chmod(0o600)


def handle(message, config):
    body = message["body"]
    operation = body.get("operation")
    requester, message_id = message["from"], message["id"]

    if operation == "mimic-schema":
        return {
            "ok": True,
            "operation": operation,
            "town": config["name"],
            "dataset": service.dataset(),
            "query_contract": service.schema(),
            "access": {
                "credential_required": True,
                "accepted_issuers": list(_trusted_issuers()),
                "required_roles": list(_required_roles()) or "any verified credential",
                "signature_verification": camelot_trust.ALGORITHM,
                "trust_store": _trust_store_status(),
                "holder_binding": "required; single-use signed challenge",
                "challenge_operation": "mimic-challenge",
                "revocation_check": service.policy()["revocation_check"],
                "status_operation": credential_status.OPERATION,
                "undertaking_required": undertaking.agreement()["version"],
                "agreement_operation": "mimic-agreement",
                "disclosure_control": service.policy(),
                "composition_control": (
                    "requests that differ from an already-answered request by fewer "
                    f"than {service.MIN_CELL} records are refused, and each subject "
                    "has a rolling request budget; not applied to a public dataset"
                ),
                "warning": KEY_TRUST_NOTE,
            },
        }

    if operation == "describe":
        # The operation a stranger tries first. The default handler answers it
        # with a bare verb list, which says nothing about what the town is for.
        dataset = service.dataset()
        return {
            "ok": True,
            "name": config["name"],
            "display": config.get("display", config["name"]),
            "capabilities": config.get("capabilities", []),
            "description": (
                "Zerzura answers statistical questions about ICU patients for "
                "requesters who can prove they are entitled to ask, and never "
                "releases a record. Cohorts are named from an allowlist, results "
                "are suppressed below a minimum cell size, and a request is "
                "refused when subtracting it from an earlier answer would "
                "describe individuals."
            ),
            "dataset": {
                "name": dataset.get("name"),
                "schema": "MIMIC-IV",
                "synthetic": dataset.get("synthetic"),
                "unit_of_analysis": "icu_stay",
                "icu_stays": dataset.get("icu_stays"),
                "licence": dataset.get("licence"),
            },
            "access": {
                "summary": (
                    "Five conditions, in order: a credential signed by an issuer "
                    "Camelot publishes a key for; proof you hold the key it names; "
                    "a signed undertaking to this town; an allowlisted query; and a "
                    "composition check against what you have already been told."
                ),
                "open_operations": ["echo", "describe", "mimic-schema", "mimic-challenge"],
                "credentialed_operations": ["dua-assent", "mimic-aggregate"],
                "accepted_issuers": list(_trusted_issuers()),
            },
            "not_established": (
                "PhysioNet is never consulted; issuer keys arrive over the same "
                "relay that carries these messages; and cell suppression is not "
                "differential privacy."
            ),
            "source": "https://github.com/micheldumontier/zerzura",
            "text": (
                "Send operation mimic-schema for the full query contract and "
                "limits. No credential is needed to read it."
            ),
        }

    if operation == "mimic-agreement":
        current = undertaking.agreement()
        return {
            "ok": True,
            "operation": operation,
            "town": config["name"],
            **current,
            "sign": {
                "fields": ["agreement", "created", "subject", "town", "version"],
                "note": "Sign canonical JSON of those fields with the key the "
                        "credential binds to its subject, then send 'dua-assent'.",
            },
            "why": "We do not collect agreements you hold with other parties. This "
                   "is a direct undertaking to this town, incorporating those terms "
                   "by reference.",
        }

    if operation == "dua-assent":
        try:
            check = check_credential(body.get("credential"))
        except CredentialError as error:
            _audit(requester, message_id, None, operation, None, f"refused: {error}")
            return {"ok": False, "operation": operation, "error": str(error)}
        current = undertaking.agreement()
        store = undertaking.AssentStore()
        try:
            recorded = undertaking.verify_assent(
                body.get("assent"), credential=body["credential"],
                town=config["name"], current=current, store=store,
            )
        except undertaking.AssentError as error:
            _audit(requester, message_id, check, operation, None, f"refused: {error}")
            return {
                "ok": False, "operation": operation, "error": str(error),
                "agreement": {k: v for k, v in current.items() if k != "text"},
            }
        finally:
            store.close()
        _audit(requester, message_id, check, operation, None, "assent recorded")
        return {
            "ok": True, "operation": operation, "town": config["name"],
            "credential_check": check, **recorded,
            "stored": ["subject", "agreement digest", "version", "signature", "timestamp"],
        }

    if operation == "mimic-challenge":
        store = presentation.ChallengeStore()
        try:
            issued = store.issue(requester)
        finally:
            store.close()
        return {
            "ok": True,
            "operation": operation,
            "town": config["name"],
            **issued,
            "sign_with": "the private key whose public half the credential binds to its subject",
            "binding_fields": ["challenge", "created", "credential", "query_digest", "subject", "town"],
            "note": "Single-use, and only redeemable by the town it was issued to.",
        }

    if operation != "mimic-aggregate":
        return default_handler(message, config)

    try:
        check = check_credential(body.get("credential"))
    except CredentialError as error:
        _audit(requester, message_id, None, operation, body.get("query"), f"refused: {error}")
        return {
            "ok": False,
            "operation": operation,
            "error": str(error),
            "credential_check": {"presented": body.get("credential") is not None,
                                 "verified": False, "status": "rejected"},
        }

    # Revocation, before anything is computed or released. A verified signature
    # proves issuance, not that the credential still stands. Skipped only for a
    # dataset whose rows are public, where withdrawing access protects nothing.
    control = service.policy()
    revocation = {"checked": False, "reason": control["reason"]}
    if control["revocation_check"] == "required":
        try:
            revocation = credential_status.check(
                body["credential"],
                record=camelot_trust.load(),
                ask=credential_status.asker_for(config),
            )
        except (credential_status.StatusError, camelot_trust.TrustError,
                RemoteError, OSError, ValueError) as error:
            _audit(requester, message_id, check, operation, body.get("query"),
                   f"status unresolved: {error}")
            return {
                "ok": False,
                "operation": operation,
                "error": f"credential status could not be established: {error}",
                "credential_check": check,
                "revocation": {"checked": False, "status": "unresolved",
                               "policy": "fail-closed"},
                "hint": (
                    f"this town requires a signed, fresh '{credential_status.OPERATION}' "
                    f"answer from {credential_status.status_town()} before releasing "
                    "data from a non-public dataset"
                ),
            }

    store = presentation.ChallengeStore()
    try:
        binding = presentation.verify_presentation(
            body.get("presentation"),
            credential=body["credential"],
            query=body.get("query", {}),
            requester=requester,
            town=config["name"],
            store=store,
        )
    except presentation.PresentationError as error:
        _audit(requester, message_id, check, operation, body.get("query"), f"unbound: {error}")
        return {
            "ok": False,
            "operation": operation,
            "error": str(error),
            "credential_check": {**check, "holder_binding": "failed"},
            "hint": "request a challenge with operation 'mimic-challenge', then sign the binding",
        }
    finally:
        store.close()

    current = undertaking.agreement()
    store = undertaking.AssentStore()
    try:
        assent = undertaking.require(check["subject"], current, store)
    except undertaking.AssentError as error:
        _audit(requester, message_id, check, operation, body.get("query"), f"no assent: {error}")
        return {
            "ok": False,
            "operation": operation,
            "error": str(error),
            "credential_check": {**check, **binding},
            "agreement": {k: v for k, v in current.items() if k != "text"},
            "hint": "fetch it with 'mimic-agreement', sign its digest, send 'dua-assent'",
        }
    finally:
        store.close()

    started = time.monotonic()
    try:
        result, membership = service.run(body.get("query", {}), with_membership=True)
    except service.QueryError as error:
        _audit(requester, message_id, check, operation, body.get("query"), f"rejected: {error}")
        return {
            "ok": False,
            "operation": operation,
            "error": str(error),
            "credential_check": check,
            "query_contract": service.schema(),
        }

    # The query has run locally, but nothing is released until the sequence is
    # checked: a request that is legal alone can still isolate people in
    # combination. Skipped for a dataset whose rows are public already, since
    # the composition control protects exactly what suppression protects.
    if control["disclosure_control"] == "none":
        spend = {"composition_control": "not applied", "reason": control["reason"]}
        _audit(requester, message_id, check, operation, result["query"],
               f"answered (public dataset): cohort={result['cohort_size']}")
        return _answer(config, check, binding, assent, current, result, started, spend, revocation)

    ledger = budget.CohortLedger()
    try:
        spend = ledger.check_budget(check["subject"])
        ledger.check_differencing(
            check["subject"], service_membership := budget.membership_sets(result, membership)
        )
        ledger.record(check["subject"], service_membership)
    except budget.BudgetError as error:
        _audit(requester, message_id, check, operation, result["query"], f"withheld: {error}")
        return {
            "ok": False,
            "operation": operation,
            "error": str(error),
            "credential_check": {**check, **binding},
            "privacy": {"composition_control": "cohort-differencing-ledger-v1",
                        "minimum_cell_size": service.MIN_CELL},
        }
    finally:
        ledger.close()

    _audit(
        requester, message_id, check, operation, result["query"],
        f"answered: {len(result['groups'])} group(s), cohort={result['cohort_size']}",
    )
    return _answer(config, check, binding, assent, current, result, started, spend, revocation)


def _answer(config, check, binding, assent, current, result, started, spend, revocation):
    composition = spend.get("composition_control", "cohort-differencing-ledger-v1")
    remaining = {key: value for key, value in spend.items() if key != "composition_control"}
    return {
        "ok": True,
        "operation": "mimic-aggregate",
        "town": config["name"],
        "method": "mimic-aggregate-v1",
        "credential_check": {**check, **binding},
        "revocation": revocation,
        "undertaking": {**assent, "digest": current["digest"]},
        "dataset": service.dataset(),
        "query": result["query"],
        "cohort_size": result["cohort_size"],
        "groups": result["groups"],
        "suppressed": result["suppressed"],
        "privacy": {**result["privacy"],
                    "composition_control": composition,
                    "budget": remaining},
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
