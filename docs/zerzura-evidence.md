# Zerzura: evidence for a gated, aggregate-only MIMIC service

*2026-09-16T13:15:52Z by Showboat 0.6.1*
<!-- showboat-id: 5d78df57-c3d4-4b93-8d55-1c90e8cd3f31 -->

Zerzura is a town on the Academic Wasteland relay. It answers statistical
questions about ICU patients for requesters who can prove they are entitled to
ask, and never releases a record.

Every code block below runs offline from a clean checkout of this repository,
with no relay, no credential and no network access, so anyone can re-run this
document and check the claims rather than take them on trust:

    uvx showboat verify docs/zerzura-evidence.md

The live town is a separate matter. Blocks that depended on Robert's hosts being
up, or on a credential we hold, would prove nothing to a reader who has neither,
so they are not here.

```bash
python3 tests/run_checks.py
```

```output
PASS: 90 relay, isolation, recovery and independent-worker checks
```

Camelot publishes Ed25519 issuer keys through its `issuers` operation. Its signing construction is not documented anywhere, so it was established empirically: seven candidate formulations were tried against three genuinely signed Camelot credentials, and exactly one matched all three. A real credential, fetched from the live registrar on 2026-09-15, is pinned in the test suite and verified here. Altering any claim inside it breaks the signature.

```python
import json
from examples import camelot_trust
from tests.test_credential_verification import (
    CAMELOT_CREDENTIAL, CAMELOT_ISSUER, CAMELOT_PUBLIC_KEY)

record = {"fetched": "pinned 2026-09-15", "source": "camelot, via the relay",
          "issuers": [{"id": CAMELOT_ISSUER, "publicKey": CAMELOT_PUBLIC_KEY}]}
copy = lambda: json.loads(json.dumps(CAMELOT_CREDENTIAL))

verdict = camelot_trust.verify_credential(copy(), record=record)
print("genuine credential :", "VERIFIED" if verdict["verified"] else "rejected")
print("construction       :", verdict["algorithm"])
print("proof type         :", verdict["proof_type"])

for field, value in (("credentialSubject", {"id": "https://example.invalid/me"}),
                     ("validFrom", "1999-01-01T00:00:00Z")):
    forged = copy()
    forged[field] = value
    try:
        camelot_trust.verify_credential(forged, record=record)
        print(f"altered {field:<18}: ACCEPTED")
    except camelot_trust.TrustError as error:
        print(f"altered {field:<18}: refused -- {error}")

```

```output
genuine credential : VERIFIED
construction       : Ed25519 over JCS-canonical JSON without the proof block
proof type         : PangenomeTownEd25519Jcs2026
altered credentialSubject : refused -- signature does not verify against the issuer's published key
altered validFrom         : refused -- signature does not verify against the issuer's published key
```

A verified signature shows the credential is genuine. It does not show who is presenting it, so a credential copied from a transcript would otherwise work for anyone. Camelot binds the holder's key inside the signed document as `credentialSubject.publicKey`, which completes the chain: the issuer's key signs the credential, the credential names the subject's key, and that key signs a single-use challenge covering this exact query.

```python
import base64, json, tempfile
from datetime import datetime, timezone
from pathlib import Path
from examples import ed25519, mimic_presentation as presentation
from wasteland.protocol import canonical

b64 = lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip("=")
issuer_key, holder_key, thief_key = bytes(range(32)), bytes(range(32, 64)), bytes(range(64, 96))
ISSUER = "https://w3id.org/academic-wasteland/camelot/issuers/demo-council"

credential = {"@context": ["https://www.w3.org/ns/credentials/v2"],
              "id": "urn:credential:demo", "type": ["VerifiableCredential"],
              "issuer": ISSUER,
              "credentialSubject": {"id": "https://physionet.org/users/example",
                                    "publicKey": "ed25519:" + b64(ed25519.public_key(holder_key))}}
document = dict(credential)
credential["proof"] = {"type": presentation.PROOF_TYPE,
                       "verificationMethod": ISSUER + "#key-1",
                       "proofValue": b64(ed25519.sign(issuer_key, canonical(document).encode()))}

query = {"aggregate": "count", "group_by": ["gender"]}
store = presentation.ChallengeStore(Path(tempfile.mkdtemp()) / "c.sqlite")

def present(signing_key, label):
    issued = store.issue("peer_lab")
    created = datetime.now(timezone.utc).isoformat()
    message = presentation.binding(
        challenge=issued["challenge"], created=created, credential_id=credential["id"],
        query=query, subject=credential["credentialSubject"]["id"], town="zerzura")
    proof = {"type": presentation.PROOF_TYPE, "challenge": issued["challenge"],
             "created": created, "proofValue": b64(ed25519.sign(signing_key, message))}
    try:
        verdict = presentation.verify_presentation(
            proof, credential=credential, query=query, requester="peer_lab",
            town="zerzura", store=store)
        print(f"{label:<34}: {verdict['holder_binding']}")
        return proof
    except presentation.PresentationError as error:
        print(f"{label:<34}: refused -- {error}")

accepted = present(holder_key, "holder signs with the named key")
present(thief_key, "someone who copied the credential")

try:
    presentation.verify_presentation(accepted, credential=credential, query=query,
                                     requester="peer_lab", town="zerzura", store=store)
    print(f"{'replay of a valid presentation':<34}: accepted")
except presentation.PresentationError as error:
    print(f"{'replay of a valid presentation':<34}: refused -- {error}")
store.close()

```

