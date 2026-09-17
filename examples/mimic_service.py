"""Aggregate-only query service over a MIMIC-IV-shaped database.

Requesters never supply SQL. They supply a structured query whose every field
name is checked against an allowlist below; only the *values* reach the database,
as bound parameters. The unit of analysis is one ICU stay.

No row-level value is ever returned. Every reported group is checked against a
minimum cell size, and a group that falls short is suppressed rather than
rounded, along with a second group where the total would otherwise reveal it.
"""

import os
import sqlite3
from pathlib import Path

DEFAULT_DB = ".town/mimic.sqlite"

# field name -> SQL expression. Server-defined; a requester can name one, never write one.
DIMENSIONS = {
    "gender": "p.gender",
    "age_group": (
        "CASE WHEN p.anchor_age < 30 THEN '18-29'"
        " WHEN p.anchor_age < 45 THEN '30-44'"
        " WHEN p.anchor_age < 60 THEN '45-59'"
        " WHEN p.anchor_age < 75 THEN '60-74'"
        " ELSE '75+' END"
    ),
    "admission_type": "a.admission_type",
    "insurance": "a.insurance",
    "race": "a.race",
    "care_unit": "s.first_careunit",
}
MEASURES = {"los_days": "s.los", "age_years": "p.anchor_age"}
AGGREGATES = ("count", "mean", "median", "mortality_rate")
OPERATORS = ("eq", "in")

MIN_CELL = 10          # groups smaller than this are suppressed, never rounded
                       # ...unless the dataset declares itself public; see policy()
MAX_FILTERS = 8
MAX_IN_VALUES = 20
MAX_GROUP_BY = 2
MAX_GROUPS = 50        # refuse rather than truncate; a truncated table plus a total leaks
MAX_ROWS = 1_000_000
MAX_VALUE_CHARS = 64

FROM_CLAUSE = (
    "FROM icustays s"
    " JOIN admissions a ON a.hadm_id = s.hadm_id"
    " JOIN patients p ON p.subject_id = s.subject_id"
)


class QueryError(ValueError):
    """The query was rejected before it reached the database."""


def database_path():
    return Path(os.environ.get("WASTELAND_MIMIC_DB", DEFAULT_DB))


def schema():
    """The public contract: what a requester is allowed to ask for."""
    return {
        "unit_of_analysis": "icu_stay",
        "dimensions": sorted(DIMENSIONS),
        "measures": sorted(MEASURES),
        "aggregates": list(AGGREGATES),
        "operators": list(OPERATORS),
        "limits": {
            "minimum_cell_size": MIN_CELL,
            "max_filters": MAX_FILTERS,
            "max_values_per_filter": MAX_IN_VALUES,
            "max_group_by": MAX_GROUP_BY,
            "max_groups_returned": MAX_GROUPS,
        },
    }


def policy(path=None):
    """How much disclosure control this dataset needs.

    A dataset that declares itself public gets none: suppressing a cell of three
    when anyone can download the rows protects nothing, and pretending otherwise
    misrepresents the control. Anything that does not declare itself public --
    including any database with no ``dataset_meta`` table, which is what real
    MIMIC-IV looks like -- gets the full treatment. The default is strict.
    """
    meta = dataset(path)
    if meta.get("public") is True:
        return {
            "minimum_cell_size": None,
            "disclosure_control": "none",
            "revocation_check": "not-applicable",
            "reason": (
                "this dataset declares itself openly licensed; its row-level data "
                "is already downloadable by anyone, so suppression would protect "
                "nothing"
            ),
        }
    return {
        "minimum_cell_size": MIN_CELL,
        "disclosure_control": "cell-suppression",
        "revocation_check": "required",
        "reason": "dataset is not declared public; full disclosure control applies",
    }


