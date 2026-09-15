"""Sequences that isolate people are refused, even when each query is legal."""

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from examples import agreement as undertaking
from examples import ed25519, mimic_budget, mimic_fixture, mimic_handler
from examples import mimic_service as service
from tests.test_holder_binding import (
    HOLDER_SECRET, ISSUER, QUERY, b64, mint, sign_presentation, trust_record,
)


class LedgerTests(unittest.TestCase):
    def ledger(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        return mimic_budget.CohortLedger(Path(self.directory.name) / "ledger.sqlite")

    def test_a_slightly_smaller_cohort_is_refused(self):
        ledger = self.ledger()
        everyone = set(range(1000))
        ledger.record("alice", [("cohort", everyone)])
        # 997 of the same 1000: subtracting the answers describes three people.
        with self.assertRaises(mimic_budget.BudgetError) as caught:
            ledger.check_differencing("alice", [("cohort", set(range(997)))])
        self.assertIn("fewer than", str(caught.exception))

    def test_a_slightly_larger_cohort_is_refused(self):
        ledger = self.ledger()
        ledger.record("alice", [("cohort", set(range(1000)))])
        with self.assertRaises(mimic_budget.BudgetError):
            ledger.check_differencing("alice", [("cohort", set(range(1004)))])

    def test_a_genuinely_different_cohort_is_allowed(self):
        ledger = self.ledger()
        ledger.record("alice", [("cohort", set(range(1000)))])
        ledger.check_differencing("alice", [("cohort", set(range(500, 2000)))])

    def test_repeating_the_identical_request_is_allowed(self):
        ledger = self.ledger()
        ledger.record("alice", [("cohort", set(range(1000)))])
        ledger.check_differencing("alice", [("cohort", set(range(1000)))])

    def test_two_groups_inside_one_request_are_compared_to_each_other(self):
        ledger = self.ledger()
        with self.assertRaises(mimic_budget.BudgetError):
            ledger.check_differencing(
                "alice",
                [("group:a", set(range(100))), ("group:b", set(range(103)))],
            )

    def test_one_subject_does_not_constrain_another(self):
        ledger = self.ledger()
        ledger.record("alice", [("cohort", set(range(1000)))])
        ledger.check_differencing("bob", [("cohort", set(range(997)))])

    def test_the_budget_is_spent_per_subject_and_refuses_when_exhausted(self):
        ledger = self.ledger()
        os.environ["WASTELAND_MIMIC_BUDGET"] = "3"
        self.addCleanup(os.environ.pop, "WASTELAND_MIMIC_BUDGET", None)
        for start in range(0, 3000, 1000):
            ledger.check_budget("alice")
            ledger.record("alice", [("cohort", set(range(start, start + 500)))])
        with self.assertRaises(mimic_budget.BudgetError) as caught:
            ledger.check_budget("alice")
        self.assertIn("budget spent", str(caught.exception))
        ledger.check_budget("bob")

    def test_membership_round_trips_exactly(self):
        ledger = self.ledger()
        ids = {1, 2, 3, 99999999, 2**40}
        ledger.record("alice", [("cohort", ids)])
        row = ledger.db.execute("SELECT members FROM answered").fetchone()
        self.assertEqual(mimic_budget._unpack(row[0]), ids)


class DifferencingAttackTests(unittest.TestCase):
    """The real attack, over the real service, through the full handler."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        mimic_fixture.build(cls.root / "mimic.sqlite", patients=1400)
        (cls.root / "trust.json").write_text(json.dumps(trust_record()))
        # Find a category holding fewer than MIN_CELL stays: excluding it from an
        # otherwise identical cohort is the attack.
        db = sqlite3.connect(cls.root / "mimic.sqlite")
        counts = db.execute(
            "SELECT a.race, COUNT(*) FROM icustays s JOIN admissions a ON a.hadm_id=s.hadm_id"
            " JOIN patients p ON p.subject_id=s.subject_id"
            " WHERE p.gender='F' AND a.admission_type='ELECTIVE'"
            " GROUP BY a.race ORDER BY COUNT(*)"
        ).fetchall()
        db.close()
        cls.counts = counts
        rare = [race for race, n in counts if 0 < n < service.MIN_CELL]
        # Exclude exactly ONE rare category: removing several could legitimately
        # remove MIN_CELL or more records, which is not the attack.
        cls.rare = rare[:1]
        cls.keep = [race for race, _ in counts if race not in cls.rare]

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        if not self.rare or not self.keep:
            self.skipTest("fixture has no category below the minimum cell size")
        self.environment = tempfile.TemporaryDirectory()
        here = Path(self.environment.name)
        os.environ.update({
            "WASTELAND_MIMIC_DB": str(self.root / "mimic.sqlite"),
            "WASTELAND_CAMELOT_TRUST": str(self.root / "trust.json"),
            "WASTELAND_MIMIC_CHALLENGES": str(here / "c.sqlite"),
            "WASTELAND_MIMIC_ASSENT": str(here / "s.sqlite"),
            "WASTELAND_MIMIC_AUDIT": str(here / "a.sqlite"),
            "WASTELAND_MIMIC_LEDGER": str(here / "l.sqlite"),
            "WASTELAND_MIMIC_TRUSTED_ISSUERS": ISSUER.rsplit("/", 1)[0] + "/",
        })
        self.give_assent()

    def tearDown(self):
        for name in ("WASTELAND_MIMIC_DB", "WASTELAND_CAMELOT_TRUST",
                     "WASTELAND_MIMIC_CHALLENGES", "WASTELAND_MIMIC_ASSENT",
                     "WASTELAND_MIMIC_AUDIT", "WASTELAND_MIMIC_LEDGER",
                     "WASTELAND_MIMIC_TRUSTED_ISSUERS"):
            os.environ.pop(name, None)
        self.environment.cleanup()

    def ask(self, body):
        return mimic_handler.handle(
            {"from": "peer_lab", "id": "urn:uuid:" + body["operation"], "body": body},
            {"name": "zerzura"},
        )

    def give_assent(self):
        current = undertaking.agreement()
        created = datetime.now(timezone.utc).isoformat()
        message = undertaking.binding(
            digest=current["digest"], version=current["version"],
            subject=mint()["credentialSubject"]["id"], town="zerzura", created=created)
        self.ask({"operation": "dua-assent", "credential": mint(),
                  "assent": {"agreement": current["digest"], "version": current["version"],
                             "created": created,
                             "proofValue": b64(ed25519.sign(HOLDER_SECRET, message))}})

    def query(self, cohort):
        issued = self.ask({"operation": "mimic-challenge"})
        request = {"cohort": cohort, "aggregate": "count"}
        return self.ask({
            "operation": "mimic-aggregate", "credential": mint(), "query": request,
            "presentation": sign_presentation(issued["challenge"], HOLDER_SECRET,
                                              query=request),
        })

    def test_the_classic_differencing_attack_is_refused(self):
        excluded = dict(self.counts)[self.rare[0]]
        self.assertLess(excluded, service.MIN_CELL)  # the attack, by construction
        base = [{"field": "gender", "op": "eq", "value": "F"},
                {"field": "admission_type", "op": "eq", "value": "ELECTIVE"}]
        first = self.query(base)
        self.assertTrue(first["ok"], first.get("error"))

        # Same cohort minus one rare category. Both totals are large and legal;
        # their difference is the handful of people in that category.
        narrowed = base + [{"field": "race", "op": "in", "value": self.keep}]
        second = self.query(narrowed)
        self.assertFalse(second["ok"])
        self.assertNotIn("groups", second)
        self.assertNotIn("cohort_size", second)
        self.assertIn("subtracting the two answers", second["error"])

    def test_an_unrelated_cohort_is_still_answered(self):
        self.assertTrue(self.query(
            [{"field": "gender", "op": "eq", "value": "F"},
             {"field": "admission_type", "op": "eq", "value": "ELECTIVE"}])["ok"])
        other = self.query([{"field": "admission_type", "op": "eq", "value": "EW EMER."}])
        self.assertTrue(other["ok"], other.get("error"))

    def test_the_reply_reports_the_control_and_never_leaks_identifiers(self):
        reply = self.query([{"field": "admission_type", "op": "eq", "value": "EW EMER."}])
        self.assertEqual(reply["privacy"]["composition_control"],
                         "cohort-differencing-ledger-v1")
        self.assertIn("budget", reply["privacy"])
        encoded = json.dumps(reply)
        self.assertNotIn("stay_id", encoded)
        self.assertNotIn("members", encoded)


if __name__ == "__main__":
    unittest.main()
