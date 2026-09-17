"""Refuse query sequences that isolate people, even when each query is legal.

Cell suppression works one query at a time. It cannot see that a requester has
asked for "women in the medical ICU" and then "women in the medical ICU who are
not aged 18-29": both cohorts are large, both answers are released, and the
difference describes three people.

So this town remembers which records answered each request, per credential
subject, and refuses a new request whose membership differs from something it
already answered by a non-zero amount smaller than the minimum cell size. It
also caps how many requests a subject may make in a window, because a large
enough family of legal queries is itself an attack.

Membership sets are record identifiers. They stay in a local ledger, are never
placed in a reply, and are what makes the check exact rather than heuristic.
"""

import array
import os
import sqlite3
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .mimic_service import MIN_CELL

DEFAULT_LEDGER = ".town/mimic-ledger.sqlite"
DEFAULT_BUDGET = 50          # requests per subject per window
DEFAULT_WINDOW_HOURS = 24
DEFAULT_HISTORY = 400        # membership sets retained per subject


class BudgetError(ValueError):
    """The request was refused because of what it would reveal in combination."""


def _setting(name, default, cast=int):
    try:
        return cast(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _pack(indices, width):
    """Membership as a bitmap over the ledger's dense index space.

    A cohort is a subset of a fixed universe, so a bitmap is the natural
    representation: comparing two of them is a bitwise AND and a popcount, both
    of which run in C. Sets of a hundred thousand Python integers do not.
    """
    bitmap = bytearray((width + 7) // 8)
    for index in indices:
        bitmap[index >> 3] |= 1 << (index & 7)
    return zlib.compress(bytes(bitmap), 6)


def _unpack(blob):
    """The bitmap as one big integer, for bitwise comparison.

    Little-endian, so that index *i* is bit *i* whatever the stored width. A
    bitmap written when the universe was smaller must still compare correctly
    against one written later.
    """
    return int.from_bytes(zlib.decompress(blob), "little")


def _difference_counts(first, first_size, second, second_size):
    """|first - second| and |second - first|, exactly.

    Both follow from the size of the intersection, which is the population count
    of the bitwise AND -- one C-level operation over a few kilobytes, whatever
    the cohort size.
    """
    shared = (first & second).bit_count()
    return first_size - shared, second_size - shared


class CohortLedger:
    """What each subject has already been told, as record membership."""

    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("WASTELAND_MIMIC_LEDGER", DEFAULT_LEDGER))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS answered ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT NOT NULL,"
            " recorded TEXT NOT NULL, label TEXT NOT NULL, size INTEGER NOT NULL,"
            " members BLOB NOT NULL)"
        )
        # Record identifiers are mapped to dense indices so that membership can be
        # held as a bitmap. The mapping never leaves this file.
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS universe ("
            "member INTEGER PRIMARY KEY, idx INTEGER NOT NULL UNIQUE)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_answered_subject ON answered(subject)"
        )
        self.db.commit()
        self.path.chmod(0o600)
        self._index = dict(self.db.execute("SELECT member, idx FROM universe"))

    def _indices(self, members, assign=True):
        """Dense indices for these record ids, assigning new ones when recording."""
        indices, fresh = [], []
        for member in members:
            index = self._index.get(member)
            if index is None:
                if not assign:
                    continue  # never seen: it is in no prior set, which is what we want
                index = len(self._index)
                self._index[member] = index
                fresh.append((member, index))
            indices.append(index)
        if fresh:
            self.db.executemany("INSERT OR IGNORE INTO universe VALUES(?,?)", fresh)
        return indices

    def _width(self):
        return max(len(self._index), 1)

    def spent(self, subject):
        window = datetime.now(timezone.utc) - timedelta(
            hours=_setting("WASTELAND_MIMIC_BUDGET_WINDOW", DEFAULT_WINDOW_HOURS)
        )
        return self.db.execute(
            "SELECT COUNT(DISTINCT recorded) FROM answered WHERE subject=? AND recorded>=?",
            (subject, window.isoformat()),
        ).fetchone()[0]

    def check_budget(self, subject):
        budget = _setting("WASTELAND_MIMIC_BUDGET", DEFAULT_BUDGET)
        spent = self.spent(subject)
        if spent >= budget:
            raise BudgetError(
                f"query budget spent: {spent} of {budget} in the last "
                f"{_setting('WASTELAND_MIMIC_BUDGET_WINDOW', DEFAULT_WINDOW_HOURS)}h; "
                "a large family of individually-legal queries can isolate individuals"
            )
        return {"spent": spent, "budget": budget}

    def check_differencing(self, subject, proposed):
        """Refuse if any proposed set differs slightly from one already answered."""
        history = [
            (row[0], _unpack(row[1]), row[2])
            for row in self.db.execute(
                "SELECT label, members, size FROM answered WHERE subject=? ORDER BY id DESC LIMIT ?",
                (subject, _setting("WASTELAND_MIMIC_HISTORY", DEFAULT_HISTORY)),
            )
        ]
        # Indices are assigned here too: two groups inside one request must share
        # an index space to be comparable, and a member never seen before is in no
        # prior set, so giving it an index changes no earlier comparison.
        prepared = [(label, self._indices(members), len(members)) for label, members in proposed]
        width = self._width()
        prepared = [(label, _unpack(_pack(indices, width)), size)
                    for label, indices, size in prepared]
        for label, members, size in prepared:
            for prior_label, prior, prior_size in history:
                added, removed = _difference_counts(members, size, prior, prior_size)
                if 0 < added < MIN_CELL or 0 < removed < MIN_CELL:
                    raise BudgetError(
                        "refused: this request differs from one already answered for "
                        f"this subject by fewer than {MIN_CELL} records "
                        f"({added} added, {removed} removed), so subtracting the two "
                        "answers would describe individuals; widen the cohort or "
                        "change more than one condition"
                    )
            # Compare within this request too, not only against history.
            history.append((label, members, size))
        return True

    def record(self, subject, proposed):
        recorded = datetime.now(timezone.utc).isoformat()
        rows = []
        for label, members in proposed:
            indices = self._indices(members)
            rows.append((subject, recorded, label, len(members), indices))
        width = self._width()
        self.db.executemany(
            "INSERT INTO answered(subject, recorded, label, size, members) VALUES(?,?,?,?,?)",
            [(subject, recorded, label, size, _pack(indices, width))
             for subject, recorded, label, size, indices in rows],
        )
        self.db.commit()
        self._prune(subject)

    def _prune(self, subject):
        keep = _setting("WASTELAND_MIMIC_HISTORY", DEFAULT_HISTORY)
        self.db.execute(
            "DELETE FROM answered WHERE subject=? AND id NOT IN "
            "(SELECT id FROM answered WHERE subject=? ORDER BY id DESC LIMIT ?)",
            (subject, subject, keep),
        )
        self.db.commit()

    def close(self):
        self.db.close()


def membership_sets(result, membership):
    """What to remember: the cohort, and every group actually reported."""
    proposed = [("cohort", membership["cohort"])]
    for group in result["groups"]:
        if "suppressed" in group:
            continue  # nothing was revealed about it
        key = ",".join(f"{k}={v}" for k, v in sorted(group["key"].items())) or "all"
        proposed.append((f"group:{key}", membership["groups"][key]))
    return proposed
