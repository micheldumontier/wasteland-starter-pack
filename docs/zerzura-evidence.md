# Zerzura: evidence for a gated, aggregate-only MIMIC service

*2026-09-16T14:58:27Z by Showboat 0.6.1*
<!-- showboat-id: 5719d593-e603-4732-87a0-f0549215fb89 -->

Zerzura is a town on the Academic Wasteland relay. It answers statistical
questions about ICU patients for requesters who can prove they are entitled to
ask, and never releases a record.

Every block below runs offline from a clean checkout, with no relay, no
credential and no network access, against the real MIMIC-IV Clinical Database
Demo bundled in `examples/data/mimic-iv-demo`. So this document can be checked
rather than believed:

    uvx showboat verify docs/zerzura-evidence.md

```bash
python3 tests/run_checks.py
```

```output
PASS: 90 relay, isolation, recovery and independent-worker checks
```

What the town serves, and what a stranger can learn without any credential. The data is 100 real de-identified patients from Beth Israel Deaconess, openly licensed under the ODbL. `describe` and `mimic-schema` are both open.

```python
import os, tempfile
from pathlib import Path
from examples import mimic_handler, mimic_load, mimic_service as service

root = Path(tempfile.mkdtemp())
counts = mimic_load.build(mimic_load.BUNDLED, root / "mimic.sqlite")
os.environ["WASTELAND_MIMIC_DB"] = str(root / "mimic.sqlite")
print("loaded from the bundled demo:", ", ".join(f"{v} {k}" for k, v in counts.items()))

config = {"name": "zerzura", "display": "Zerzura", "capabilities": ["describe"]}
described = mimic_handler.handle(
    {"from": "stranger", "id": "urn:uuid:x", "body": {"operation": "describe"}}, config)
print()
print(described["description"])
print()
for field in ("name", "version", "synthetic", "licence"):
    print(f"  {field:<12}: {service.dataset()[field]}")
print(f"  {'citation':<12}: {service.dataset()['citation'][:72]}...")
print()
result = service.run({"aggregate": "mortality_rate", "group_by": ["admission_type"]},
                     root / "mimic.sqlite")
print(f"mortality rate by admission type (cohort {result['cohort_size']} ICU stays):")
for group in result["groups"]:
    print(f"  {group['key']['admission_type']:<30} n={str(group['n']):<4} {group['value']}")

```

```output
loaded from the bundled demo: 100 patients, 275 admissions, 140 icustays

Zerzura answers statistical questions about ICU patients for requesters who can prove they are entitled to ask, and never releases a record. Cohorts are named from an allowlist, results are suppressed below a minimum cell size, and a request is refused when subtracting it from an earlier answer would describe individuals.

  name        : mimic-iv-clinical-database-demo
  version     : 2.2
  synthetic   : False
  licence     : Open Data Commons Open Database License v1.0
  citation    : Johnson, A., Bulgarelli, L., Pollard, T., Horng, S., Celi, L. A., & Mark...

mortality rate by admission type (cohort 140 ICU stays):
  EW EMER.                       n=67   0.134
  URGENT                         n=31   0.226
  OBSERVATION ADMIT              n=17   0.176
  SURGICAL SAME DAY ADMISSION    n=15   0.0
  DIRECT EMER.                   n=5    0.2
  ELECTIVE                       n=5    0.0
```

Camelot publishes Ed25519 issuer keys through its `issuers` operation. Its signing construction is documented nowhere, so it was established empirically: seven candidate formulations were tried against three genuinely signed Camelot credentials, and exactly one matched all three. A real credential, fetched from the live registrar on 2026-09-15, is pinned in the test suite and verified here. Altering any claim inside it breaks the signature.

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
altered credentialSubject : refused -- signature does not verify against the issuer's published key
altered validFrom         : refused -- signature does not verify against the issuer's published key
```

A verified signature shows the credential is genuine, not who is presenting it. Camelot binds the holder's key inside the signed document as `credentialSubject.publicKey`, completing the chain: the issuer's key signs the credential, the credential names the subject's key, and that key signs a single-use challenge covering this exact query.

```python
import base64, tempfile
from datetime import datetime, timezone
from pathlib import Path
from examples import ed25519, mimic_presentation as presentation
from wasteland.protocol import canonical

b64 = lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip("=")
issuer_key, holder_key, thief_key = bytes(range(32)), bytes(range(32, 64)), bytes(range(64, 96))
ISSUER = "https://w3id.org/academic-wasteland/camelot/issuers/demo-council"
credential = {"@context": ["https://www.w3.org/ns/credentials/v2"],
              "id": "urn:credential:demo", "type": ["VerifiableCredential"], "issuer": ISSUER,
              "credentialSubject": {"id": "https://physionet.org/users/example",
                                    "publicKey": "ed25519:" + b64(ed25519.public_key(holder_key))}}
