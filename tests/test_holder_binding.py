"""Holder binding: a copied credential is useless without the key it names."""

import base64
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from examples import agreement as undertaking
from examples import ed25519, mimic_fixture, mimic_handler
from examples import mimic_presentation as presentation
from wasteland.protocol import canonical

ISSUER = "https://w3id.org/academic-wasteland/camelot/issuers/test-council"
SUBJECT = "https://physionet.org/users/holder"
QUERY = {"aggregate": "count", "group_by": ["gender"]}


ISSUER_SECRET = bytes(range(32))
HOLDER_SECRET = bytes(range(32, 64))
OTHER_SECRET = bytes(range(64, 96))


def b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def trust_record():
    return {
        "fetched": "2026-09-15T00:00:00+00:00",
        "source": "test",
        "issuers": [
            {
                "id": ISSUER,
                "publicKey": "ed25519:" + b64(ed25519.public_key(ISSUER_SECRET)),
                "accreditations": [],
            }
        ],
    }


def mint(subject_key="bind", subject=SUBJECT):
    """A credential signed by the test issuer, optionally binding a subject key."""
    if subject_key == "bind":
        subject_key = "ed25519:" + b64(ed25519.public_key(HOLDER_SECRET))
    credential = {
        "@context": ["https://www.w3.org/ns/credentials/v2"],
        "id": "https://w3id.org/academic-wasteland/camelot/credentials/holder-1",
        "type": ["VerifiableCredential", "Accreditation"],
        "issuer": ISSUER,
        "validFrom": "2026-01-01T00:00:00Z",
        "credentialSubject": {"id": subject, "roles": ["CredentialedPhysioNetUser"]},
    }
    if subject_key is not None:
        credential["credentialSubject"]["publicKey"] = subject_key
    document = dict(credential)
    credential["proof"] = {
        "type": presentation.PROOF_TYPE,
        "verificationMethod": ISSUER + "#key-1",
        "proofValue": b64(ed25519.sign(ISSUER_SECRET, canonical(document).encode())),
    }
    return credential


def sign_presentation(challenge, secret, query=QUERY, credential=None, town="zerzura"):
    credential = credential or mint()
    created = datetime.now(timezone.utc).isoformat()
    message = presentation.binding(
        challenge=challenge, created=created, credential_id=credential.get("id"),
        query=query, subject=credential["credentialSubject"]["id"], town=town,
    )
    return {
        "type": presentation.PROOF_TYPE,
        "challenge": challenge,
        "created": created,
        "proofValue": b64(ed25519.sign(secret, message)),
    }


class HolderBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        cls.holder_secret = HOLDER_SECRET
        cls.other_secret = OTHER_SECRET
        cls.credential = mint()
        mimic_fixture.build(root / "mimic.sqlite", patients=400)

    @classmethod
    def tearDownClass(cls):
        for name in ("WASTELAND_MIMIC_DB", "WASTELAND_MIMIC_AUDIT"):
            os.environ.pop(name, None)
        cls.directory.cleanup()

    def store(self):
        return presentation.ChallengeStore(Path(self.directory.name) / "challenges.sqlite")

    def verify(self, proof, store, query=QUERY, requester="peer_lab", credential=None):
        return presentation.verify_presentation(
            proof, credential=credential or self.credential, query=query,
            requester=requester, town="zerzura", store=store,
        )

    def test_the_holder_of_the_named_key_is_accepted(self):
        store = self.store()
        try:
            issued = store.issue("peer_lab")
            verdict = self.verify(
                sign_presentation(issued["challenge"], self.holder_secret), store
            )
        finally:
            store.close()
        self.assertEqual(verdict["holder_binding"], "verified")
        self.assertEqual(verdict["bound_to_subject"], SUBJECT)

    def test_someone_who_merely_copied_the_credential_is_refused(self):
        """The whole point: a genuine credential plus the wrong key proves nothing."""
        store = self.store()
        try:
            issued = store.issue("peer_lab")
            with self.assertRaises(presentation.PresentationError) as caught:
                self.verify(
                    sign_presentation(issued["challenge"], self.other_secret), store
                )
        finally:
            store.close()
        self.assertIn("does not hold this credential", str(caught.exception))

    def test_a_presentation_cannot_be_replayed(self):
        store = self.store()
        try:
            issued = store.issue("peer_lab")
            proof = sign_presentation(issued["challenge"], self.holder_secret)
            self.verify(proof, store)
            with self.assertRaises(presentation.PresentationError) as caught:
                self.verify(proof, store)
        finally:
            store.close()
        self.assertIn("already used", str(caught.exception))

    def test_a_challenge_cannot_be_redeemed_by_another_town(self):
        store = self.store()
        try:
            issued = store.issue("peer_lab")
            proof = sign_presentation(issued["challenge"], self.holder_secret)
            with self.assertRaises(presentation.PresentationError) as caught:
                self.verify(proof, store, requester="somebody_else")
        finally:
            store.close()
        self.assertIn("different requester", str(caught.exception))

    def test_a_captured_presentation_cannot_be_re_aimed_at_another_query(self):
        store = self.store()
        try:
            issued = store.issue("peer_lab")
            proof = sign_presentation(issued["challenge"], self.holder_secret)
            with self.assertRaises(presentation.PresentationError):
                self.verify(proof, store, query={"aggregate": "count", "group_by": ["race"]})
        finally:
            store.close()

    def test_an_expired_challenge_is_refused(self):
        store = self.store()
        try:
            issued = store.issue("peer_lab", seconds=-1)
            proof = sign_presentation(issued["challenge"], self.holder_secret)
            with self.assertRaises(presentation.PresentationError) as caught:
                self.verify(proof, store)
        finally:
            store.close()
        self.assertIn("expired", str(caught.exception))

    def test_an_unknown_challenge_is_refused(self):
        store = self.store()
        try:
            proof = sign_presentation("never-issued", self.holder_secret)
            with self.assertRaises(presentation.PresentationError) as caught:
                self.verify(proof, store)
        finally:
            store.close()
        self.assertIn("unknown challenge", str(caught.exception))

    def test_a_failed_attempt_does_not_burn_the_challenge(self):
        store = self.store()
        try:
            issued = store.issue("peer_lab")
            with self.assertRaises(presentation.PresentationError):
                self.verify(
                    sign_presentation(issued["challenge"], self.other_secret), store
                )
            verdict = self.verify(
                sign_presentation(issued["challenge"], self.holder_secret), store
            )
        finally:
            store.close()
        self.assertEqual(verdict["holder_binding"], "verified")

    def test_a_credential_binding_no_key_cannot_be_presented(self):
        store = self.store()
        unbound = mint(subject_key=None)
        try:
            issued = store.issue("peer_lab")
            proof = sign_presentation(issued["challenge"], self.holder_secret,
                                           credential=unbound)
            with self.assertRaises(presentation.PresentationError) as caught:
                self.verify(proof, store, credential=unbound)
        finally:
            store.close()
        self.assertIn("does not bind a public key", str(caught.exception))


