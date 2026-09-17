"""Extract the tables this service reads from a plain-text PostgreSQL dump.

A full MIMIC-IV dump is tens of gigabytes of `COPY ... FROM stdin;` blocks, and
all of it is patient-level. This reads the compressed dump as a stream, keeps
only the three tables and the handful of columns the service is allowed to
expose, and writes them as CSV. Nothing else is decompressed to disk, and no
database is needed.

    python3 -m examples.mimic_pgdump --source ~/mimic-iv/mimic.sql.gz --out ~/mimic-iv

The dump is scanned in blocks rather than line by line. Most of its volume is
tables we do not want -- chartevents alone is hundreds of millions of rows -- so
those are skipped by searching for the end-of-block marker rather than by
iterating their rows.
"""

import argparse
import csv
import gzip
import re
import subprocess
import sys
import time
from pathlib import Path

# table -> the columns this service reads, in the order mimic_load expects.
WANTED = {
    "patients": ("subject_id", "gender", "anchor_age"),
    "admissions": ("hadm_id", "subject_id", "admission_type", "insurance", "race",
                   "hospital_expire_flag"),
    "icustays": ("stay_id", "subject_id", "hadm_id", "first_careunit", "los"),
}
HEADER = re.compile(rb"^COPY\s+(?:([\w]+)\.)?\"?(\w+)\"?\s*\(([^)]*)\)\s+FROM\s+stdin;")
START = b"\nCOPY "
END = b"\n\\.\n"
CHUNK = 1 << 23  # 8 MiB

# pg_dump text format escapes; \N means NULL.
ESCAPES = {b"\\t": b"\t", b"\\n": b"\n", b"\\r": b"\r", b"\\b": b"\b",
           b"\\f": b"\f", b"\\v": b"\v", b"\\\\": b"\\"}


def _unescape(field):
    if b"\\" not in field:
        return field.decode("utf-8", "replace")
    if field == b"\\N":
        return ""
    out, i = bytearray(), 0
    while i < len(field):
        if field[i:i + 1] == b"\\" and field[i:i + 2] in ESCAPES:
            out += ESCAPES[field[i:i + 2]]
            i += 2
        else:
            out += field[i:i + 1]
            i += 1
    return bytes(out).decode("utf-8", "replace")


def _reader(path):
    """Decompress with zcat when available; it is much faster than Python's gzip."""
    try:
        process = subprocess.Popen(["zcat", str(path)], stdout=subprocess.PIPE,
                                   bufsize=CHUNK)
        return process.stdout, process
    except FileNotFoundError:
        return gzip.open(path, "rb"), None


class _Table:
    def __init__(self, name, columns, directory):
        self.name = name
        self.picks = [columns.index(c) for c in WANTED[name]]
        self.path = Path(directory) / f"{name}.csv"
        self.handle = self.path.open("w", newline="", encoding="utf-8")
        self.writer = csv.writer(self.handle)
        self.writer.writerow(WANTED[name])
        self.rows = 0

    def add(self, line):
        fields = line.split(b"\t")
        if len(fields) <= self.picks[-1]:
            return
        self.writer.writerow([_unescape(fields[i]) for i in self.picks])
        self.rows += 1

    def close(self):
        self.handle.close()


def extract(source, directory, progress=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stream, process = _reader(source)
    # Starts with a newline so that a COPY on the very first line still matches.
    buffer, state, table, done = b"\n", "scan", None, {}
    read = 0
    started = last = time.monotonic()
    try:
        while True:
            chunk = stream.read(CHUNK)
            if not chunk:
                break
            read += len(chunk)
            buffer += chunk
            if progress and time.monotonic() - last > 30:
                last = time.monotonic()
                progress(read, time.monotonic() - started, done)
            while True:
                if state == "scan":
                    start = buffer.find(START)
                    if start == -1:
                        buffer = buffer[-len(START):]
                        break
                    stop = buffer.find(b"\n", start + 1)
                    if stop == -1:
                        buffer = buffer[start:]
                        break
                    match = HEADER.match(buffer[start + 1:stop])
                    buffer = buffer[stop + 1:]
                    name = match.group(2).decode() if match else None
                    if name in WANTED and name not in done:
                        columns = [c.strip().strip('"') for c in
                                   match.group(3).decode().split(",")]
                        table = _Table(name, columns, directory)
                        state = "keep"
                    else:
                        state = "skip"
                elif state == "skip":
                    stop = buffer.find(END)
                    if stop == -1:
                        buffer = buffer[-len(END):]
                        break
                    # Keep the newline that ends the marker: it is what the next
                    # COPY header is matched against.
                    buffer = buffer[stop + len(END) - 1:]
                    state = "scan"
                else:  # keep
                    stop = buffer.find(END)
                    body = buffer if stop == -1 else buffer[:stop]
                    cut = body.rfind(b"\n") + 1 if stop == -1 else len(body) + 1
                    for line in body[:cut].split(b"\n") if cut else []:
                        if line:
                            table.add(line)
                    if stop == -1:
                        buffer = buffer[cut:]
                        break
                    table.close()
                    done[table.name] = table.rows
                    buffer, state, table = buffer[stop + len(END) - 1:], "scan", None
                    if len(done) == len(WANTED):
                        return done
    finally:
        if table:
            table.close()
        if process:
            process.stdout.close()
            process.terminate()
        elif hasattr(stream, "close"):
            stream.close()
    return done


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    def progress(read, elapsed, done):
        print(f"  {read/1e9:6.1f} GB decompressed  {elapsed/60:5.1f} min  "
              f"extracted: {done or 'none yet'}", file=sys.stderr, flush=True)

    counts = extract(args.source, args.out, progress)
    print("\nextracted:")
    for name in WANTED:
        print(f"  {name:<12} {counts.get(name, 0):>10,} rows  -> {args.out / (name + '.csv')}")
    missing = [n for n in WANTED if n not in counts]
    if missing:
        raise SystemExit("did not find: " + ", ".join(missing))


if __name__ == "__main__":
    main()
