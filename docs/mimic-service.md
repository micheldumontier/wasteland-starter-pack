# Zerzura: gated, aggregate-only MIMIC service

Zerzura answers structured queries over a MIMIC-IV-shaped database and returns
**aggregates only**. No row, identifier or free-text field is ever placed in a
reply. Two operations are advertised:

| Operation | Credential | Returns |
|---|---|---|
| `mimic-schema` | not required | the query contract and a dataset descriptor |
| `mimic-agreement` | not required | this town's undertaking, with its digest |
| `mimic-challenge` | not required | a single-use challenge to bind a presentation |
| `dua-assent` | required | records a signed undertaking |
| `mimic-aggregate` | required, *held*, and with an undertaking on file | suppressed group counts or summary statistics |

## The credential gate

A requester must present a verifiable credential in `credential`. Zerzura checks
that it is well formed, that its issuer is one this town accepts, that it states
no expiry in the past, and **that its signature verifies against the public key
Camelot publishes for that issuer**. A credential that does not verify is
refused and no query is run.

The construction, confirmed against genuinely signed Camelot credentials:

```
Ed25519_verify(proofValue, canonical(credential without its proof block), issuer_key)
```

where `canonical` is the repo's JCS-style sorted-key compact JSON
(`wasteland.protocol.canonical`) and the proof type is
`PangenomeTownEd25519Jcs2026`. Verification is pure Python
(`examples/ed25519.py`, checked against the RFC 8032 test vectors); there is no
dependency to install. Keys come from Camelot's `issuers` operation, cached
locally so message handling never blocks on the network:

```bash
python3 -m examples.camelot_trust --state .town --refresh
```

That command also reports, by checking each signature, which issuers Camelot's
own council accredits. Zerzura additionally requires that a credential's
`verificationMethod` belong to the issuer the credential names, so a credential
signed by one issuer cannot be attributed to another.

Set `WASTELAND_MIMIC_REQUIRED_ROLES` to a comma-separated list to require that
`credentialSubject.roles` assert one of them — for example
`CredentialedPhysioNetUser`, once Camelot issues such credentials. Unset, any
verified credential is accepted.

### Proving you hold it

Verifying the signature shows the credential is genuine. It does not show who is
presenting it, so Zerzura also requires proof that the requester controls the key
the credential names. Camelot binds that key inside the signed document, as
`credentialSubject.publicKey`, which completes the chain:

```
Camelot's issuer key  signs →  the credential  names →  the subject's key  signs → this request
```

The exchange is two steps. Ask for a challenge, then sign a binding over it:

```json
{
  "challenge": "<from mimic-challenge>",
  "created": "<ISO-8601>",
  "credential": "<credential id>",
  "query_digest": "<sha256 of the canonical query>",
  "subject": "<credentialSubject.id>",
  "town": "zerzura"
}
```

Sign `canonical(...)` of that object with the subject's private key and send it as
`presentation.proofValue`. `examples/present.py` does all of this for you.

Because the binding covers `query_digest`, a captured presentation cannot be
re-aimed at a different question. Because the challenge is single-use and issued
to one relay identity, it cannot be replayed or used by another town. A failed
attempt does not spend the challenge, so a bad actor cannot burn a valid
requester's.

### The undertaking: we do not collect your other agreements

A data use agreement you signed with a third party binds you to *them*. It is
not permission for this town to release anything, and a signed copy of one is
personal data with no purpose here — the agreement text is public anyway, and
the only informative part is the part we should not be holding. Sending one
through the relay would also put it in front of the relay operator, which this
project's own rule forbids.

So we do not ask for one. Instead you give a short undertaking directly to this
town, which incorporates those terms by reference:

```bash
python3 -m examples.present --state .town --to zerzura \
    --credential my-credential.json --key .town/holder.key --assent
```

That prints the agreement and stops. Read it, then repeat with `--i-agree` to
sign its digest with the key the credential binds to you. What is recorded is a
digest, a version, your subject identifier, a signature and a timestamp — no
document belonging to anyone else.

The undertaking is tied to one agreement digest. Change a byte of the text and
every prior undertaking stops satisfying the gate, which is how a new version is
rolled out: there is no separate expiry to track.

`examples/agreements/zerzura-1.0.txt` is a **draft, not reviewed by counsel**.
Read it and make it yours before serving anything that matters. One clause is
load-bearing and worth keeping in some form: paragraph 4, where the requester
acknowledges that composing multiple queries to isolate an individual is a
breach whether or not any single query is refused. Cell suppression cannot stop
that technically, so it is closed here instead.

### Limits — read before relying on this

**Trust in the keys is trust in the relay.** Zerzura fetches Camelot's public
keys over the same relay that carries the messages. The relay operator could
substitute them. Pin the keys out of band if that matters.

