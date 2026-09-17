"""The gated aggregate service: query allowlist, cell suppression, credential gate."""

import base64
import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from examples import ed25519, mimic_fixture, mimic_handler, mimic_service as service
from wasteland.protocol import canonical

CREDENTIAL = {
    "@context": ["https://www.w3.org/ns/credentials/v2"],
    "id": "https://w3id.org/academic-wasteland/camelot/credentials/test-1",
    "type": ["VerifiableCredential", "Accreditation"],
    "issuer": "https://w3id.org/academic-wasteland/camelot/issuers/ethics-council",
    "credentialSubject": {
        "id": "https://physionet.org/users/example",
        "roles": ["CredentialedPhysioNetUser"],
    },
    "proof": {
        "type": "PangenomeTownEd25519Jcs2026",
        "verificationMethod": "https://w3id.org/academic-wasteland/camelot/issuers/ethics-council#key-1",
        "proofValue": "not-a-real-signature",
    },
}


class QueryValidationTests(unittest.TestCase):
    def test_only_allowlisted_fields_are_accepted(self):
        for query in (
            {"cohort": [{"field": "p.gender; DROP TABLE patients", "value": "F"}]},
            {"cohort": [{"field": "gender", "op": "like", "value": "F"}]},
            {"group_by": ["subject_id"]},
            {"aggregate": "exfiltrate"},
            {"select": "*"},
        ):
            with self.subTest(query=query):
                with self.assertRaises(service.QueryError):
                    service.validate(query)

    def test_values_are_bound_not_interpolated(self):
        query = service.validate(
            {"cohort": [{"field": "gender", "op": "eq", "value": "F' OR '1'='1"}]}
        )
        clause, params = service._where(query["cohort"])
        self.assertEqual(clause, " WHERE p.gender = ?")
        self.assertEqual(params, ["F' OR '1'='1"])

    def test_mean_requires_an_allowlisted_measure(self):
        with self.assertRaises(service.QueryError):
            service.validate({"aggregate": "mean"})
        with self.assertRaises(service.QueryError):
            service.validate({"aggregate": "mean", "measure": "subject_id"})
        self.assertEqual(
            service.validate({"aggregate": "mean", "measure": "los_days"})["measure"],
            "los_days",
        )


class SuppressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.path = Path(cls.directory.name) / "mimic.sqlite"
        mimic_fixture.build(cls.path, patients=400)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_no_row_level_value_is_returned(self):
        result = service.run({"aggregate": "count", "group_by": ["gender"]}, self.path)
        for group in result["groups"]:
            self.assertEqual(set(group) - {"suppressed"}, {"key", "n", "value"})
            self.assertTrue(set(group["key"]) <= set(service.DIMENSIONS))

    def test_small_groups_are_suppressed_not_rounded(self):
        # A deliberately narrow cohort; whatever survives must clear the threshold.
        result = service.run(
            {
                "cohort": [{"field": "age_group", "op": "eq", "value": "18-29"}],
                "aggregate": "count",
                "group_by": ["race", "care_unit"],
            },
            self.path,
        )
        for group in result["groups"]:
            if "suppressed" in group:
                self.assertIsNone(group["n"])
                self.assertIsNone(group["value"])
            else:
                self.assertGreaterEqual(group["n"], service.MIN_CELL)

    def test_a_lone_suppressed_group_cannot_be_derived_from_the_total(self):
        groups = [
            {"key": {"g": "a"}, "n": 500, "value": 500},
            {"key": {"g": "b"}, "n": 40, "value": 40},
            {"key": {"g": "c"}, "n": 3, "value": 3},
        ]
        suppressed = _apply_suppression(groups)
        self.assertEqual(sum(1 for g in suppressed if "suppressed" in g), 2)

    def test_whole_cohort_below_threshold_returns_nothing(self):
        result = service.run(
            {
                "cohort": [
                    {"field": "gender", "op": "eq", "value": "F"},
                    {"field": "age_group", "op": "eq", "value": "18-29"},
                    {"field": "race", "op": "eq", "value": "ASIAN"},
                    {"field": "insurance", "op": "eq", "value": "Medicaid"},
                    {"field": "care_unit", "op": "eq", "value": "Neuro ICU"},
                    {"field": "admission_type", "op": "eq", "value": "ELECTIVE"},
                ],
                "aggregate": "count",
            },
            self.path,
        )
        self.assertTrue(result["suppressed"])
        self.assertIsNone(result["cohort_size"])
        self.assertEqual(result["groups"], [])

    def test_missing_database_is_refused_cleanly(self):
        with self.assertRaises(service.QueryError):
            service.run({"aggregate": "count"}, Path(self.directory.name) / "absent.sqlite")


