"""Build a synthetic database in MIMIC-IV's shape, for developing the service.

The real service reads whatever database ``WASTELAND_MIMIC_DB`` points at. This
module only exists so the handler can be built and tested without credentialed
data present. Nothing here is a real patient record.

    python3 -m examples.mimic_fixture --out .town/mimic.sqlite
"""

import argparse
import random
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE patients (
    subject_id INTEGER PRIMARY KEY,
    gender TEXT NOT NULL,
    anchor_age INTEGER NOT NULL
);
CREATE TABLE admissions (
    hadm_id INTEGER PRIMARY KEY,
    subject_id INTEGER NOT NULL,
    admission_type TEXT NOT NULL,
    insurance TEXT,
    race TEXT NOT NULL,
    hospital_expire_flag INTEGER NOT NULL
);
CREATE TABLE icustays (
    stay_id INTEGER PRIMARY KEY,
    subject_id INTEGER NOT NULL,
    hadm_id INTEGER NOT NULL,
    first_careunit TEXT NOT NULL,
    los REAL
);
CREATE TABLE dataset_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# Applied after bulk loading: building an index once beats maintaining it per row.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_adm_subject ON admissions(subject_id);
CREATE INDEX IF NOT EXISTS idx_icu_hadm ON icustays(hadm_id);
"""

META = {
    "name": "synthetic-mimic-iv-shaped",
    "version": "fixture-1",
    "synthetic": "true",
    "licence": "not applicable (generated data)",
    "source": "examples/mimic_fixture.py",
    "description": "Generated records in MIMIC-IV's schema. No real patients.",
}

GENDERS = ["F", "M"]
ADMISSION_TYPES = ["EW EMER.", "URGENT", "ELECTIVE", "OBSERVATION ADMIT", "SURGICAL SAME DAY ADMISSION"]
INSURANCE = ["Medicare", "Medicaid", "Other"]
RACE = ["WHITE", "BLACK/AFRICAN AMERICAN", "HISPANIC/LATINO", "ASIAN", "OTHER", "UNKNOWN"]
CARE_UNITS = ["Medical ICU", "Surgical ICU", "Cardiac Vascular ICU", "Neuro ICU", "Trauma SICU"]


def build(path, patients=1400, seed=20260915):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    rng = random.Random(seed)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.executescript(INDEXES)
    hadm_id = 20000000
    stay_id = 30000000
    for subject_id in range(10000000, 10000000 + patients):
        gender = rng.choice(GENDERS)
        age = min(91, max(18, int(rng.gauss(64, 17))))
        db.execute("INSERT INTO patients VALUES(?,?,?)", (subject_id, gender, age))
        for _ in range(rng.choices([1, 2, 3], weights=[72, 21, 7])[0]):
            hadm_id += 1
            admission_type = rng.choices(ADMISSION_TYPES, weights=[46, 17, 16, 12, 9])[0]
            # Older, emergency admissions die more often; keeps aggregates non-trivial.
            risk = 0.03 + (age - 18) * 0.0022 + (0.05 if admission_type == "EW EMER." else 0.0)
            expired = 1 if rng.random() < risk else 0
            db.execute(
                "INSERT INTO admissions VALUES(?,?,?,?,?,?)",
                (
                    hadm_id,
                    subject_id,
                    admission_type,
                    rng.choices(INSURANCE, weights=[54, 17, 29])[0],
                    rng.choices(RACE, weights=[62, 12, 9, 7, 6, 4])[0],
                    expired,
                ),
            )
            for _ in range(rng.choices([1, 2], weights=[88, 12])[0]):
                stay_id += 1
                los = round(max(0.1, rng.lognormvariate(0.55, 0.95)), 4)
                db.execute(
                    "INSERT INTO icustays VALUES(?,?,?,?,?)",
                    (stay_id, subject_id, hadm_id, rng.choice(CARE_UNITS), los),
                )
    db.executemany("INSERT INTO dataset_meta VALUES(?,?)", sorted(META.items()))
    db.commit()
    counts = {
        table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("patients", "admissions", "icustays")
    }
    db.close()
    path.chmod(0o600)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(".town/mimic.sqlite"))
    parser.add_argument("--patients", type=int, default=1400)
    args = parser.parse_args()
    counts = build(args.out, args.patients)
    print(f"Wrote {args.out}: " + ", ".join(f"{v} {k}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