def _check_value(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise QueryError("filter values must be a string or number")
    if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
        raise QueryError(f"filter values must be at most {MAX_VALUE_CHARS} characters")
    return value


def validate(query):
    """Return a normalised query, or raise QueryError. Touches no data."""
    if not isinstance(query, dict):
        raise QueryError("query must be an object")
    unknown = set(query) - {"cohort", "aggregate", "measure", "group_by"}
    if unknown:
        raise QueryError("unknown query fields: " + ", ".join(sorted(unknown)))

    aggregate = query.get("aggregate", "count")
    if aggregate not in AGGREGATES:
        raise QueryError("aggregate must be one of: " + ", ".join(AGGREGATES))

    measure = query.get("measure")
    if aggregate in ("mean", "median"):
        if measure not in MEASURES:
            raise QueryError(
                f"aggregate '{aggregate}' needs measure: " + ", ".join(sorted(MEASURES))
            )
    elif measure is not None:
        raise QueryError(f"aggregate '{aggregate}' does not take a measure")

    cohort = query.get("cohort", [])
    if not isinstance(cohort, list):
        raise QueryError("cohort must be a list of filters")
    if len(cohort) > MAX_FILTERS:
        raise QueryError(f"at most {MAX_FILTERS} filters")
    filters = []
    for entry in cohort:
        if not isinstance(entry, dict):
            raise QueryError("each filter must be an object")
        field, operator = entry.get("field"), entry.get("op", "eq")
        if field not in DIMENSIONS:
            raise QueryError(f"unknown filter field: {str(field)[:40]}")
        if operator not in OPERATORS:
            raise QueryError("filter op must be one of: " + ", ".join(OPERATORS))
        value = entry.get("value")
        if operator == "in":
            if not isinstance(value, list) or not 1 <= len(value) <= MAX_IN_VALUES:
                raise QueryError(f"'in' needs 1-{MAX_IN_VALUES} values")
            value = [_check_value(item) for item in value]
        else:
            value = _check_value(value)
        filters.append({"field": field, "op": operator, "value": value})

    group_by = query.get("group_by", [])
    if isinstance(group_by, str):
        group_by = [group_by]
    if not isinstance(group_by, list):
        raise QueryError("group_by must be a list of field names")
    if len(group_by) > MAX_GROUP_BY:
        raise QueryError(f"at most {MAX_GROUP_BY} group_by fields")
    for field in group_by:
        if field not in DIMENSIONS:
            raise QueryError(f"unknown group_by field: {str(field)[:40]}")
    if len(set(group_by)) != len(group_by):
        raise QueryError("group_by fields must be distinct")

    normalised = {"cohort": filters, "aggregate": aggregate, "group_by": list(group_by)}
    if measure is not None:
        normalised["measure"] = measure
    return normalised


def _where(filters):
    clauses, params = [], []
    for entry in filters:
        column = DIMENSIONS[entry["field"]]
        if entry["op"] == "in":
            clauses.append(f"{column} IN ({','.join('?' * len(entry['value']))})")
            params.extend(entry["value"])
        else:
            clauses.append(f"{column} = ?")
            params.append(entry["value"])
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def _fetch(query, path):
    selected = [DIMENSIONS[field] for field in query["group_by"]]
    measure = MEASURES.get(query.get("measure"), "NULL")
    where, params = _where(query["cohort"])
    # stay_id is selected for the local ledger only; it never enters a reply.
    sql = (
        "SELECT " + ", ".join(selected + [measure, "a.hospital_expire_flag", "s.stay_id"])
        + " " + FROM_CLAUSE + where + f" LIMIT {MAX_ROWS + 1}"
    )
    if not path.exists():
        raise QueryError("no MIMIC database is configured on this town")
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = db.execute(sql, params).fetchall()
    except sqlite3.Error as error:
        raise QueryError(f"database rejected the query: {type(error).__name__}") from None
    finally:
        db.close()
    if len(rows) > MAX_ROWS:
        raise QueryError("cohort too large; add filters")
    return rows


def _summarise(values, expired, aggregate):
    if aggregate == "count":
        return len(values)
    if aggregate == "mortality_rate":
        return round(sum(expired) / len(expired), 3)
    numbers = sorted(value for value in values if value is not None)
    if not numbers:
        return None
    if aggregate == "mean":
        return round(sum(numbers) / len(numbers), 2)
    middle = len(numbers) // 2
    median = (
        numbers[middle]
        if len(numbers) % 2
        else (numbers[middle - 1] + numbers[middle]) / 2
    )
    return round(median, 2)


MISSING = "(missing)"


def group_label(key):
    """Stable name for a group, shared with the ledger."""
    return ",".join(f"{k}={v}" for k, v in sorted(key.items())) or "all"


def run(query, path=None, with_membership=False):
    """Execute a validated query and return only suppressed aggregates.

    With ``with_membership``, also returns which records answered it, for the
    local differencing ledger. That structure must never reach a reply.
    """
    query = validate(query)
    path = Path(path) if path else database_path()
    rows = _fetch(query, path)
    control = policy(path)
    minimum = control["minimum_cell_size"]
    width = len(query["group_by"])

    buckets = {}
    for row in rows:
        key = tuple(row[:width])
        values, expired, members = buckets.setdefault(key, ([], [], set()))
        values.append(row[width])
        expired.append(row[width + 1])
        members.add(row[width + 2])

    if len(buckets) > MAX_GROUPS:
        raise QueryError(
            f"{len(buckets)} groups exceeds the {MAX_GROUPS} returned; narrow the cohort"
        )

    groups = [
        {
            "key": {field: MISSING if value is None else value
                    for field, value in zip(query["group_by"], key)},
            "n": len(values),
            "value": _summarise(values, expired, query["aggregate"]),
            # A measure can be missing where the record is not: reporting a mean
            # over fewer records than n, without saying so, would mislead.
            **({"measured": sum(1 for v in values if v is not None)}
               if query["aggregate"] in ("mean", "median")
               and sum(1 for v in values if v is not None) != len(values) else {}),
        }
        for key, (values, expired, _) in buckets.items()
    ]
    membership = {
        "cohort": {row[width + 2] for row in rows},
        "groups": {
            group_label(dict(zip(query["group_by"], key))): members
            for key, (_, _, members) in buckets.items()
        },
    }
    groups.sort(key=lambda group: (-group["n"], str(group["key"])))
    cohort_size = len(rows)

    notes = []
    if minimum is not None and cohort_size < minimum:
        # Reporting even the total would describe fewer than MIN_CELL stays.
        withheld = {
            "cohort_size": None,
            "groups": [],
            "suppressed": True,
            "privacy": _privacy(["whole cohort below the minimum cell size"], control),
            "query": query,
        }
        return (withheld, {"cohort": set(), "groups": {}}) if with_membership else withheld

    small = [] if minimum is None else [g for g in groups if g["n"] < minimum]
    for group in small:
        group["suppressed"] = "below-minimum-cell-size"
    if small:
        notes.append(f"{len(small)} group(s) below the minimum cell size")
    # One suppressed group is recoverable by subtracting the rest from the total.
    if len(small) == 1 and len(groups) > 1:
        remaining = [group for group in groups if "suppressed" not in group]
        if remaining:
            victim = min(remaining, key=lambda group: group["n"])
            victim["suppressed"] = "complementary-suppression"
            notes.append("a second group suppressed so the first cannot be derived")
    for group in groups:
        if "suppressed" in group:
            group["n"] = None
            group["value"] = None

    result = {
        "cohort_size": cohort_size,
        "groups": groups,
        "suppressed": bool(small),
        "privacy": _privacy(notes, control),
        "query": query,
    }
    return (result, membership) if with_membership else result


def _privacy(notes, control=None):
    control = control or policy()
    applied = control["disclosure_control"]
    return {
        "method": f"allowlisted-query-with-{applied}-v1",
        "disclosure_control": applied,
        "disclosure_control_reason": control["reason"],
        "minimum_cell_size": control["minimum_cell_size"],
        "row_level_data_returned": False,
        "differential_privacy": False,
        "notes": notes,
    }


def dataset(path=None):
    """Describe the backing database, so a caller can tell synthetic from real."""
    path = Path(path) if path else database_path()
    if not path.exists():
        return {"configured": False}
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        meta = dict(db.execute("SELECT key, value FROM dataset_meta").fetchall())
    except sqlite3.Error:
        # A real MIMIC-IV database carries no such table; say so rather than guess.
        meta = {"name": "unlabelled", "synthetic": "unknown"}
    try:
        meta["icu_stays"] = db.execute("SELECT COUNT(*) FROM icustays").fetchone()[0]
    except sqlite3.Error:
        meta["icu_stays"] = None
    finally:
        db.close()
    meta["configured"] = True
    meta["synthetic"] = {"true": True, "false": False}.get(meta.get("synthetic"), "unknown")
    # Absent or unparseable means not public. Never fail open.
    meta["public"] = meta.get("public") == "true"
    return meta