class HandlerBindingTests(unittest.TestCase):
    """The full handler path, including the challenge operation."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        mimic_fixture.build(cls.root / "mimic.sqlite", patients=400)
        cls.trust = cls.root / "trust.json"
        cls.trust.write_text(json.dumps(trust_record()))

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.environment = tempfile.TemporaryDirectory()
        os.environ.update({
            "WASTELAND_MIMIC_DB": str(self.root / "mimic.sqlite"),
            "WASTELAND_CAMELOT_TRUST": str(self.trust),
            "WASTELAND_MIMIC_CHALLENGES": str(Path(self.environment.name) / "c.sqlite"),
            "WASTELAND_MIMIC_AUDIT": str(Path(self.environment.name) / "a.sqlite"),
            "WASTELAND_MIMIC_ASSENT": str(Path(self.environment.name) / "s.sqlite"),
            "WASTELAND_MIMIC_TRUSTED_ISSUERS": ISSUER.rsplit("/", 1)[0] + "/",
        })

    def tearDown(self):
        for name in ("WASTELAND_MIMIC_DB", "WASTELAND_CAMELOT_TRUST",
                     "WASTELAND_MIMIC_CHALLENGES", "WASTELAND_MIMIC_AUDIT",
                     "WASTELAND_MIMIC_ASSENT", "WASTELAND_MIMIC_TRUSTED_ISSUERS"):
            os.environ.pop(name, None)
        self.environment.cleanup()

    def ask(self, body, requester="peer_lab"):
        message = {"from": requester, "id": "urn:uuid:" + body["operation"], "body": body}
        return mimic_handler.handle(message, {"name": "zerzura"})

    def give_assent(self, secret=HOLDER_SECRET):
        """These tests are about holder binding; the undertaking is a separate gate."""
        current = undertaking.agreement()
        created = datetime.now(timezone.utc).isoformat()
        message = undertaking.binding(
            digest=current["digest"], version=current["version"],
            subject=mint()["credentialSubject"]["id"], town="zerzura", created=created,
        )
        return self.ask({
            "operation": "dua-assent", "credential": mint(),
            "assent": {"agreement": current["digest"], "version": current["version"],
                       "created": created, "proofValue": b64(ed25519.sign(secret, message))},
        })

    def test_a_query_without_a_presentation_returns_no_data(self):
        reply = self.ask({"operation": "mimic-aggregate", "credential": mint(), "query": QUERY})
        self.assertFalse(reply["ok"])
        self.assertNotIn("groups", reply)
        self.assertIn("presentation", reply["error"])

    def test_challenge_then_signed_presentation_returns_aggregates(self):
        self.give_assent()
        issued = self.ask({"operation": "mimic-challenge"})
        self.assertTrue(issued["ok"])
        reply = self.ask({
            "operation": "mimic-aggregate",
            "credential": mint(),
            "query": QUERY,
            "presentation": sign_presentation(issued["challenge"], HOLDER_SECRET),
        })
        self.assertTrue(reply["ok"], reply.get("error"))
        self.assertEqual(reply["credential_check"]["holder_binding"], "verified")
        self.assertTrue(reply["credential_check"]["verified"])
        self.assertTrue(reply["groups"])

    def test_a_copied_credential_without_the_key_returns_no_data(self):
        self.give_assent()
        issued = self.ask({"operation": "mimic-challenge"})
        reply = self.ask({
            "operation": "mimic-aggregate",
            "credential": mint(),
            "query": QUERY,
            "presentation": sign_presentation(issued["challenge"], OTHER_SECRET),
        })
        self.assertFalse(reply["ok"])
        self.assertNotIn("groups", reply)
        self.assertEqual(reply["credential_check"]["holder_binding"], "failed")


if __name__ == "__main__":
    unittest.main()
