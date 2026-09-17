"""Revocation: every unresolved answer is a refusal."""

import json
import os
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from examples import credential_status, ed25519, mimic_fixture, mimic_handler
from tests.test_holder_binding import (
    HOLDER_SECRET, ISSUER, ISSUER_SECRET, OTHER_SECRET, QUERY, b64, mint,
    signed_status, sign_presentation, stub_asker, trust_record,
)


def answering(statement):
    return lambda town, body: {"ok": True, "statement": statement}


class StatusCheckTests(unittest.TestCase):
    def setUp(self):
        self.credential = mint()
        self.record = trust_record()
        roots = unittest.mock.patch.dict(
            os.environ, {"WASTELAND_CAMELOT_ROOT_ISSUERS": ISSUER})
        roots.start()
        self.addCleanup(roots.stop)

    def check(self, ask, **kwargs):
        return credential_status.check(
            self.credential, record=self.record, ask=ask, **kwargs)

    def test_a_fresh_signed_active_statement_is_accepted(self):
        verdict = self.check(stub_asker(self.credential))
        self.assertEqual(verdict["status"], "active")
        self.assertTrue(verdict["signature_checked"])
        self.assertLessEqual(verdict["age_seconds"], 5)

    def test_every_status_other_than_active_is_refused(self):
        for status in ("revoked", "suspended", "unknown", "pending", ""):
            with self.subTest(status=status):
                with self.assertRaises(credential_status.StatusError) as caught:
                    self.check(stub_asker(self.credential, status=status))
                self.assertIn("not active" if status else "missing", str(caught.exception))

    def test_a_stale_statement_is_refused(self):
        old = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        with self.assertRaises(credential_status.StatusError) as caught:
            self.check(stub_asker(self.credential, as_of=old), age=60)
        self.assertIn("older than the 60s", str(caught.exception))

    def test_a_future_dated_statement_is_refused(self):
        ahead = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with self.assertRaises(credential_status.StatusError) as caught:
            self.check(stub_asker(self.credential, as_of=ahead))
        self.assertIn("future", str(caught.exception))

    def test_an_expired_statement_is_refused(self):
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with self.assertRaises(credential_status.StatusError) as caught:
            self.check(stub_asker(self.credential, valid_until=past))
        self.assertIn("expired", str(caught.exception))

    def test_a_statement_signed_by_the_wrong_key_is_refused(self):
        with self.assertRaises(credential_status.StatusError) as caught:
            self.check(stub_asker(self.credential, secret=OTHER_SECRET))
        self.assertIn("does not verify", str(caught.exception))

    def test_a_tampered_statement_is_refused(self):
        statement = signed_status(self.credential)
        statement["status"] = "active"
        statement["credential"] = "urn:credential:someone-else"
        with self.assertRaises(credential_status.StatusError) as caught:
            self.check(answering(statement))
        self.assertIn("does not verify", str(caught.exception))

    def test_an_unsigned_statement_is_refused(self):
        statement = signed_status(self.credential)
        del statement["proof"]
        with self.assertRaises(credential_status.StatusError) as caught:
            self.check(answering(statement))
        self.assertIn("not signed", str(caught.exception))

    def test_a_statement_from_an_untrusted_issuer_is_refused(self):
        other = "https://w3id.org/academic-wasteland/camelot/issuers/impostor"
        with self.assertRaises(credential_status.StatusError) as caught:
            self.check(answering(signed_status(self.credential, issuer=other)))
        self.assertIn("different issuer", str(caught.exception))

    def test_a_key_arriving_with_the_response_is_not_trusted(self):
        """The registrar cannot nominate the key used to check its own answer."""
        statement = signed_status(self.credential, secret=OTHER_SECRET)
        statement["publicKey"] = "ed25519:" + b64(ed25519.public_key(OTHER_SECRET))
        with self.assertRaises(credential_status.StatusError):
            self.check(answering(statement))

    def test_an_unreachable_registrar_is_refused(self):
        def unreachable(town, body):
            raise credential_status.StatusError("registrar unreachable: connection refused")
        with self.assertRaises(credential_status.StatusError) as caught:
            self.check(unreachable)
        self.assertIn("unreachable", str(caught.exception))

    def test_a_refusal_or_nonsense_reply_is_refused(self):
        for reply in ({"ok": False, "error": "no such credential"}, {}, "a string", None,
                      {"ok": True, "statement": {"status": "active"}}):
            with self.subTest(reply=reply):
                with self.assertRaises(credential_status.StatusError):
                    self.check(lambda town, body: reply)

    def test_a_misconfigured_town_refuses_rather_than_crashing(self):
        with self.assertRaises(credential_status.StatusError) as caught:
            self.check(credential_status.asker_for({"name": "zerzura"}))
        self.assertIn("configuration lacks", str(caught.exception))