```output
holder signs with the named key   : verified
someone who copied the credential : refused -- presentation signature does not verify against the subject key bound in the credential; the presenter does not hold this credential
replay of a valid presentation    : refused -- challenge already used; presentations are single-use
```

Cell suppression works one query at a time, and that is not enough. Two individually legal requests can be subtracted from each other. Below, a seeded synthetic fixture is built, one category is identified that is too small to release, and the attack is attempted: both cohorts are large, both totals would pass suppression on their own, and their difference is exactly the suppressed category.

```python
import os, sqlite3, tempfile
from pathlib import Path
from examples import mimic_budget, mimic_fixture, mimic_service as service

root = Path(tempfile.mkdtemp())
mimic_fixture.build(root / "m.sqlite", patients=1400)
os.environ["WASTELAND_MIMIC_LEDGER"] = str(root / "ledger.sqlite")
ledger = mimic_budget.CohortLedger()

cohort = [{"field": "gender", "op": "eq", "value": "F"},
          {"field": "admission_type", "op": "eq", "value": "ELECTIVE"}]
counts = dict(sqlite3.connect(root / "m.sqlite").execute(
    "SELECT a.race, COUNT(*) FROM icustays s JOIN admissions a ON a.hadm_id=s.hadm_id"
    " JOIN patients p ON p.subject_id=s.subject_id"
    " WHERE p.gender='F' AND a.admission_type='ELECTIVE' GROUP BY a.race").fetchall())
target = min((r for r in counts if 0 < counts[r] < service.MIN_CELL), key=counts.get)
keep = [race for race in counts if race != target]

print(f"minimum cell size            : {service.MIN_CELL}")
print(f"category the attacker wants  : {target}, holding {counts[target]} stays")
print()

first, members = service.run({"cohort": cohort, "aggregate": "count"},
                             root / "m.sqlite", with_membership=True)
ledger.check_differencing("attacker", mimic_budget.membership_sets(first, members))
ledger.record("attacker", mimic_budget.membership_sets(first, members))
print(f"query 1  women, elective                     -> {first['cohort_size']}  released")

second, members = service.run({"cohort": cohort + [{"field": "race", "op": "in", "value": keep}],
                               "aggregate": "count"}, root / "m.sqlite", with_membership=True)
print(f"query 2  women, elective, all races but one  -> {second['cohort_size']}  computed locally")
print(f"         difference {first['cohort_size']} - {second['cohort_size']} = "
      f"{first['cohort_size'] - second['cohort_size']}, the suppressed category exactly")
print()
try:
    ledger.check_differencing("attacker", mimic_budget.membership_sets(second, members))
    print("query 2 released -- the attack succeeded")
except mimic_budget.BudgetError as error:
    print("query 2 refused --", error)
ledger.close()

```

```output
minimum cell size            : 10
category the attacker wants  : OTHER, holding 3 stays

query 1  women, elective                     -> 197  released
query 2  women, elective, all races but one  -> 194  computed locally
         difference 197 - 194 = 3, the suppressed category exactly

query 2 refused -- refused: this request differs from one already answered for this subject by fewer than 10 records (0 added, 3 removed), so subtracting the two answers would describe individuals; widen the cohort or change more than one condition
```

Finally, what a stranger actually gets. `describe` and `mimic-schema` are open: no credential is needed to read what this town is, what it holds, and what it will refuse. The reply states that the backing data is synthetic, and says what none of the gates establish.

```python
import os, tempfile
from pathlib import Path
from examples import mimic_fixture, mimic_handler

root = Path(tempfile.mkdtemp())
mimic_fixture.build(root / "m.sqlite", patients=1400)
os.environ["WASTELAND_MIMIC_DB"] = str(root / "m.sqlite")
config = {"name": "zerzura", "display": "Zerzura",
          "capabilities": ["echo", "describe", "mimic-schema", "mimic-agreement",
                           "mimic-challenge", "dua-assent", "mimic-aggregate"]}
ask = lambda op: mimic_handler.handle(
    {"from": "stranger", "id": "urn:uuid:x", "body": {"operation": op}}, config)

described = ask("describe")
print("DESCRIBE")
print(" ", described["description"])
print()
print("  dataset      :", described["dataset"]["name"],
      "| schema:", described["dataset"]["schema"],
      "| synthetic:", described["dataset"]["synthetic"])
print("  open         :", ", ".join(described["access"]["open_operations"]))
print("  credentialed :", ", ".join(described["access"]["credentialed_operations"]))
print()
print("  not established:", described["not_established"])
print()
schema = ask("mimic-schema")["query_contract"]
print("MIMIC-SCHEMA")
print("  unit         :", schema["unit_of_analysis"])
print("  dimensions   :", ", ".join(schema["dimensions"]))
print("  measures     :", ", ".join(schema["measures"]))
print("  aggregates   :", ", ".join(schema["aggregates"]))
print("  limits       :", schema["limits"])

```

