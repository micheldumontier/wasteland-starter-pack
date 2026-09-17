# Class B: what a requester-directed evaluation would take

[The data access policy](data-access-policy.md) defines Class B — a request where
the *requester* directs computation over patient-level records — and says Zerzura
implements none. This note assesses one concrete approach to building one: the
REALM node architecture, an evaluation substrate for AI medical-device software
over OMOP data, developed separately by this town's operator.

It is a design assessment, not a plan.

**Decision, 2026-09-17: deferred.** This town will not execute user-contributed
code against MIMIC data, and will not adopt the REALM substrate, for the
foreseeable future. The assessment below is kept because the reasoning is
durable and the B1/B2 distinction in it has already been folded back into the
policy — not because the work is queued. Anyone reading this as a backlog item
is reading it wrong.

## Why it is a candidate

REALM's operating principle is the one this town already runs on, applied one
level up: a node runs a submitted model against its own OMOP data and exchanges
only aggregated, small-cell-suppressed results. Raw patient data never leaves the
node. Zerzura does that for bounded aggregate queries; REALM does it for model
evaluation, which is precisely the Class B shape.

## How it satisfies the no-peer-code rule

The starter pack is emphatic that a handler must never execute peer-supplied
Python, shell commands or module names. REALM does not violate that, because it
does not accept code. It accepts an **artifact with a fixed interface**: a
container serving three KServe V2 (Open Inference Protocol) endpoints — readiness,
model metadata, and inference over a numeric matrix.

That distinction carries the whole design. The requester never names a module,
supplies a script, or chooses what runs. The node builds the feature matrix from
its own data, calls inference, and reads per-row scores back. The artifact's only
channel is numbers in, numbers out. "Run this code" and "answer these questions
through an interface I control" are different propositions, and only the second
is compatible with the rule.

## The parts that map onto gates this town already has

| REALM | Zerzura equivalent | Note |
|---|---|---|
| Feature annotation — declared features are mapped by a human to OMOP concepts, and the node builds the matrix | Dimension allowlist | Same shape, richer vocabulary. The artifact cannot request a field the node has not agreed to construct. |
| Conformance gate — metadata matches the declared contract | Query validation | Reject before execution, not during. |
| **Hermetic gate — the image must serve with the network switched off** | No equivalent, and none needed today | This is the control that makes compute-to-data true rather than aspirational. An artifact that needs the network is refused. |
| Determinism and behavioral gates | No equivalent | Zerzura runs no foreign artifact, so it has nothing to characterise. |
| **Vendor attestation — a signed SLSA provenance verified against a key the node trusts** | Credential verification | Same construction, same epistemic stance. |
| Aggregated, small-cell-suppressed results | Cell suppression | See the caveat below. |

The attestation stance is worth dwelling on, because it is identical to the one
this town reached independently. REALM's documentation is explicit that signature
verification is the gate, while the claims inside the attestation are only what
the signer asserts, made tamper-evident. That is exactly what
`credential_check.warning` says about a Camelot credential. Two systems arriving
at the same sentence is mild evidence the sentence is right.

## What it would sharpen in our policy

Our Class B definition assumes the requester chooses the question. REALM shows a
case where they do not.

A manufacturer submitting a device for evaluation supplies the *thing being
tested*. The node chooses the cohort, the metrics, the thresholds and the
interpretation, and produces the assessment. The vendor directs nothing except
what their own artifact computes on a matrix they never see.

That is materially different from "run my analysis for me," and arguably does not
require the vendor to hold the data's entitlement at all — the node is conducting
its own evaluation using a supplied component, much as it might evaluate any
instrument. So Class B should be split:

- **B1 — requester-directed:** the requester chooses the cohort and the analysis.
  They are exercising the access; they should hold the entitlement.
- **B2 — artifact-under-test:** the requester supplies a component; the node
  chooses everything else. The node is exercising its own access. The entitlement
  question falls on the node, not the submitter — but the isolation guarantees
  have to be real, because the artifact is untrusted code running beside the data.

The policy should be amended to say this whenever a Class B capability is built.

## Where the aggregate reading gets harder

Model evaluation reports **subgroup and fairness metrics**, and those are exactly
where small-cell problems live. An AUROC over a subgroup of six is a statement
about six identifiable people, however aggregate it looks. REALM's own summary
says "small-cell-suppressed," so the requirement is recognised; the point for us
is that our `MIN_CELL` reasoning transfers directly and would need applying per
subgroup, not only to the cohort.

The composition concern transfers too, and gets worse: an adversary who can
submit successive models against overlapping cohorts has a far richer channel
than one issuing count queries. A ledger over evaluation requests would be doing
much harder work than ours does.

## What would have to be solved for the Wasteland transport

Three obstacles, none fatal, all real:

1. **Envelopes are capped at 64 KiB.** An image cannot travel over the relay. It
   would have to be a registry reference plus a digest, with the node pulling it
   out of band — or carried through the existing `resources`/`resource`
   operations, which already do digest-verified download.
2. **Evaluations are long-running.** The relay is request/reply with asynchronous
   answers, which fits: the pattern already exists in the ecosystem, where a
   `message` returns `delivered-to-resident` and the real answer follows
   separately. An evaluation would acknowledge on submission and reply on
   completion.
3. **The dependency weight is a genuine tension.** This starter pack runs on the
   standard library and nothing else, deliberately. REALM needs Docker, a
   registry, KServe and Postgres. A Class B town would not be a starter town; it
   would be a different kind of node that speaks the same protocol. That is
   allowed — the protocol documentation says towns may be implemented in any
   language and with any backend — but it should be a conscious split rather than
   a slow accretion of dependencies into this repository.

## Assessment

The architecture is a good fit, and the reason is the interface, not the
container: constraining a submission to numbers-in/numbers-out through endpoints
the node calls is what makes untrusted computation tractable next to controlled
data. The hermetic gate is the specific control that makes compute-to-data a
property rather than a promise, and it is the thing Zerzura would most need to
copy.

The work is not in the protocol. It is in isolation, certification and
disclosure control over subgroup metrics — and in deciding, per the B1/B2 split
above, who is actually exercising the access.