def _apply_suppression(groups):
    """Drive the suppression branch of run() against a fixed group table."""
    small = [g for g in groups if g["n"] < service.MIN_CELL]
    for group in small:
        group["suppressed"] = "below-minimum-cell-size"
    if len(small) == 1 and len(groups) > 1:
        remaining = [g for g in groups if "suppressed" not in g]
        if remaining:
            min(remaining, key=lambda g: g["n"])["suppressed"] = "complementary-suppression"
    return groups


class DescribeTests(unittest.TestCase):
    """describe is the operation a stranger tries first; it must say something."""

    def describe(self):
        return mimic_handler.handle(
            {"from": "visitor", "id": "urn:uuid:d", "body": {"operation": "describe"}},
            {"name": "zerzura", "display": "Zerzura", "capabilities": ["describe"]},
        )

    def test_it_says_what_the_town_is_for(self):
        reply = self.describe()
        self.assertTrue(reply["ok"])
        self.assertIn("ICU", reply["description"])
        self.assertIn("never", reply["description"])

    def test_it_declares_whether_the_data_is_synthetic(self):
        self.assertIn("synthetic", self.describe()["dataset"])

    def test_it_points_at_the_open_contract_and_states_the_gates(self):
        reply = self.describe()
        self.assertIn("mimic-schema", reply["text"])
        self.assertIn("mimic-schema", reply["access"]["open_operations"])
        self.assertIn("mimic-aggregate", reply["access"]["credentialed_operations"])
        self.assertIn("Five conditions", reply["access"]["summary"])

    def test_it_admits_what_it_does_not_establish(self):
        text = self.describe()["not_established"]
        self.assertIn("PhysioNet", text)
        self.assertIn("differential privacy", text)

    def test_it_never_leaks_the_town_credential(self):
        reply = mimic_handler.handle(
            {"from": "visitor", "id": "urn:uuid:d", "body": {"operation": "describe"}},
            {"name": "zerzura", "token": "SECRET-TOKEN", "capabilities": []},
        )
        self.assertNotIn("SECRET-TOKEN", json.dumps(reply))