```output
DESCRIBE
  Zerzura answers statistical questions about ICU patients for requesters who can prove they are entitled to ask, and never releases a record. Cohorts are named from an allowlist, results are suppressed below a minimum cell size, and a request is refused when subtracting it from an earlier answer would describe individuals.

  dataset      : synthetic-mimic-iv-shaped | schema: MIMIC-IV | synthetic: True
  open         : echo, describe, mimic-schema, mimic-challenge
  credentialed : dua-assent, mimic-aggregate

  not established: PhysioNet is never consulted; issuer keys arrive over the same relay that carries these messages; and cell suppression is not differential privacy.

MIMIC-SCHEMA
  unit         : icu_stay
  dimensions   : admission_type, age_group, care_unit, gender, insurance, race
  measures     : age_years, los_days
  aggregates   : count, mean, median, mortality_rate
  limits       : {'minimum_cell_size': 10, 'max_filters': 8, 'max_values_per_filter': 20, 'max_group_by': 2, 'max_groups_returned': 50}
```

## What this does not establish

The blocks above are the whole of the evidence. They do not show that:

- **anything about credentialed MIMIC-IV.** The blocks above run against a
  synthetic fixture, so that they work from a clean checkout with no download.
  The live town serves the openly licensed MIMIC-IV Clinical Database Demo
  (100 real de-identified patients). Neither is credentialed data, and every
  reply reports which it is.
- **the requester is PhysioNet credentialed.** No third-party API exists to
  check that. What is established is that an issuer Camelot lists asserted
  something about a subject, and that the requester holds the key it named.
- **issuer keys are trustworthy.** They are fetched from Camelot over the same
  relay that carries the messages, so trusting them means trusting the relay.
  Pin them out of band if that matters.
- **a revoked credential stops working.** Camelot's credentials carry a
  `credentialStatus` block, but nothing publishes the status it points at, so
  every gate passes until the credential expires on its own.
- **the composition control is a formal privacy guarantee.** It is not
  differential privacy. It closes differencing exactly and constrains volume,
  but two subjects who collude each stay within their own ledger, and retained
  history is capped, so a patient attacker falls off the end of it.

Protocol delivery and scientific validation remain separate, as the starter
pack puts it. This document is evidence about the first.

How much protection a result needs depends on what it is a result about. The MIMIC-IV Clinical Database Demo is openly licensed: anyone can download all 100 patients' rows without an account, so suppressing a cell of three from it protects nothing. A control that protects nothing while appearing to is worse than none. Disclosure control is therefore a property of the dataset, and the default is strict — a database earns relaxed treatment only by declaring `public` = `true` exactly, so a credentialed MIMIC-IV database, which carries no metadata table at all, is protected without anyone having to remember to configure it.

```python
import sqlite3, tempfile
from pathlib import Path
from examples import mimic_fixture, mimic_service as service

root = Path(tempfile.mkdtemp())
for name in ("private.sqlite", "public.sqlite"):
    mimic_fixture.build(root / name, patients=300)
db = sqlite3.connect(root / "public.sqlite")
db.execute("INSERT OR REPLACE INTO dataset_meta VALUES('public','true')")
db.commit(); db.close()

query = {"cohort": [{"field": "age_group", "op": "eq", "value": "18-29"}],
         "aggregate": "count", "group_by": ["race", "care_unit"]}

for name in ("private.sqlite", "public.sqlite"):
    control = service.policy(root / name)
    result = service.run(query, root / name)
    shown = sum(1 for g in result["groups"] if "suppressed" not in g)
    hidden = len(result["groups"]) - shown
    print(f"{name:<16} control={control['disclosure_control']:<17} "
          f"groups reported={shown:<3} suppressed={hidden}")

print()
print("the default, for databases that do not declare themselves public:")
bare = sqlite3.connect(root / "bare.sqlite")
bare.executescript(mimic_fixture.SCHEMA)
bare.execute("DROP TABLE dataset_meta")
bare.commit(); bare.close()
for label, value in (("no dataset_meta table", None), ("public='TRUE'", "TRUE"),
                     ("public='1'", "1"), ("public='yes'", "yes")):
    if value is None:
        path = root / "bare.sqlite"
    else:
        path = root / "confused.sqlite"
        mimic_fixture.build(path, patients=50)
        db = sqlite3.connect(path)
        db.execute("INSERT OR REPLACE INTO dataset_meta VALUES('public',?)", (value,))
        db.commit(); db.close()
    print(f"  {label:<24} -> minimum cell size {service.policy(path)['minimum_cell_size']}")

```

```output
private.sqlite   control=cell-suppression  groups reported=0   suppressed=8
public.sqlite    control=none              groups reported=8   suppressed=0

the default, for databases that do not declare themselves public:
  no dataset_meta table    -> minimum cell size 10
  public='TRUE'            -> minimum cell size 10
  public='1'               -> minimum cell size 10
  public='yes'             -> minimum cell size 10
```
