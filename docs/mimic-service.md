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
the credential names. The issuer binds that key inside the signed document — as
`credentialSubject.holderKey` on a participant credential from the registrar's
approval path, or `publicKey` on an issuer accreditation. Either is accepted; a
credential carrying both with *different* values is refused as ambiguous rather
than resolved by guessing. That completes the chain:

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

### Revocation

A verified signature proves a credential was issued. It says nothing about
whether it still stands, and revocation is normally the control you can rely on
being prompt. Before computing or releasing anything from a dataset that has not
declared itself public, this town asks the registrar over the relay:

```
credential-status  ->  an issuer-signed statement carrying the credential id,
                       status id, issuer, status, as_of and valid_until
```

The statement is verified with the same canonical-JSON Ed25519 construction as
the credential, using the issuer key from **this town's trust store** — never a
key that arrived with the response, since a registrar cannot be allowed to
nominate the key used to check its own answer.

**Every unresolved answer is a refusal.** Revoked, suspended, unknown, stale,
future-dated, expired, unsigned, wrongly signed, about a different credential,
from an untrusted issuer, malformed, or unreachable all mean no data. A
statement older than `WASTELAND_MIMIC_STATUS_MAX_AGE` seconds (default 60) is
stale.

A public dataset is exempt, for the same reason it is exempt from suppression:
withdrawing someone's access to rows anyone can download protects nothing.

The relay `credential-status` operation does not exist yet — it is
[issue #2](https://github.com/academic-wasteland/wasteland-starter-pack/issues/2)
upstream. Until it does, this town cannot serve a non-public dataset at all,
which is the correct failure direction.

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
they break it. It does not prevent them breaking it. Paragraph 4 of the
agreement still matters even though the differencing attack is now blocked
technically, because two colluding subjects each stay within their own ledger.

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

### Disclosure control belongs to the dataset, not the town

How much protection a result needs depends on what it is a result *about*. The
MIMIC-IV Clinical Database Demo is distributed under the Open Data Commons Open
Database License: anyone can download all 100 patients' rows without an account.
Suppressing a cell of three from that data protects nothing, and a control that
protects nothing while appearing to is worse than none, because it misleads
whoever relies on it.

So a database may declare itself public, and one that does gets no suppression
and no composition ledger. The reply says which control applied and why:

```json
"privacy": {
  "disclosure_control": "none",
  "disclosure_control_reason": "this dataset declares itself openly licensed;
     its row-level data is already downloadable by anyone, so suppression would
     protect nothing",
  "minimum_cell_size": null
}
```

**The default is strict, and the failure direction is deliberate.** A database
gets relaxed treatment only if its `dataset_meta` table says `public` = `true`,
spelled exactly. No table, no key, or any other value — including `TRUE`, `1`
or `yes` — is treated as private. A credentialed MIMIC-IV database carries no
`dataset_meta` at all, so it gets full disclosure control without anyone having
to remember to configure it.

Everything below therefore describes a dataset that has *not* declared itself
public.

Any group of fewer than **10** stays is suppressed — `n` and `value` become
`null` with a `suppressed` reason — rather than rounded or noised. Because a
single suppressed group can be recovered by subtracting the others from the
reported total, a second group is suppressed alongside it. If the whole cohort
falls below the threshold, even `cohort_size` is withheld. A query producing
more than 50 groups is refused rather than truncated, since a truncated table
plus a total also leaks.

### Sequences, not just single queries

Cell suppression works one query at a time, and that is not enough. Consider:

```
Q1  women, elective admissions                     -> 197
Q2  women, elective admissions, every race but one -> 194
```

Both cohorts are large. Both answers pass suppression. Their difference is 3 —
exactly the category that was too small to release. Neither query is illegal;
the pair is.

So this town remembers which records answered each request, per credential
subject, and refuses a request whose membership differs from something already
answered by a non-zero amount below the minimum cell size. Groups within a
single request are compared to each other as well. Each subject also has a
rolling request budget, because a large enough family of individually-legal
queries is itself an attack.

The check runs *after* the query executes and *before* anything is returned: a
refused request is computed locally and discarded, and the reply carries no
counts at all.

Membership is stored as record identifiers in a local ledger
(`.town/mimic-ledger.sqlite`, mode 0600). That is what makes the check exact
rather than heuristic. Those identifiers are never placed in a reply — a test
asserts it — and the ledger never leaves the machine.

Tunable with `WASTELAND_MIMIC_BUDGET` (default 50), `WASTELAND_MIMIC_BUDGET_WINDOW`
(hours, default 24) and `WASTELAND_MIMIC_HISTORY` (membership sets retained per
subject, default 400). A shorter history is cheaper and weaker: an attacker who
waits long enough falls off the end of it.

This is still not differential privacy — `privacy.differential_privacy` is
`false` in every reply. It closes the differencing attack exactly, and it
constrains volume, but it offers no formal guarantee against an adversary who
is patient, or who has outside knowledge the ledger cannot see.

## Running it

Serve the open MIMIC-IV demo — 100 real de-identified patients, no credentialed
account or data use agreement needed to download it:

```bash
python3 -m examples.mimic_load --out .town/mimic.sqlite
```

The three tables it reads are bundled in `examples/data/mimic-iv-demo` under the
Open Data Commons Open Database License; see the `LICENCE.txt` beside them. That
licence covers the data directory only — the rest of the repository is
Apache-2.0. Pass `--source` to load a copy you downloaded yourself from
<https://physionet.org/content/mimic-iv-demo/2.2/>.

Or generate a synthetic fixture, if you would rather not download anything:

```bash
python3 -m examples.mimic_fixture --out .town/mimic.sqlite      # synthetic, for development
```

Then:

```bash
python3 -m examples.camelot_trust --state .town --refresh       # required: issuer keys
WASTELAND_MIMIC_DB=.town/mimic.sqlite \
  python3 -m wasteland work --handler examples.mimic_handler:handle
```

Without a trust store every credential is refused, so refresh it before serving
and again whenever Camelot rotates a key.

Point `WASTELAND_MIMIC_DB` at a credentialed MIMIC-IV database to serve it. Such
a database carries no `dataset_meta` table, so replies report
`"name": "unlabelled", "synthetic": "unknown"` and it receives full disclosure
control. Add that table to describe what you are serving — but do **not** set
`public` on it. `WASTELAND_MIMIC_TRUSTED_ISSUERS` (comma-separated URL
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
2. **Revocation** — a fresh, issuer-signed `active` status (non-public datasets)
3. **Holder binding** — a single-use challenge signed by the subject's key
4. **Undertaking** — a signed assent to this town's current agreement
5. **Query limits** — allowlisted fields, then cell suppression on the result
6. **Composition** — the request is checked against what this subject has
   already been told, and refused if the difference would isolate anyone

Tests: `python3 tests/run_checks.py` (see `tests/test_mimic_service.py`).
