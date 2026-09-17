# Zerzura data access policy

This is the operating policy of one town. It explains what Zerzura does with the
data it holds, what it returns, what it refuses, and the reasoning behind each
line — so that a requester, a data access committee, or another town building
something similar can judge it rather than take it on trust.

It is not legal advice, and it does not bind anyone but this town. Where it
interprets a data use agreement, it says so, and the interpretation is the
operator's own.

## 1. What leaves this machine

Aggregates, and nothing else. A reply contains:

- values of **allowlisted dimensions** (`gender`, `age_group`, `admission_type`,
  `insurance`, `race`, `care_unit`) — never a free-text or identifying field
- a **group size** and a **summary statistic** (`count`, `mean`, `median`,
  `mortality_rate`)
- the **question as it was understood**, and metadata describing which privacy
  controls were applied

No `subject_id`, `hadm_id` or `stay_id`. No dates, no notes, no free text. Record
identifiers exist only in a local ledger used for disclosure control, are never
placed in a reply, and never leave the machine. A test asserts their absence.

## 2. Two classes of request, and the axis that separates them

The distinction that governs this policy is **not** what comes back. It is
**who directs the computation over patient-level records.**

### Class A — bounded aggregate queries (implemented)

The *town* defines the computation. A requester selects parameters from a fixed
allowlist and can express nothing the town has not already sanctioned. Nobody
runs anything over individual records except this town, on its own lawful
access. `mimic-aggregate` is the only operation in this class.

### Class B — requester-directed workflows (not implemented)

The *requester* defines the computation and the town executes it. Even if only
derived results return, and even if no data moves, the requester is materially
exercising the access: "I can run arbitrary analysis over these records, I just
cannot take them home" describes having access, not receiving a service.

**Zerzura implements no Class B operation.** Section 6 states what would be
required before it did, and [a design note](class-b-design-note.md) assesses one
concrete approach — including a distinction it exposes between a requester who
directs the analysis and one who merely supplies an artifact the node tests.

## 3. Reading the data use agreement

For data obtained under a data use agreement — the PhysioNet Credentialed Health
Data Use Agreement in particular — this town's reading is:

**A service over data is not sharing access to it.** The clause "I will not share
access to PhysioNet restricted data with anyone else" targets giving someone the
ability to obtain the data: credentials, files, a database connection, an export.
Zerzura gives none of these. The data never leaves the machine; the question
travels to the data instead. This is the premise of federated analysis generally.

**Derived, non-identifying results are the intended output.** Every paper written
on such data puts aggregates derived from patient records in front of an entirely
uncredentialed readership. The PhysioNet agreement itself requires contributing
analysis code to a repository open to the research community, so it plainly
contemplates disseminating derived results. The agreement protects individuals,
not aggregate knowledge.

**The caveat that matters is adaptivity, not audience.** A paper publishes a small
number of aggregates, chosen by an author, static and reviewed. A query service
offers an unbounded family of aggregates, chosen by the requester, adaptively and
in sequence. A thousand adaptive queries is a categorically different object from
ten published tables even when every individual output looks identical. Section 4
is what closes that gap, and this town's reading of the agreement depends on it
holding.

**Class B is different in kind, not degree.** Where the requester directs
computation over row-level records, they are exercising the access rather than
receiving its fruits, and they should hold the entitlement themselves.

## 4. What earns the reading in section 3

These are engineering properties. They can be lost by changing configuration,
which is why they are enumerated rather than assumed.

| Control | What it prevents |
|---|---|
| Allowlisted queries | Requesters cannot express arbitrary computation; no SQL reaches the database, only bound parameter values |
| Minimum cell size | A group too small to be non-identifying is withheld, not rounded or noised |
| Complementary suppression | A single suppressed group cannot be recovered by subtracting the rest from the total |
| Composition ledger | A request differing from an earlier answer by fewer records than the minimum cell size is refused, so two legal queries cannot be subtracted to isolate people |
| Request budget | A large enough family of individually legal queries is itself an attack |
| Signed undertaking | The requester commits, in a non-repudiable signature, not to compose queries to isolate anyone — the residue that no technical control closes |
| Audit log | Every request, refused or answered, is recorded locally with the requester, the credential subject and the outcome |

**Disclosure control is a property of the dataset, not of this town.** A dataset
that declares itself openly licensed receives none, because suppressing a cell
of three when anyone can download the rows protects nothing, and a control that
protects nothing while appearing to is worse than none. Everything that does not
declare itself public — including any database with no metadata table, which is
what a credentialed database looks like — receives the full set. The default is
strict and the failure direction is deliberate.

## 5. Who may ask

| Requirement | Class A | Class B |
|---|---|---|
| Credential verified against a published issuer key | Yes | Yes |
| Proof the requester holds the key the credential names | Yes | Yes |
| A current, issuer-signed revocation status | Yes | Yes |
| Signed undertaking to this town | Yes | Yes |
| Evidence of the requester's own entitlement to the underlying data | Not required | **Required** |

Class A does not require the requester to hold the data's own entitlement,
because the reasoning in section 3 does not depend on who is asking — a bounded
aggregate is the same object whoever receives it. Class B does require it,
because the requester is directing computation over individual records.

A credential attesting a qualification is an **input** to this town's access
decision, never authority over it. An issuer saying someone is credentialed
elsewhere does not oblige this town to answer, and this town remains free to
require a different issuer, or none.

## 6. What this town will not do

- **Execute peer-supplied code.** No Python, shell command, module name or query
  language from a requester is evaluated. A Class B capability, if ever built,
  would have to be a constrained specification the town interprets, or a vetted
  pipeline selected by name and run under isolation — never "send us code."
- **Return row-level data**, under any operation, to any requester.
- **Attempt or assist re-identification**, or answer a request whose evident
  purpose is to isolate an individual.
- **Serve a dataset whose credential status cannot be established.** Revoked,
  suspended, unknown, stale, unsigned or unreachable all mean no data.
- **Act as a proxy for an entitlement the requester lacks** in Class B.

## 7. What this policy does not establish

Stated plainly, because a policy that only lists its strengths is marketing.

- **The relay operator can read replies.** Aggregates pass through a third party
  who is neither this town nor the requester. If they are genuinely
  non-identifying this is acceptable, but it is a disclosure, and it should be a
  knowing one.
- **This is not differential privacy.** No formal guarantee is offered. Cell
  suppression and the composition ledger close specific, named attacks.
- **Two colluding requesters each stay within their own ledger.** The composition
  control is per credential subject. This residue is addressed only by the signed
  undertaking, which is a promise rather than a mechanism.
- **Retained history is finite.** An attacker patient enough to fall off the end
  of it is not constrained by the ledger.
- **Issuer keys arrive over the same relay that carries the messages.** Trusting
  them means trusting the relay unless they are pinned out of band.
- **An institution may read its agreements differently.** The reasoning in
  section 3 is this operator's, and the operator bears the consequence of it
  being wrong. Before serving data obtained under a data use agreement, get the
  reading confirmed by whoever is accountable for that agreement.

## 8. Adapting this for your own town

The reasoning transfers; the specifics should not be copied unexamined. In
particular: decide your own minimum cell size and budget from your own data and
obligations rather than inheriting these; re-read section 3 against *your*
agreement, since not every data use agreement contemplates dissemination the way
PhysioNet's does; and keep section 7 honest for your deployment rather than
copying ours.

The implementation is in [`examples/`](../examples), the controls are
demonstrated in [the evidence document](zerzura-evidence.md), and the mechanics
are documented in [the service documentation](mimic-service.md).
