<!-- Türkçe: README.tr.md -->

# helpdesk-guide

**An offline troubleshooting assistant for IT support desks.** Type a symptom the
way a user said it — `monitorum calismiyor` — and get walked through a branching
decision tree, one step at a time, until the fault is fixed or handed over with a
summary somebody can actually act on.

Everything runs locally. No cloud, no account, no network call. IT support is
needed most at exactly the moment the network is down.

[![CI](https://github.com/D3NIZ-D/helpdesk-guide/actions/workflows/ci.yml/badge.svg)](https://github.com/D3NIZ-D/helpdesk-guide/actions/workflows/ci.yml)
[![Content](https://github.com/D3NIZ-D/helpdesk-guide/actions/workflows/content-check.yml/badge.svg)](https://github.com/D3NIZ-D/helpdesk-guide/actions/workflows/content-check.yml)
[![Engine: MIT](https://img.shields.io/badge/engine-MIT-blue.svg)](LICENSE)
[![Content: CC BY-SA 4.0](https://img.shields.io/badge/content-CC%20BY--SA%204.0-lightgrey.svg)](content/LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

---

## Quick start

```bash
pipx install helpdesk-guide
helpdesk compile          # build the bundled content into a local database
helpdesk serve            # opens http://127.0.0.1:8756 in your browser
```

Or from a checkout, with nothing but Python:

```bash
git clone https://github.com/D3NIZ-D/helpdesk-guide && cd helpdesk-guide
pip install -e ".[web,dev]"
helpdesk compile && helpdesk serve
```

The core — `compile`, `lint`, `search`, `run`, `report` — needs exactly one
dependency (PyYAML). The web UI is an optional extra.

### From the terminal, no browser

```console
$ helpdesk search "monitorum calismiyor"
"monitorum calismiyor"
  normalised : monitorum calismiyor
  stems      : monitor calis
  intent     : NOT_WORKING

  ●●●●○○   61%  DSP-001  Monitörde görüntü yok
          donanim/goruntu · runbook · ✔ doğrulanmış · via alias_key
  ●●○○○○   37%  PWR-001  Bilgisayar hiç açılmıyor
          donanim/guc · runbook · ✔ doğrulanmış · via fts

$ helpdesk run DSP-001
DSP-001  Monitörde görüntü yok
Seçenek numarasını yaz · b geri · s atla · q çık

[1/~4] Monitörün güç LED'i yanıyor mu?
  Monitörün ön yüzünde veya alt çerçevesindeki güç ışığına bak.
  Kullanıcıdan tarif etmesini iste: rengi ne, sabit mi yanıp sönüyor mu.
  Nasıl anlarsın: ...

  1) Hayır, hiç yanmıyor
  2) Evet, turuncu / yanıp sönüyor
  3) Evet, beyaz veya mavi sabit

>
```

Note what the two scores mean. `monitorum calismiyor` is not an exact alias —
it reaches DSP-001 through the stemmed key, which is a strong but not
certain signal, so the shortlist is shown rather than opening the runbook.
Type `ekran siyah`, which *is* an alias, and it opens directly.

---

## Why this exists

L1 technicians meet the same twenty faults over and over, and yet:

- The knowledge lives in senior people's heads and is never written down.
- What *is* written is scattered across Word, Excel and Confluence, where search
  fails because Turkish is agglutinative — `monitörüm`, `monitörde` and
  `monitörünüzün` are three different strings for one word.
- Every technician improvises a different order, so resolution times are
  inconsistent and steps get skipped.
- Unsolved cases reach L2 with half the context missing.

This tool fixes the fourth problem for free, which is what makes people tolerate
the first three being fixed. The escalation summary is generated from the session
record — the same handover, written by hand, costs five minutes every time.

---

## How it works

```
┌──────────────────────────────────────────────────────────────┐
│  PRESENTATION                                                │
│  Local web UI (127.0.0.1, token-gated)  ·  CLI               │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP/JSON, loopback only
┌───────────────────────────┴──────────────────────────────────┐
│  APPLICATION  (FastAPI)                                      │
│  search · session · content · reports                        │
└───────────────────────────┬──────────────────────────────────┘
┌───────────────────────────┴──────────────────────────────────┐
│  CORE (domain)                                               │
│  ┌───────────────┐ ┌──────────────┐ ┌────────────────────┐   │
│  │ Matcher       │ │ Tree engine  │ │ Session recorder   │   │
│  │ BM25 + alias  │ │ deterministic│ │ + escalation       │   │
│  │ + intent      │ │ walk         │ │   summary          │   │
│  └───────────────┘ └──────────────┘ └────────────────────┘   │
│  ┌───────────────┐ ┌──────────────────────────────────────┐  │
│  │ Turkish       │ │ Content compiler + validator         │  │
│  │ normaliser    │ │ (graph checks, secret scan)          │  │
│  └───────────────┘ └──────────────────────────────────────┘  │
└───────────────────────────┬──────────────────────────────────┘
┌───────────────────────────┴──────────────────────────────────┐
│  DATA:  SQLite (WAL) + FTS5 full-text index + media          │
└───────────────────────────┬──────────────────────────────────┘
                            ▲  helpdesk compile
┌───────────────────────────┴──────────────────────────────────┐
│  CONTENT (git)                                               │
│  content/runbooks/**.yaml · content/lexicon/**.yaml          │
│  content-local/**  ← site-private, never committed           │
└──────────────────────────────────────────────────────────────┘
```

Content is written as YAML, compiled into SQLite, and only ever *read* at
runtime. The YAML stays human-readable and diffable; the runtime pays no parsing
cost and gets a real full-text index.

### Design principles

1. **Offline first, dependency-light.** It has to work when the network does not.
2. **Content is separate from code.** Runbooks are YAML in git, not rows someone
   edited in production.
3. **One node, one action.** "Check the cable and update the driver" is two steps.
4. **Every step has a verification.** "I did it" is not evidence. `verify_text`
   asks what changed.
5. **Deterministic execution.** The same answers always produce the same path.
   An LLM may help draft aliases; it is never in the execution path.
6. **Usage feeds the content.** Where people get stuck is measured, and the
   measurements are a to-do list.

---

## Three content tiers

Not every fault deserves a decision tree. A tree exists to *narrow uncertainty* —
"no display" has six plausible root causes, and the questions eliminate five of
them. `0x0000007B` has one cause and needs no questions at all; forcing it
through twelve nodes only slows the technician down.

| Tier | Shape | Target count | Writing time | Execution |
|---|---|---|---|---|
| **`runbook`** | Branching decision tree | 100–200 | 2–4 h | Step-by-step walk |
| **`guide`** | Linear checklist, 3–8 steps | 800–1,500 | 20–40 min | Tick-off list |
| **`reference`** | Symptom → causes → fix, one card | 3,000–4,000 | 5–15 min | Single card |

All three share one search pool and one schema; only the execution differs.
Reference cards link back to runbooks (`related_runbook`), so 5,000 records end up
as a network woven around 150 trees rather than a pile.

This is the difference between a reachable goal and an unreachable one: 5,000
decision trees is five to ten person-years. 5,000 *records* is about eighteen
months of part-time work, and 80% of real call volume is covered by the first
210 of them.

---

## The confidence ladder

At twenty records, content quality is self-evident. At five thousand, it is the
only thing that matters — a knowledge base where one record in five is wrong is
worse than no knowledge base at all, because a technician follows the wrong step,
makes the fault bigger, and never trusts the tool again.

Every record carries a `verification` level, and the engine acts on it:

| Level | Meaning | Score multiplier |
|---|---|---|
| `verified` / `dogrulanmis` | Applied to a real case and it worked | ×1.00 |
| `reviewed` / `incelendi` | Someone who knows the subject approved it | ×0.90 |
| `draft` / `taslak` | Written, not reviewed | ×0.70 |
| `generated` / `otomatik` | Imported or machine-drafted, unseen by a human | ×0.50 |

Enforced in code, not by convention:

- A `generated` record **cannot** be published — the compiler rejects it.
- A `high` risk step in unverified content is a **build error**. Deleting
  registry keys and changing BIOS settings do not get suggested by content
  nobody has checked.
- A `high` risk step with no `rollback_md` is a **build error**.

---

## Turkish search

The hard part, and the reason a generic knowledge base search does not work here.

```
"MONİTORUM calismiyor!!"
  │
  ├─ Unicode NFC
  ├─ Turkish-aware lowercase       İ→i, I→ı   (str.lower() gets this wrong)
  ├─ Error-code extraction         0x0000007B, ERR-1234, INACCESSIBLE_BOOT_DEVICE
  ├─ Punctuation strip
  ├─ ASCII folding                 ş→s, ğ→g, ı→i, ö→o, ü→u, ç→c
  ├─ Tokenise + stopword removal
  ├─ Suffix stripping              monitorum → monitor,  calismiyor → calis
  ├─ Concept mapping               ekran → monitor  (the concept, not every synonym)
  └─ Intent inference              NOT_WORKING, NO_DISPLAY
                                   ↓
                    FTS5:  "monitor"* OR "ekran"* OR "calis"* ...
```

Two decisions do most of the work:

**The stemmer is consistent, not correct.** Queries and aliases go through the
same pipeline, so both sides collapse onto the same token even when that token is
not a real Turkish root. `dosya` → `dos` is linguistically wrong and completely
harmless; what matters is that `dosyası` lands there too.

**Every FTS term is a prefix query.** Residual suffixes on the indexed side still
match, which means over-stripping is safe and under-stripping is not. This is what
lets a 200-line rule-based stemmer stand in for a full morphological analyser.

Scoring (`src/helpdesk/core/matcher.py`):

```
base  = 0.40·BM25 + 0.25·alias_exact + 0.15·intent + 0.10·context + 0.10·history
score = max(base, alias_floor) × verification_weight
```

`alias_floor` is what makes an exact alias match open the record even on a
small corpus, where BM25's IDF term collapses and every score looks equally
mediocre. Tier is deliberately **not** in the formula: it breaks ties in the
sort order and nothing else, because a bonus big enough to matter is also big
enough to overturn a real relevance difference.

| Score | Behaviour |
|---|---|
| ≥ 0.75 | Open the runbook directly |
| 0.40 – 0.75 | Show the top five with confidence badges |
| 0.20 – 0.40 | Ask an authored clarifying question |
| < 0.20 | No result → logged to `knowledge_gaps` |

That last row is the most valuable report the tool produces: a ranked list of
runbooks that do not exist yet, written by the people who needed them.

### Measuring it

Search quality only improves if it is measured. `tests/golden_queries.yaml` is a
regression suite of real phrasings:

```bash
helpdesk eval
# 141 queries · Recall@1 92.9% · Recall@3 100.0%
```

CI runs it on every content change, so adding a runbook that breaks an older
query is caught in the pull request rather than three weeks later on a call.

---

## Commands

| Command | What it does |
|---|---|
| `helpdesk compile` | Validate content and build the SQLite database |
| `helpdesk lint` | Run every quality gate without writing anything |
| `helpdesk search "<symptom>"` | Match a symptom, show scores and signals |
| `helpdesk run DSP-001` | Walk a runbook interactively |
| `helpdesk run -q "ekran siyah"` | Search, then walk the best match |
| `helpdesk tree <code\|file>` | Render a decision tree as ASCII |
| `helpdesk show DSP-001` | Print one record |
| `helpdesk new --tier guide` | Scaffold a new content file |
| `helpdesk eval` | Score search against the golden query set |
| `helpdesk report` | Knowledge gaps, dead ends, stale content, root causes |
| `helpdesk stats` | Corpus and build statistics |
| `helpdesk prune` | Delete telemetry past the retention window |
| `helpdesk serve` | Local web UI |

---

## Using it in your organisation

Most organisations' runbooks are specific and many are confidential — internal
hostnames, service names, processes that are nobody else's business. So the
content is split in two:

```
content/          public, generic, CC BY-SA      ← upstream, pull updates freely
content-local/    yours, git-ignored, private    ← never committed
```

Both are compiled; a record in `content-local/` with the same `code` overrides
the public one. Clone the repo, write your own runbooks into `content-local/`,
and keep taking upstream updates without a single merge conflict.

`content-local/` is the first line of `.gitignore` and a pre-commit hook refuses
to stage it. Belt and braces, because this is the failure that cannot be undone.

---

## Reports

All telemetry is local and exists to improve the content, never to rank the
staff — there is deliberately no "sessions per technician" report.

| Report | Question | Action |
|---|---|---|
| `knowledge_gaps` | Which searches found nothing? | Write those runbooks |
| `dead_ends` | Where do sessions get abandoned? | Rewrite that branch |
| `escalation_hotspots` | Which runbooks escalate most? | Deepen those branches |
| `skipped_steps` | Which steps does everyone skip? | Delete them |
| `slow_steps` | Which steps run long? | Clarify the wording |
| `stale_content` | What is past its review date? | Schedule a review |
| `root_cause_distribution` | What actually breaks? | Fix the cause |

The last one pays for the project twice over. "23 cases of `VIDEO_KABLO_ARIZA` in
90 days" is not a troubleshooting fact — it is a purchasing decision.

---

## Privacy and security

- **Loopback only.** The server binds `127.0.0.1`; `--host` prints a loud warning.
  A token in the launch URL stops any local page from driving the API.
- **No credentials in content, ever.** The compiler scans for passwords, API keys,
  private keys, JWTs, connection strings and licence keys, and *stops the build*.
  Runbooks reference a vault entry by name instead.
- **No personal data.** End-user names and e-mails are never stored. Free-text
  queries are masked before they are written (`mask_pii`), so a knowledge-gap
  report never contains somebody's name.
- **Content is data, never code.** `yaml.safe_load` only; edge conditions are a
  hand-parsed six-token language, not `eval`; Markdown renders with HTML disabled
  under a strict CSP.
- **Configurable retention**, default 365 days, enforced by `helpdesk prune`.

See [SECURITY.md](SECURITY.md) to report a vulnerability.

---

## Contributing

Content contributions matter more than code contributions here — the engine is a
few thousand lines and finite; the content never is.

- **[CONTRIBUTING-CONTENT.md](CONTRIBUTING-CONTENT.md)** — writing runbooks. Start
  here even if you do not write code. Copy a template, fill it in, open a PR; CI
  validates the schema, the graph, secrets and search regressions, then posts the
  decision tree as an ASCII diagram in the PR so a reviewer can check the logic
  without reading YAML.
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — code contributions.
- **[docs/DESIGN.tr.md](docs/DESIGN.tr.md)** — the full technical design (Turkish),
  including the road to 5,000 records.
- **[docs/DECISIONS.md](docs/DECISIONS.md)** — where the implementation departs from
  that design, and why.

Good first contributions: add aliases to an existing runbook (the cheapest way to
improve search), write a reference card for an error code you know well, or file
a `runbook_request` issue for a fault you hit that had no record.

---

## Licence

| Part | Licence |
|---|---|
| Engine (`src/`) | [MIT](LICENSE) |
| Content (`content/`) | [CC BY-SA 4.0](content/LICENSE) |

Two licences on purpose. The engine should be frictionless to adopt. Operational
knowledge gets better by circulating, so improvements to it should stay available
to everyone who depends on them.
