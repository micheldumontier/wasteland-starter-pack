"""Requester side: hold a credential and prove it, then ask a gated question.

Generate a key and give its public half to the issuer, so the credential they
mint binds it to you:

    python3 -m examples.present --new-key .town/holder.key

Then ask a gated question. This fetches a single-use challenge, signs the
binding with your key, and sends the query in one step:

    python3 -m examples.present --state .town --to zerzura \\
        --credential my-credential.json --key .town/holder.key \\
        --query examples/mimic-aggregate-query.json
"""

import argparse
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from wasteland.client import Client

from . import agreement as undertaking
from . import ed25519
from .mimic_presentation import PROOF_TYPE, b64encode, binding


def new_key(path):
    """Write a fresh private key and return the public half to register."""
    path = Path(path)
    if path.exists():
        raise SystemExit(f"{path} exists; refusing to overwrite a private key")
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(32)
    path.write_bytes(secret)
    path.chmod(0o600)
    return "ed25519:" + b64encode(ed25519.public_key(secret))


def assent(client, town, credential, secret, timeout=90, agree=False):
    """Fetch the town's undertaking, show it, and sign it only on an explicit yes."""
    message_id = client.ask(town, operation="mimic-agreement")
    current = client.wait(message_id, timeout)[-1]["body"]
    if not current.get("ok"):
        raise SystemExit("agreement unavailable: " + json.dumps(current))
    print(current["text"])
    print(f"\n--- version {current['version']}  {current['digest']} ---")
    if not agree:
        print("\nRead the above. To give this undertaking, re-run with --i-agree.")
        return None, None

    created = datetime.now(timezone.utc).isoformat()
    subject = credential.get("credentialSubject", {}).get("id")
    message = undertaking.binding(
        digest=current["digest"], version=current["version"],
        subject=subject, town=town, created=created,
    )
    body = {
        "operation": "dua-assent",
        "credential": credential,
        "assent": {
            "agreement": current["digest"],
            "version": current["version"],
            "created": created,
            "proofValue": b64encode(ed25519.sign(secret, message)),
        },
    }
    message_id = client.ask(town, operation="dua-assent", body=body)
    return message_id, client.wait(message_id, timeout)[-1]["body"]


def present(client, town, credential, query, secret, timeout=90):
    """Fetch a challenge, sign the binding over this exact query, and send it."""
    challenge_id = client.ask(town, operation="mimic-challenge")
    issued = client.wait(challenge_id, timeout)[-1]["body"]
    if not issued.get("ok"):
        raise SystemExit("challenge refused: " + json.dumps(issued))

    created = datetime.now(timezone.utc).isoformat()
    message = binding(
        challenge=issued["challenge"],
        created=created,
        credential_id=credential.get("id"),
        query=query,
        subject=credential.get("credentialSubject", {}).get("id"),
        town=town,
    )
    body = {
        "operation": "mimic-aggregate",
        "credential": credential,
        "query": query,
        "presentation": {
            "type": PROOF_TYPE,
            "challenge": issued["challenge"],
            "created": created,
            "proofValue": b64encode(ed25519.sign(secret, message)),
        },
    }
    message_id = client.ask(town, operation="mimic-aggregate", body=body)
    return message_id, client.wait(message_id, timeout)[-1]["body"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new-key", type=Path)
    parser.add_argument("--state", default=".town")
    parser.add_argument("--to", default="zerzura")
    parser.add_argument("--credential", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--query", type=Path)
    parser.add_argument("--wait", type=float, default=90)
    parser.add_argument("--assent", action="store_true",
                        help="read this town's undertaking and give it")
    parser.add_argument("--i-agree", action="store_true",
                        help="with --assent, sign the undertaking just displayed")
    args = parser.parse_args()

    if args.new_key:
        print("Private key written to", args.new_key)
        print("Give this public key to your issuer:")
        print("   ", new_key(args.new_key))
        return
    if args.assent:
        if not (args.credential and args.key):
            parser.error("--credential and --key are required for --assent")
        secret = args.key.read_bytes()
        message_id, reply = assent(
            Client(args.state), args.to,
            json.loads(args.credential.read_text()), secret, args.wait, args.i_agree,
        )
        if reply is not None:
            print("Request:", message_id)
            print(json.dumps(reply, indent=2))
        return

    if not (args.credential and args.key and args.query):
        parser.error("--credential, --key and --query are required")

    secret = args.key.read_bytes()
    if len(secret) != 32:
        parser.error(f"{args.key} is not a 32-byte private key")
    message_id, reply = present(
        Client(args.state),
        args.to,
        json.loads(args.credential.read_text()),
        json.loads(args.query.read_text()),
        secret,
        args.wait,
    )
    print("Request:", message_id)
    print(json.dumps(reply, indent=2))


if __name__ == "__main__":
    main()
