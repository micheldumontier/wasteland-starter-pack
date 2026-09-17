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

    python3 -m examples.mimic_load --out .town/mimic.sqlite

The three tables it reads are bundled in ``examples/data/mimic-iv-demo`` under
the ODbL; see the LICENCE.txt beside them. Pass ``--source`` to load a copy you
downloaded yourself.
"""

import argparse
import csv
import gzip
import io
import shutil
import sqlite3
import tempfile
from pathlib import Path

from .mimic_fixture import INDEXES, SCHEMA

CITATION = (
    "Johnson, A., Bulgarelli, L., Pollard, T., Horng, S., Celi, L. A., & Mark, R. "
    "(2023). MIMIC-IV Clinical Database Demo (version 2.2). PhysioNet. "
    "https://doi.org/10.13026/dp1f-ex47"
)
DEMO_META = {
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
# Without these a row cannot be placed at all. Everything else may be missing:
# dropping a whole admission because its insurance is unknown would quietly
# remove it from every query, including ones that never mention insurance.
REQUIRED = {
    "patients": {"subject_id", "gender", "anchor_age"},
    "admissions": {"hadm_id", "subject_id", "admission_type", "race",
                   "hospital_expire_flag"},
    "icustays": {"stay_id", "subject_id", "hadm_id", "first_careunit"},
}

# The three tables this service reads are bundled, under their own licence, so
# that the repository's checks run against real data without a download.
BUNDLED = Path(__file__).parent / "data" / "mimic-iv-demo"


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


def descriptor(source, name=None, public=False, description=None):
    """Metadata for the database being built.

    The bundled demo is the only source that may declare itself public, and only
    when it is loaded from where it is bundled. Anything else is described
    conservatively and left undeclared, so the service applies full disclosure
    control to it. Declaring a dataset public is an explicit act, never a default
    inherited from whichever loader happened to be convenient.
    """
    if Path(source).resolve() == BUNDLED.resolve():
        return dict(DEMO_META)
    meta = {
        "name": name or "unlabelled",
        "synthetic": "false",
        "source": str(source),
        "description": description or (
            "Loaded from a local source. Not declared public, so full disclosure "
            "control applies."
        ),
    }
    if public:
        meta["public"] = "true"
        meta["licence"] = "declared public by the operator at load time"
    return meta


def build(source, out, meta=None):
    """Load the CSVs into a fresh database.

    The database is assembled on local scratch and moved into place at the end.
    Workspace volumes are often network-backed, where SQLite's many small synced
    writes are an order of magnitude slower than one sequential copy of the
    finished file.
    """
    source, out = Path(source), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    scratch = Path(tempfile.mkdtemp())
    staged = scratch / "building.sqlite"
    db = sqlite3.connect(staged)
    # Durability during the build is worthless: a failure means loading again.
    db.execute("PRAGMA journal_mode = OFF")
    db.execute("PRAGMA synchronous = OFF")
    db.executescript(SCHEMA)
    counts = {}
    for table, (relative, columns) in TABLES.items():
        rows = []
        with _open(source, relative) as handle:
            for record in csv.DictReader(handle):
                values = [_cast(column, record.get(column)) for column in columns]
                if any(value is None and column in REQUIRED[table]
                       for column, value in zip(columns, values)):
                    continue
                rows.append(values)
        db.executemany(
            f"INSERT OR IGNORE INTO {table} VALUES({','.join('?' * len(columns))})", rows
        )
        counts[table] = len(rows)
    db.executemany("INSERT INTO dataset_meta VALUES(?,?)",
                   sorted((meta or descriptor(source)).items()))
    db.executescript(INDEXES)
    db.commit()
    db.close()
    _verify(staged, counts)
    shutil.move(str(staged), str(out))
    shutil.rmtree(scratch, ignore_errors=True)
    out.chmod(0o600)
    # Verified again at the destination: a database is assembled on local scratch
    # and moved onto a workspace volume, and a truncated or half-written copy
    # must not be served rather than noticed.
    _verify(out, counts)
    return counts


def _verify(path, counts):
    """Refuse to hand back a database that is not intact and complete."""
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        state = db.execute("PRAGMA integrity_check").fetchone()[0]
        if state != "ok":
            raise RuntimeError(f"{path} failed integrity check: {state[:200]}")
        for table, expected in counts.items():
            actual = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if actual != expected:
                raise RuntimeError(
                    f"{path}: {table} holds {actual:,} rows, expected {expected:,}")
    except sqlite3.DatabaseError as error:
        raise RuntimeError(f"{path} is not a readable database: {error}") from None
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=BUNDLED,
                        help=f"directory holding the demo CSVs (default: {BUNDLED})")
    parser.add_argument("--out", type=Path, default=Path(".town/mimic.sqlite"))
    parser.add_argument("--name", help="dataset name recorded in the database")
    parser.add_argument("--description")
    parser.add_argument(
        "--declare-public", action="store_true",
        help="ONLY for data anyone may already download. Disables cell suppression "
             "and the composition ledger for this database.",
    )
    args = parser.parse_args()
    meta = descriptor(args.source, args.name, args.declare_public, args.description)
    counts = build(args.source, args.out, meta)
    print(f"Wrote {args.out}: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    print("Dataset :", meta["name"])
    public = meta.get("public") == "true"
    print("Public  :", public, "-- no disclosure control" if public
          else "-- full disclosure control applies")
    if "licence" in meta:
        print("Licence :", meta["licence"])
    if meta is DEMO_META or meta.get("name") == DEMO_META["name"]:
        print("Cite    :", CITATION)


if __name__ == "__main__":
    main()