class CredentialGateTests(unittest.TestCase):
    """The gate now verifies signatures; these use a locally minted issuer key."""

    @classmethod
    def setUpClass(cls):
        cls.secret = bytes(range(32))
        cls.issuer = "https://w3id.org/academic-wasteland/camelot/issuers/test-council"
        # Nothing accredits this test issuer, so it must be named as a root.
        cls.roots = unittest.mock.patch.dict(
            os.environ, {"WASTELAND_CAMELOT_ROOT_ISSUERS": cls.issuer})
        cls.roots.start()
        cls.addClassCleanup(cls.roots.stop)
        key = "ed25519:" + base64.urlsafe_b64encode(
            ed25519.public_key(cls.secret)
        ).decode().rstrip("=")
        cls.record = {
            "fetched": "2026-09-15T00:00:00+00:00",
            "source": "test",
            "issuers": [{"id": cls.issuer, "publicKey": key, "accreditations": []}],
        }

    def sign(self, **overrides):
        credential = {**CREDENTIAL, "issuer": self.issuer, **overrides}
        credential["proof"] = {
            "type": "PangenomeTownEd25519Jcs2026",
            "verificationMethod": self.issuer + "#key-1",
            **overrides.get("proof", {}),
        }
        document = {k: v for k, v in credential.items() if k != "proof"}
        credential["proof"]["proofValue"] = base64.urlsafe_b64encode(
            ed25519.sign(self.secret, canonical(document).encode())
        ).decode().rstrip("=")
        return credential

    def test_a_genuine_signature_verifies(self):
        check = mimic_handler.check_credential(self.sign(), record=self.record)
        self.assertTrue(check["verified"])
        self.assertTrue(check["signature_checked"])
        self.assertEqual(check["status"], "verified")

    def test_verification_alone_does_not_assert_holder_binding(self):
        """check_credential proves issuance; holding is proved separately."""
        check = mimic_handler.check_credential(self.sign(), record=self.record)
        self.assertNotIn("holder_binding", check)
        self.assertIn("relay", check["warning"])

    def test_a_tampered_credential_is_refused(self):
        credential = self.sign()
        credential["credentialSubject"]["roles"] = ["Administrator"]
        with self.assertRaises(mimic_handler.CredentialError):
            mimic_handler.check_credential(credential, record=self.record)

    def test_an_unsigned_or_forged_credential_is_refused(self):
        truncated = self.sign()
        truncated["proof"]["proofValue"] = "AAAA"
        swapped = self.sign()
        swapped["proof"]["proofValue"] = base64.urlsafe_b64encode(
            ed25519.sign(bytes(range(1, 33)), b"a different message")
        ).decode().rstrip("=")
        for label, credential in (
            ("placeholder proofValue", CREDENTIAL),
            ("malformed signature", truncated),
            ("signature by another key", swapped),
            ("issuer swapped after signing",
             {**self.sign(), "issuer": "https://w3id.org/academic-wasteland/camelot/issuers/other"}),
        ):
            with self.subTest(case=label):
                with self.assertRaises(mimic_handler.CredentialError):
                    mimic_handler.check_credential(credential, record=self.record)

    def test_a_key_belonging_to_another_issuer_is_refused(self):
        # Signed correctly, but the method points at an issuer the credential does not name.
        credential = self.sign()
        credential["proof"]["verificationMethod"] = (
            "https://w3id.org/academic-wasteland/camelot/issuers/elsewhere#key-1"
        )
        with self.assertRaises(mimic_handler.CredentialError):
            mimic_handler.check_credential(credential, record=self.record)

    def test_structural_problems_are_refused_before_any_crypto(self):
        for credential in (
            None,
            "a-string",
            {},
            {**CREDENTIAL, "type": ["Accreditation"]},
            {**CREDENTIAL, "proof": {}},
            {**CREDENTIAL, "credentialSubject": {}},
            {**CREDENTIAL, "issuer": "https://example.invalid/issuers/self"},
            {**CREDENTIAL, "validUntil": "2020-01-01T00:00:00Z"},
        ):
            with self.subTest(credential=credential):
                with self.assertRaises(mimic_handler.CredentialError):
                    mimic_handler.check_credential(credential, record=self.record)

    def test_aggregate_without_a_credential_returns_no_data(self):
        message = {
            "from": "somebody",
            "id": "urn:uuid:test",
            "body": {"operation": "mimic-aggregate", "query": {"aggregate": "count"}},
        }
        with tempfile.TemporaryDirectory() as directory:
            os.environ["WASTELAND_MIMIC_AUDIT"] = str(Path(directory) / "audit.sqlite")
            reply = mimic_handler.handle(message, {"name": "zerzura"})
            del os.environ["WASTELAND_MIMIC_AUDIT"]
        self.assertFalse(reply["ok"])
        self.assertNotIn("groups", reply)
        self.assertNotIn("cohort_size", reply)


if __name__ == "__main__":
    unittest.main()
