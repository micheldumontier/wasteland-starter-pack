"""Load the open MIMIC-IV Clinical Database Demo into this town's schema.

100 real, de-identified patients from Beth Israel Deaconess, distributed under
the Open Data Commons Open Database License v1.0. No credentialed account and
no data use agreement are needed to download it:

    https://physionet.org/content/mimic-iv-demo/2.2/

Because every row is already public, a database built by this loader declares
itself public, and the service applies no disclosure control to it. That is
deliberate: suppressing a cell of three, when anyone can download the rows
themselves, protects nothing and misrepresents what the town is doing.

A database built from *credentialed* MIMIC-IV must not carry that declaration.
Databases with no ``dataset_meta`` table are treated as non-public by default.

    python3 -m examples.mimic_load --source ./mimic-iv-demo --out .town/mimic.sqlite
"""

import argparse
import csv
import gzip
import io
import sqlite3
from pathlib import Path

from .mimic_fixture import SCHEMA

CITATION = (
    "Johnson, A., Bulgarelli, L., Pollard, T., Horng, S., Celi, L. A., & Mark, R. "
    "(2023). MIMIC-IV Clinical Database Demo (version 2.2). PhysioNet. "
    "https://doi.org/10.13026/dp1f-ex47"
)
META = {
    "name": "mimic-iv-clinical-database-demo",
    "version": "2.2",
    "synthetic": "false",
    "public": "true",
    "licence": "Open Data Commons Open Database License v1.0",
    "source": "https://physionet.org/content/mimic-iv-demo/2.2/",
    "citation": CITATION,
    "description": (
        "100 real de-identified patients from Beth Israel Deaconess Medical "
        "Center. Openly licensed; row-level data is downloadable by anyone."
    ),
}
TABLES = {
    "patients": ("hosp/patients", ("subject_id", "gender", "anchor_age")),
    "admissions": (
        "hosp/admissions",
        ("hadm_id", "subject_id", "admission_type", "insurance", "race",
         "hospital_expire_flag"),
    ),
    "icustays": (
        "icu/icustays",
        ("stay_id", "subject_id", "hadm_id", "first_careunit", "los"),
    ),
}
NUMERIC = {"subject_id", "hadm_id", "stay_id", "anchor_age", "hospital_expire_flag", "los"}


def _open(source, relative):
    """Accept the published layout, or a flat directory, gzipped or not."""
    stem = Path(relative).name
    for candidate in (
        source / f"{relative}.csv.gz", source / f"{relative}.csv",
        source / f"{stem}.csv.gz", source / f"{stem}.csv",
    ):
        if candidate.exists():
            if candidate.suffix == ".gz":
                return io.TextIOWrapper(gzip.open(candidate, "rb"), encoding="utf-8")
            return candidate.open(encoding="utf-8")
    raise FileNotFoundError(
        f"no {stem}.csv[.gz] under {source}; download the demo from "
        "https://physionet.org/content/mimic-iv-demo/2.2/"
    )


def _cast(column, value):
    if value in ("", None):
        return None
    if column in NUMERIC:
        try:
            return float(value) if column == "los" else int(value)
        except ValueError:
            return None
    return value


def build(source, out):
    source, out = Path(source), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    db = sqlite3.connect(out)
    db.executescript(SCHEMA)
    counts = {}
    for table, (relative, columns) in TABLES.items():
        rows = []
        with _open(source, relative) as handle:
            for record in csv.DictReader(handle):
                values = [_cast(column, record.get(column)) for column in columns]
                # A stay with no length, or a row missing its keys, cannot be counted.
                if any(value is None for value in values):
                    continue
                rows.append(values)
        db.executemany(
            f"INSERT OR IGNORE INTO {table} VALUES({','.join('?' * len(columns))})", rows
        )
        counts[table] = len(rows)
    db.executemany("INSERT INTO dataset_meta VALUES(?,?)", sorted(META.items()))
    db.commit()
    db.close()
    out.chmod(0o600)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="directory holding the demo CSVs")
    parser.add_argument("--out", type=Path, default=Path(".town/mimic.sqlite"))
    args = parser.parse_args()
    counts = build(args.source, args.out)
    print(f"Wrote {args.out}: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    print("Licence:", META["licence"])
    print("Cite   :", CITATION)


if __name__ == "__main__":
    main()
