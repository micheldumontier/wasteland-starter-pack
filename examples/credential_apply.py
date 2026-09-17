"""Apply for, and later fetch, a holder-bound credential over the relay.

This speaks the authority's `credential-challenge` / `credential-apply` /
`credential-fetch` protocol using this repository's own pure-Python Ed25519, so
it needs nothing installed. The private key is generated locally, stored at mode
0600, and never sent: only its public half travels.

    python3 -m examples.credential_apply --key .town/holder.key \\
        --holder urn:participant:example apply application.json
    python3 -m examples.credential_apply --key .town/holder.key \\
        --holder urn:participant:example fetch app-ID

Applying is a request for a claim, not evidence of holding it. A human reviews
the evidence you supply privately and decides; an unsupported application is
denied or left pending.
"""

import argparse
import json
import os
import secrets
from pathlib import Path

from wasteland.client import Client
from wasteland.protocol import canonical

from . import ed25519
from .mimic_presentation import PROOF_TYPE, b64encode

CHALLENGE_TYPE = "CredentialRelayProof"


def load_key(path, create=False):
    path = Path(path)
    if not path.exists():
        if not create:
            raise SystemExit(f"{path} does not exist; apply first to create it")
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(handle, "wb") as out:
            out.write(secrets.token_bytes(32))
    if path.stat().st_mode & 0o077:
        raise SystemExit(f"{path} must be mode 0600")
    secret = path.read_bytes()
    if len(secret) != 32:
        raise SystemExit(f"{path} is not a 32-byte Ed25519 private key")
    return secret


def sign(document, secret):
    """The authority's wire format: Ed25519 over canonical JSON without `proof`."""
    payload = {key: value for key, value in document.items() if key != "proof"}
    return {
        **document,
        "proof": {"type": PROOF_TYPE,
                  "proofValue": b64encode(ed25519.sign(secret, canonical(payload).encode()))},
    }


def call(client, town, operation, body, timeout=60):
    message_id = client.ask(town, operation=operation, body=body)
    for reply in client.wait(message_id, timeout):
        if (reply["from"] == town and reply["kind"] == "answer"
                and reply["in_reply_to"] == message_id):
            if reply["body"].get("ok") is not True:
                raise SystemExit("authority refused: "
                                 + str(reply["body"].get("error", reply["body"])))
            return reply["body"]
    raise SystemExit("no authenticated reply from the authority")


def holder_request(client, town, secret, holder, *, application, action="apply"):
    request = {
        "action": action,
        "holder": holder,
        "publicKey": "ed25519:" + b64encode(ed25519.public_key(secret)),
        "application": application,
    }
    challenge = call(client, town, "credential-challenge", {"request": request})["challenge"]
    # The challenge must bind this town, this authority and this exact request,
    # or it is not proof of anything we asked for.
    if (challenge.get("type") != CHALLENGE_TYPE
            or challenge.get("audience") != town
            or challenge.get("sender") != client.config["name"]
            or challenge.get("request") != request):
        raise SystemExit("authority returned a challenge that does not match the request")
    return call(client, town, f"credential-{action}", {"presentation": sign(challenge, secret)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default=".town")
    parser.add_argument("--authority", default="camelot")
    parser.add_argument("--holder", required=True)
    parser.add_argument("--key", type=Path, required=True)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("apply").add_argument("application", type=Path)
    actions.add_parser("fetch").add_argument("application")
    args = parser.parse_args()

    secret = load_key(args.key, create=args.action == "apply")
    application = (json.loads(args.application.read_text()) if args.action == "apply"
                   else args.application)
    print("public key:", "ed25519:" + b64encode(ed25519.public_key(secret)))
    print(json.dumps(holder_request(Client(args.state), args.authority, secret, args.holder,
                                    application=application, action=args.action), indent=2))


if __name__ == "__main__":
    main()
