"""Extracting the service's three tables from a plain-text PostgreSQL dump."""

import gzip
import tempfile
import unittest
from pathlib import Path

from examples import mimic_pgdump

# Column order deliberately differs from the order the service wants, so the
# extractor has to map by name. Includes NULLs, an embedded comma, escapes, and a
# decoy line inside an unwanted table that looks exactly like a COPY header.
DUMP = b"""--
-- PostgreSQL database dump
--
SET statement_timeout = 0;
CREATE SCHEMA mimiciv_hosp;
COPY mimiciv_hosp.admissions (subject_id, hadm_id, admittime, admission_type, insurance, race, hospital_expire_flag) FROM stdin;
10001\t20001\t2180-01-01 00:00:00\tEW EMER.\tMedicare\tBLACK/CAPE VERDEAN\t0
10002\t20002\t\\N\tURGENT\t\\N\tWHITE\t1
10003\t20003\t2180-01-03 00:00:00\tELECTIVE\tOther\tUNKNOWN, NOT SPECIFIED\t0
\\.
COPY mimiciv_hosp.chartevents (subject_id, value) FROM stdin;
10001\tan enormous table we never want
10002\tCOPY mimiciv_icu.icustays (a) FROM stdin;
10003\ttab\\there and a backslash \\\\ and \\N is null
\\.
COPY mimiciv_hosp.patients (subject_id, gender, anchor_age, anchor_year, dod) FROM stdin;
10001\tF\t52\t2180\t\\N
10002\tM\t\\N\t2181\t\\N
\\.
COPY mimiciv_icu.icustays (subject_id, hadm_id, stay_id, first_careunit, last_careunit, los) FROM stdin;
10001\t20001\t30001\tMedical Intensive Care Unit (MICU)\tMICU\t3.7025
10002\t20002\t30002\tNeuro Stepdown\tNeuro Stepdown\t0.5
\\.
--
-- PostgreSQL database dump complete
--
"""


class ExtractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        with gzip.open(cls.root / "dump.sql.gz", "wb") as handle:
            handle.write(DUMP)
        cls.counts = mimic_pgdump.extract(cls.root / "dump.sql.gz", cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def rows(self, name):
        return (self.root / f"{name}.csv").read_text().splitlines()

    def test_every_wanted_table_is_found(self):
        self.assertEqual(self.counts, {"admissions": 3, "patients": 2, "icustays": 2})

    def test_columns_are_selected_by_name_not_position(self):
        self.assertEqual(self.rows("icustays")[0],
                         "stay_id,subject_id,hadm_id,first_careunit,los")
        self.assertEqual(self.rows("icustays")[1],
                         "30001,10001,20001,Medical Intensive Care Unit (MICU),3.7025")

    def test_only_the_allowed_columns_are_written(self):
        """Admission timestamps are in the dump and must not reach the CSV."""
        text = (self.root / "admissions.csv").read_text()
        self.assertNotIn("2180-01-01", text)
        self.assertNotIn("admittime", text)

    def test_nulls_become_empty_and_commas_are_quoted(self):
        self.assertEqual(self.rows("admissions")[2], "20002,10002,URGENT,,WHITE,1")
        self.assertIn('"UNKNOWN, NOT SPECIFIED"', self.rows("admissions")[3])
        self.assertEqual(self.rows("patients")[2], "10002,M,")

    def test_an_unwanted_table_is_skipped_whole(self):
        """A data line that looks like a COPY header must not be parsed as one."""
        for name in ("patients", "admissions", "icustays"):
            self.assertNotIn("enormous table", (self.root / f"{name}.csv").read_text())
        self.assertFalse((self.root / "chartevents.csv").exists())

    def test_the_output_loads_into_the_service_schema(self):
        from examples import mimic_load, mimic_service as service
        counts = mimic_load.build(self.root, self.root / "loaded.sqlite")
        self.assertEqual(counts["icustays"], 2)
        result = service.run({"aggregate": "count", "group_by": ["care_unit"]},
                             self.root / "loaded.sqlite")
        # Both stays load, but one belongs to a patient whose anchor_age is NULL.
        # The loader drops that patient - someone with no age cannot be placed in
        # an age group - so their stay falls out of the join rather than being
        # counted with an unknown age.
        self.assertEqual([g["key"]["care_unit"] for g in result["groups"]],
                         ["Medical Intensive Care Unit (MICU)"])
        self.assertEqual(result["cohort_size"], 1)

    def test_a_record_with_a_missing_required_value_is_dropped_not_guessed(self):
        from examples import mimic_load
        import sqlite3
        mimic_load.build(self.root, self.root / "dropped.sqlite")
        db = sqlite3.connect(self.root / "dropped.sqlite")
        loaded = [row[0] for row in db.execute("SELECT subject_id FROM patients")]
        db.close()
        self.assertEqual(loaded, [10001], "the NULL-age patient should not be loaded")


class EscapeTests(unittest.TestCase):
    def test_postgres_escapes_are_decoded(self):
        self.assertEqual(mimic_pgdump._unescape(rb"plain"), "plain")
        self.assertEqual(mimic_pgdump._unescape(rb"a\tb"), "a\tb")
        self.assertEqual(mimic_pgdump._unescape(rb"a\\b"), "a\\b")
        self.assertEqual(mimic_pgdump._unescape(rb"line\nbreak"), "line\nbreak")
        self.assertEqual(mimic_pgdump._unescape(rb"\N"), "")


if __name__ == "__main__":
    unittest.main()