**PhysioNet is not consulted.** No API exists for a third party to confirm
someone's credentialed PhysioNet status. What is established is that an issuer
Camelot lists asserted something about a subject, and that the requester holds
the key named in that assertion. Whether the issuer checked PhysioNet before
signing is Camelot's operational question, not a cryptographic one.

**A stolen private key is a stolen identity.** Holder binding proves control of
a key, nothing more. Keep `.town/holder.key` at mode 0600 and treat it as you
would an SSH key.

**An undertaking is a promise, not an enforcement mechanism.** It gives you a
signed, non-repudiable record of what someone agreed to, and a basis to act if
they break it. It does not prevent them breaking it.

Every request — answered, refused or unbound — is appended to a local audit
database (`.town/mimic-audit.sqlite`, mode 0600) with the relay-authenticated
sender, the credential subject, whether it verified, the query and the outcome.
That file never leaves the machine.

## Requesters never supply SQL

A query names fields from an allowlist; only *values* reach the database, as
bound parameters. `subject_id`, `hadm_id` and anything else not listed below are
rejected before the database is opened.

```json
{
  "operation": "mimic-aggregate",
  "credential": { "...": "a Camelot-issued verifiable credential" },
  "query": {
    "cohort": [{"field": "admission_type", "op": "eq", "value": "EW EMER."}],
    "aggregate": "mean",
    "measure": "los_days",
    "group_by": ["age_group"]
  }
}
```

- **dimensions** (filter and `group_by`): `gender`, `age_group`, `admission_type`,
  `insurance`, `race`, `care_unit`
- **measures**: `los_days`, `age_years`
- **aggregates**: `count`, `mean`, `median`, `mortality_rate`
- **operators**: `eq`, `in`
- Unit of analysis is one ICU stay. Limits: 8 filters, 20 values per filter,
  2 `group_by` fields, 50 groups returned.

Ask `mimic-schema` for the live contract rather than hard-coding this list.

## What keeps the results non-identifying

Any group of fewer than **10** stays is suppressed — `n` and `value` become
`null` with a `suppressed` reason — rather than rounded or noised. Because a
single suppressed group can be recovered by subtracting the others from the
reported total, a second group is suppressed alongside it. If the whole cohort
falls below the threshold, even `cohort_size` is withheld. A query producing
more than 50 groups is refused rather than truncated, since a truncated table
plus a total also leaks.

This is cell suppression, not differential privacy: `privacy.differential_privacy`
is `false` in every reply. It offers no formal guarantee against an adversary who
combines many overlapping queries. The audit log exists so that such a pattern
can at least be detected after the fact.

## Running it

```bash
python3 -m examples.mimic_fixture --out .town/mimic.sqlite      # synthetic, for development
python3 -m examples.camelot_trust --state .town --refresh       # required: issuer keys
WASTELAND_MIMIC_DB=.town/mimic.sqlite \
  python3 -m wasteland work --handler examples.mimic_handler:handle
```

Without a trust store every credential is refused, so refresh it before serving
and again whenever Camelot rotates a key.

Point `WASTELAND_MIMIC_DB` at a real MIMIC-IV database to serve it. A real
database carries no `dataset_meta` table, so replies will report
`"name": "unlabelled", "synthetic": "unknown"` — add that table to describe what
you are serving. `WASTELAND_MIMIC_TRUSTED_ISSUERS` (comma-separated URL
prefixes) sets the accepted issuers; `WASTELAND_MIMIC_REQUIRED_ROLES` gates on
asserted roles; `WASTELAND_CAMELOT_TRUST` and `WASTELAND_MIMIC_AUDIT` relocate
the trust store and the log.

Before serving credentialed data, confirm your DUA permits returning aggregates
to remote requesters, and raise `mimic_service.MIN_CELL` if it specifies a larger
minimum cell size.

A requester first mints a key and gives its public half to the issuer, so the
credential they are issued binds it:

```bash
python3 -m examples.present --new-key .town/holder.key
```

Then they ask. `present` fetches a challenge, signs the binding and sends the
query in one step:

```bash
python3 -m wasteland send zerzura --operation mimic-schema --wait 60
python3 -m examples.present --state .town --to zerzura \
    --credential my-credential.json --key .town/holder.key \
    --query examples/mimic-aggregate-query.json
```

A plain `send` with a hand-built body still works, but must carry both a
`credential` and a matching `presentation`, and the subject must already have an
undertaking on file; without any of the three, no data is returned.

The four gates, in the order they are applied:

1. **Credential** — signed by an issuer Camelot publishes a key for
2. **Holder binding** — a single-use challenge signed by the subject's key
3. **Undertaking** — a signed assent to this town's current agreement
4. **Query limits** — allowlisted fields, then cell suppression on the result

Tests: `python3 tests/run_checks.py` (see `tests/test_mimic_service.py`).
