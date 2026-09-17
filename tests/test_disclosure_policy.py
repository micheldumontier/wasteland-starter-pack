"""Disclosure control belongs to the dataset, and defaults to strict."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from examples import mimic_fixture
from examples import mimic_service as service


def declare(path, **meta):
    db = sqlite3.connect(path)
    db.executemany("INSERT OR REPLACE INTO dataset_meta VALUES(?,?)", sorted(meta.items()))
    db.commit()
    db.close()


class PolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        mimic_fixture.build(cls.root / "private.sqlite", patients=400)
        mimic_fixture.build(cls.root / "public.sqlite", patients=400)
        declare(cls.root / "public.sqlite", public="true",
                licence="Open Data Commons Open Database License v1.0")
        # A database with no dataset_meta at all: what real MIMIC-IV looks like.
        bare = sqlite3.connect(cls.root / "bare.sqlite")
        bare.executescript(mimic_fixture.SCHEMA)
        bare.execute("DROP TABLE dataset_meta")
        bare.commit()
        bare.close()

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_a_public_dataset_gets_no_suppression(self):
        control = service.policy(self.root / "public.sqlite")
        self.assertIsNone(control["minimum_cell_size"])
        self.assertEqual(control["disclosure_control"], "none")
        self.assertIn("already downloadable", control["reason"])

    def test_an_undeclared_dataset_is_treated_as_private(self):
        control = service.policy(self.root / "private.sqlite")
        self.assertEqual(control["minimum_cell_size"], service.MIN_CELL)
        self.assertEqual(control["disclosure_control"], "cell-suppression")

    def test_a_database_without_metadata_is_treated_as_private(self):
        """Real MIMIC-IV carries no dataset_meta. It must not fail open."""
        control = service.policy(self.root / "bare.sqlite")
        self.assertEqual(control["minimum_cell_size"], service.MIN_CELL)

    def test_an_unparseable_declaration_is_treated_as_private(self):
        path = self.root / "confused.sqlite"
        mimic_fixture.build(path, patients=400)
        for value in ("TRUE", "yes", "1", "", "maybe"):
            declare(path, public=value)
            with self.subTest(public=value):
                self.assertEqual(service.policy(path)["minimum_cell_size"], service.MIN_CELL)

    def test_a_missing_database_is_treated_as_private(self):
        self.assertEqual(
            service.policy(self.root / "absent.sqlite")["minimum_cell_size"],
            service.MIN_CELL,
        )


class LoaderDeclarationTests(unittest.TestCase):
    """Only the bundled demo may declare itself public without being asked to."""

    def test_the_bundled_demo_declares_itself_public(self):
        from examples.mimic_load import BUNDLED, descriptor
        self.assertEqual(descriptor(BUNDLED).get("public"), "true")

    def test_any_other_source_is_not_public_by_default(self):
        """Loading credentialed data must not inherit the demo's declaration."""
        from examples.mimic_load import descriptor
        for source in ("/home/someone/mimic-iv", "/mnt/data", "./elsewhere"):
            with self.subTest(source=source):
                self.assertIsNone(descriptor(source).get("public"))

    def test_a_database_built_from_another_source_gets_full_control(self):
        from examples import mimic_load
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mimic_fixture.build(root / "seed.sqlite", patients=50)
            # Re-export the fixture as CSVs so the loader has something to read.
            import csv, sqlite3
            db = sqlite3.connect(root / "seed.sqlite")
            for table, columns in (("patients", ("subject_id", "gender", "anchor_age")),
                                   ("admissions", ("hadm_id", "subject_id", "admission_type",
                                                   "insurance", "race", "hospital_expire_flag")),
                                   ("icustays", ("stay_id", "subject_id", "hadm_id",
                                                 "first_careunit", "los"))):
                with (root / f"{table}.csv").open("w", newline="") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(columns)
                    writer.writerows(db.execute(f"SELECT {','.join(columns)} FROM {table}"))
            db.close()
            out = root / "loaded.sqlite"
            mimic_load.build(root, out, mimic_load.descriptor(root))
            self.assertEqual(service.policy(out)["minimum_cell_size"], service.MIN_CELL)
            self.assertEqual(service.policy(out)["disclosure_control"], "cell-suppression")


class SuppressionFollowsThePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        for name in ("private.sqlite", "public.sqlite"):
            mimic_fixture.build(cls.root / name, patients=300)
        declare(cls.root / "public.sqlite", public="true")
        cls.narrow = {
            "cohort": [{"field": "age_group", "op": "eq", "value": "18-29"}],
            "aggregate": "count",
            "group_by": ["race", "care_unit"],
        }

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_small_groups_are_suppressed_on_a_private_dataset(self):
        result = service.run(self.narrow, self.root / "private.sqlite")
        self.assertTrue(any("suppressed" in group for group in result["groups"]))
        self.assertEqual(result["privacy"]["disclosure_control"], "cell-suppression")

    def test_the_same_query_reports_every_group_on_a_public_dataset(self):
        result = service.run(self.narrow, self.root / "public.sqlite")
        self.assertFalse(result["suppressed"])
        self.assertTrue(result["groups"])
        for group in result["groups"]:
            self.assertNotIn("suppressed", group)
            self.assertIsNotNone(group["n"])
        self.assertEqual(result["privacy"]["disclosure_control"], "none")
        self.assertIsNone(result["privacy"]["minimum_cell_size"])

    def test_a_tiny_cohort_is_still_reported_on_a_public_dataset(self):
        tiny = {"cohort": [{"field": "gender", "op": "eq", "value": "F"},
                           {"field": "age_group", "op": "eq", "value": "18-29"},
                           {"field": "insurance", "op": "eq", "value": "Medicaid"},
                           {"field": "care_unit", "op": "eq", "value": "Neuro ICU"}],
                "aggregate": "count"}
        public = service.run(tiny, self.root / "public.sqlite")
        private = service.run(tiny, self.root / "private.sqlite")
        self.assertIsNotNone(public["cohort_size"])
        # The same query against the undeclared copy withholds even the total.
        if private["cohort_size"] is not None:
            self.skipTest("fixture cohort is not small enough to exercise this")
        self.assertTrue(private["suppressed"])

    def test_the_reply_explains_which_control_applied_and_why(self):
        for name, expected in (("public.sqlite", "none"), ("private.sqlite", "cell-suppression")):
            privacy = service.run({"aggregate": "count"}, self.root / name)["privacy"]
            with self.subTest(dataset=name):
                self.assertEqual(privacy["disclosure_control"], expected)
                self.assertTrue(privacy["disclosure_control_reason"])
                self.assertFalse(privacy["differential_privacy"])
                self.assertFalse(privacy["row_level_data_returned"])


if __name__ == "__main__":
    unittest.main()


class IsolationTests(unittest.TestCase):
    """Tests must never write to the town's own state directory."""

    def test_every_store_default_points_inside_dot_town(self):
        """If a default moves outside .town, this guard needs revisiting."""
        from examples import agreement, mimic_budget, mimic_presentation
        for default in (service.DEFAULT_DB, mimic_budget.DEFAULT_LEDGER,
                        agreement.DEFAULT_STORE, mimic_presentation.DEFAULT_STORE):
            self.assertTrue(str(default).startswith(".town/"), default)

    def test_no_store_was_written_in_the_repository_during_this_run(self):
        """A test that forgets to redirect a store leaves a file here."""
        stray = sorted(p.name for p in Path(".").glob("*.sqlite"))
        self.assertEqual(stray, [], f"tests leaked state into the repository: {stray}")
