"""Counterpart assent: no undertaking on file, no data."""

import base64
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from examples import agreement as undertaking
from examples import ed25519, mimic_fixture, mimic_handler
from tests.test_holder_binding import (
    HOLDER_SECRET, ISSUER, OTHER_SECRET, QUERY, b64, mint, sign_presentation,
    trust_record,
)


class AgreementDocumentTests(unittest.TestCase):
    def test_the_digest_covers_the_exact_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.txt"
            path.write_text("one")
            first = undertaking.agreement(path)["digest"]
            path.write_text("one ")
            self.assertNotEqual(first, undertaking.agreement(path)["digest"])

    def test_the_shipped_agreement_names_what_it_incorporates(self):
        text = " ".join(undertaking.agreement()["text"].split())
        self.assertIn("PhysioNet Credentialed Health Data Use Agreement, version 1.5.0", text)
        self.assertIn("direct undertaking to this town", text)
        self.assertIn("no such agreement is required to be disclosed", text)
        # The multi-query risk cannot be closed by suppression, so it is closed here.
        self.assertIn("composing multiple queries", text)


class AssentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        mimic_fixture.build(cls.root / "mimic.sqlite", patients=400)
        (cls.root / "trust.json").write_text(json.dumps(trust_record()))

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.environment = tempfile.TemporaryDirectory()
        here = Path(self.environment.name)
        os.environ.update({
            "WASTELAND_MIMIC_DB": str(self.root / "mimic.sqlite"),
            "WASTELAND_CAMELOT_TRUST": str(self.root / "trust.json"),
            "WASTELAND_MIMIC_CHALLENGES": str(here / "c.sqlite"),
            "WASTELAND_MIMIC_ASSENT": str(here / "s.sqlite"),
            "WASTELAND_MIMIC_AUDIT": str(here / "a.sqlite"),
            "WASTELAND_MIMIC_TRUSTED_ISSUERS": ISSUER.rsplit("/", 1)[0] + "/",
        })

    def tearDown(self):
        for name in ("WASTELAND_MIMIC_DB", "WASTELAND_CAMELOT_TRUST",
                     "WASTELAND_MIMIC_CHALLENGES", "WASTELAND_MIMIC_ASSENT",
                     "WASTELAND_MIMIC_AUDIT", "WASTELAND_MIMIC_TRUSTED_ISSUERS",
                     "WASTELAND_MIMIC_AGREEMENT"):
            os.environ.pop(name, None)
        self.environment.cleanup()

    def ask(self, body, requester="peer_lab"):
        return mimic_handler.handle(
            {"from": requester, "id": "urn:uuid:" + body["operation"], "body": body},
            {"name": "zerzura"},
        )

    def sign_assent(self, secret=HOLDER_SECRET, digest=None, version=None):
        current = undertaking.agreement()
        digest = digest or current["digest"]
        version = version or current["version"]
        created = datetime.now(timezone.utc).isoformat()
        message = undertaking.binding(
            digest=digest, version=version,
            subject=mint()["credentialSubject"]["id"], town="zerzura", created=created,
        )
        return {"agreement": digest, "version": version, "created": created,
                "proofValue": b64(ed25519.sign(secret, message))}

    def query(self):
        issued = self.ask({"operation": "mimic-challenge"})
        return self.ask({
            "operation": "mimic-aggregate", "credential": mint(), "query": QUERY,
            "presentation": sign_presentation(issued["challenge"], HOLDER_SECRET),
        })

    def test_the_agreement_is_public_and_carries_its_digest(self):
        reply = self.ask({"operation": "mimic-agreement"})
        self.assertTrue(reply["ok"])
        self.assertTrue(reply["digest"].startswith("sha256:"))
        self.assertIn("PhysioNet", reply["text"])

    def test_a_verified_holder_without_an_undertaking_gets_no_data(self):
        reply = self.query()
        self.assertFalse(reply["ok"])
        self.assertNotIn("groups", reply)
        self.assertEqual(reply["credential_check"]["holder_binding"], "verified")
        self.assertIn("no undertaking on file", reply["error"])

    def test_assent_then_query_returns_data(self):
        given = self.ask({"operation": "dua-assent", "credential": mint(),
                          "assent": self.sign_assent()})
        self.assertTrue(given["ok"], given.get("error"))
        reply = self.query()
        self.assertTrue(reply["ok"], reply.get("error"))
        self.assertEqual(reply["undertaking"]["assent"], "on-file")
        self.assertTrue(reply["groups"])

    def test_an_undertaking_signed_by_another_key_is_refused(self):
        reply = self.ask({"operation": "dua-assent", "credential": mint(),
                          "assent": self.sign_assent(secret=OTHER_SECRET)})
        self.assertFalse(reply["ok"])
        self.assertIn("not signed by the key", reply["error"])

    def test_assent_to_a_different_agreement_does_not_count(self):
        reply = self.ask({"operation": "dua-assent", "credential": mint(),
                          "assent": self.sign_assent(digest="sha256:" + "0" * 64)})
        self.assertFalse(reply["ok"])
        self.assertIn("different agreement", reply["error"])

    def test_a_new_agreement_version_requires_a_new_undertaking(self):
        self.ask({"operation": "dua-assent", "credential": mint(),
                  "assent": self.sign_assent()})
        self.assertTrue(self.query()["ok"])
        replacement = Path(self.environment.name) / "v2.txt"
        replacement.write_text("Zerzura Data Use Undertaking\nVersion 2.0\nNew terms.\n")
        os.environ["WASTELAND_MIMIC_AGREEMENT"] = str(replacement)
        reply = self.query()
        self.assertFalse(reply["ok"])
        self.assertIn("no undertaking on file", reply["error"])

    def test_assent_is_recorded_without_any_third_party_document(self):
        self.ask({"operation": "dua-assent", "credential": mint(),
                  "assent": self.sign_assent()})
        store = undertaking.AssentStore()
        try:
            columns = [row[1] for row in store.db.execute("PRAGMA table_info(assent)")]
            rows = store.db.execute("SELECT * FROM assent").fetchall()
        finally:
            store.close()
        self.assertEqual(len(rows), 1)
        # Only a digest, a version, an identifier, a signature and timestamps.
        self.assertEqual(set(columns),
                         {"subject", "digest", "version", "created", "recorded",
                          "signature", "credential"})


if __name__ == "__main__":
    unittest.main()