document = dict(credential)
credential["proof"] = {"type": presentation.PROOF_TYPE, "verificationMethod": ISSUER + "#key-1",
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

How much protection a result needs depends on what it is a result about. The demo this town serves is openly licensed: anyone can download all 100 patients' rows without an account, so suppressing a cell of three from it protects nothing, and a control that protects nothing while appearing to is worse than none. Disclosure control is therefore a property of the dataset. Below, the same query runs against the real demo and against a synthetic fixture that makes no such declaration.

```python
import sqlite3, tempfile
from pathlib import Path
from examples import mimic_fixture, mimic_load, mimic_service as service

root = Path(tempfile.mkdtemp())
mimic_load.build(mimic_load.BUNDLED, root / "demo.sqlite")
mimic_fixture.build(root / "fixture.sqlite", patients=300)

query = {"aggregate": "count", "group_by": ["care_unit"]}
for name, label in (("demo.sqlite", "real demo (declares public)"),
                    ("fixture.sqlite", "fixture (declares nothing)")):
    control = service.policy(root / name)
    result = service.run(query, root / name)
    shown = sum(1 for g in result["groups"] if "suppressed" not in g)
    print(f"{label:<30} control={control['disclosure_control']:<17} "
          f"reported={shown:<3} suppressed={len(result['groups']) - shown}")
print()
print("smallest group in the real demo:")
result = service.run(query, root / "demo.sqlite")
smallest = min(result["groups"], key=lambda g: g["n"])
print(f"  {smallest['key']['care_unit']:<38} n={smallest['n']}  reported, not suppressed")
print()
print("the default, for anything that does not declare itself public:")
bare = sqlite3.connect(root / "bare.sqlite")
bare.executescript(mimic_fixture.SCHEMA)
bare.execute("DROP TABLE dataset_meta")
bare.commit(); bare.close()
print(f"  {'no dataset_meta table':<24} -> minimum cell size "
      f"{service.policy(root / 'bare.sqlite')['minimum_cell_size']}")
for value in ("TRUE", "1", "yes"):
    path = root / "confused.sqlite"
    mimic_fixture.build(path, patients=50)
    db = sqlite3.connect(path)
    db.execute("INSERT OR REPLACE INTO dataset_meta VALUES('public',?)", (value,))
    db.commit(); db.close()
    print(f"  public={value!r:<18} -> minimum cell size "
          f"{service.policy(path)['minimum_cell_size']}")

```

```output
real demo (declares public)    control=none              reported=9   suppressed=0
fixture (declares nothing)     control=cell-suppression  reported=5   suppressed=0

smallest group in the real demo:
  Neuro Intermediate                     n=1  reported, not suppressed

the default, for anything that does not declare itself public:
  no dataset_meta table    -> minimum cell size 10
  public='TRUE'             -> minimum cell size 10
  public='1'                -> minimum cell size 10
  public='yes'              -> minimum cell size 10
```

For a dataset that is not public, cell suppression alone is not enough, because two individually legal requests can be subtracted from each other. This runs against the synthetic fixture, which declares nothing and therefore gets full disclosure control.

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
print(f"category the attacker wants: {target}, holding {counts[target]} stays "
      f"(minimum cell size {service.MIN_CELL})")
print()
first, members = service.run({"cohort": cohort, "aggregate": "count"},
                             root / "m.sqlite", with_membership=True)
ledger.check_differencing("attacker", mimic_budget.membership_sets(first, members))
ledger.record("attacker", mimic_budget.membership_sets(first, members))
print(f"query 1  women, elective                     -> {first['cohort_size']}  released")
second, members = service.run({"cohort": cohort + [{"field": "race", "op": "in", "value": keep}],
                               "aggregate": "count"}, root / "m.sqlite", with_membership=True)
print(f"query 2  women, elective, all races but one  -> {second['cohort_size']}  computed locally")
print(f"         {first['cohort_size']} - {second['cohort_size']} = "
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
category the attacker wants: OTHER, holding 3 stays (minimum cell size 10)

query 1  women, elective                     -> 197  released
query 2  women, elective, all races but one  -> 194  computed locally
         197 - 194 = 3, the suppressed category exactly

query 2 refused -- refused: this request differs from one already answered for this subject by fewer than 10 records (0 added, 3 removed), so subtracting the two answers would describe individuals; widen the cohort or change more than one condition
```

## What this does not establish

- **anything about credentialed MIMIC-IV.** The data here is the open demo:
  100 patients, no account needed. A credentialed database has far more rows,
  declares no `public` flag, and would therefore receive full disclosure
  control — but none of that is exercised above.
- **that the requester is PhysioNet credentialed.** No third-party API exists
  to check that. What is established is that an issuer Camelot lists asserted
  something about a subject, and that the requester holds the key it named.
- **that issuer keys are trustworthy.** They are fetched from Camelot over the
  same relay that carries the messages, so trusting them means trusting the
  relay. Pin them out of band if that matters.
- **that a revoked credential stops working.** Camelot's credentials carry a
  `credentialStatus` block, but nothing publishes the status it points at, so
  every gate passes until a credential expires on its own.
- **that the composition control is a formal privacy guarantee.** It is not
  differential privacy. It closes differencing exactly and constrains volume,
  but two subjects who collude each stay within their own ledger, and retained
  history is capped, so a patient attacker falls off the end of it.

Protocol delivery and scientific validation remain separate, as the starter
pack puts it. This document is evidence about the first.