class HandlerRevocationTests(unittest.TestCase):
    """The gate, end to end: no resolvable status means no data."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        mimic_fixture.build(cls.root / "private.sqlite", patients=400)
        mimic_fixture.build(cls.root / "public.sqlite", patients=400)
        import sqlite3
        db = sqlite3.connect(cls.root / "public.sqlite")
        db.execute("INSERT OR REPLACE INTO dataset_meta VALUES('public','true')")
        db.commit(); db.close()
        (cls.root / "trust.json").write_text(json.dumps(trust_record()))

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.environment = tempfile.TemporaryDirectory()
        here = Path(self.environment.name)
        os.environ.update({
            "WASTELAND_CAMELOT_TRUST": str(self.root / "trust.json"),
            "WASTELAND_MIMIC_CHALLENGES": str(here / "c.sqlite"),
            "WASTELAND_MIMIC_ASSENT": str(here / "s.sqlite"),
            "WASTELAND_MIMIC_AUDIT": str(here / "a.sqlite"),
            "WASTELAND_MIMIC_LEDGER": str(here / "l.sqlite"),
            "WASTELAND_MIMIC_TRUSTED_ISSUERS": ISSUER.rsplit("/", 1)[0] + "/",
            "WASTELAND_CAMELOT_ROOT_ISSUERS": ISSUER,
        })

    def tearDown(self):
        for name in ("WASTELAND_CAMELOT_TRUST", "WASTELAND_MIMIC_CHALLENGES",
                     "WASTELAND_MIMIC_ASSENT", "WASTELAND_MIMIC_AUDIT",
                     "WASTELAND_MIMIC_LEDGER", "WASTELAND_MIMIC_TRUSTED_ISSUERS",
                     "WASTELAND_CAMELOT_ROOT_ISSUERS",
                     "WASTELAND_MIMIC_DB"):
            os.environ.pop(name, None)
        self.environment.cleanup()

    def use(self, dataset, asker):
        os.environ["WASTELAND_MIMIC_DB"] = str(self.root / dataset)
        original = credential_status.asker_for
        credential_status.asker_for = lambda config: asker
        self.addCleanup(setattr, credential_status, "asker_for", original)

    def ask(self, body):
        return mimic_handler.handle(
            {"from": "peer_lab", "id": "urn:uuid:" + body["operation"], "body": body},
            {"name": "zerzura"})

    def give_assent(self):
        from examples import agreement as undertaking
        current = undertaking.agreement()
        created = datetime.now(timezone.utc).isoformat()
        message = undertaking.binding(
            digest=current["digest"], version=current["version"],
            subject=mint()["credentialSubject"]["id"], town="zerzura", created=created)
        self.ask({"operation": "dua-assent", "credential": mint(),
                  "assent": {"agreement": current["digest"], "version": current["version"],
                             "created": created,
                             "proofValue": b64(ed25519.sign(HOLDER_SECRET, message))}})

    def query(self):
        issued = self.ask({"operation": "mimic-challenge"})
        return self.ask({"operation": "mimic-aggregate", "credential": mint(),
                         "query": QUERY,
                         "presentation": sign_presentation(issued["challenge"], HOLDER_SECRET)})

    def test_a_revoked_credential_gets_no_data_from_a_private_dataset(self):
        self.use("private.sqlite", stub_asker(mint(), status="revoked"))
        self.give_assent()
        reply = self.query()
        self.assertFalse(reply["ok"])
        self.assertNotIn("groups", reply)
        self.assertNotIn("cohort_size", reply)
        self.assertEqual(reply["revocation"]["status"], "unresolved")
        self.assertIn("not active", reply["error"])

    def test_an_unreachable_registrar_gets_no_data_from_a_private_dataset(self):
        def unreachable(town, body):
            raise credential_status.StatusError("registrar unreachable")
        self.use("private.sqlite", unreachable)
        self.give_assent()
        reply = self.query()
        self.assertFalse(reply["ok"])
        self.assertNotIn("groups", reply)
        self.assertEqual(reply["revocation"]["policy"], "fail-closed")

    def test_an_active_credential_is_answered_and_the_check_is_reported(self):
        self.use("private.sqlite", stub_asker(mint()))
        self.give_assent()
        reply = self.query()
        self.assertTrue(reply["ok"], reply.get("error"))
        self.assertTrue(reply["revocation"]["checked"])
        self.assertEqual(reply["revocation"]["status"], "active")
        self.assertTrue(reply["groups"])

    def test_a_public_dataset_does_not_require_a_registrar(self):
        """Withdrawing access to rows anyone can download protects nothing."""
        def never_called(town, body):
            raise AssertionError("a public dataset must not need the registrar")
        self.use("public.sqlite", never_called)
        self.give_assent()
        reply = self.query()
        self.assertTrue(reply["ok"], reply.get("error"))
        self.assertFalse(reply["revocation"]["checked"])
        self.assertTrue(reply["groups"])


if __name__ == "__main__":
    unittest.main()
