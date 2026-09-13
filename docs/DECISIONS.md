# Implementation decisions

The original technical design is archived at [DESIGN.tr.md](DESIGN.tr.md)
(Turkish). This document records where the implementation departs from it,
and why. Each departure was a judgement call; if one turns out to be wrong,
this is the place to argue with it.

---

## 1. No pydantic — plain dataclasses instead

**Design (§13.1):** `pydantic` for schema validation of runbook YAML.

**Built:** `dataclasses` plus explicit validation in `content/validator.py`.

**Why:** the expensive validation here is graph-shaped, not field-shaped.
Pydantic would have checked that `risk` is one of three strings — worth
maybe thirty lines — while doing nothing for dead ends, unreachable nodes,
unconditional cycles, depth limits or secret scanning, which is where all the
real work is. Dropping it leaves the core with exactly one runtime
dependency, which matters twice over: `pipx install helpdesk-guide`
stays fast, and a single-file PyInstaller build has no binary wheel to
bundle. For a tool whose whole premise is working when the network is down,
install friction is a feature cost, not a packaging detail.

**Cost:** the loader does its own type coercion (`_as_list`, `_as_bool`,
`_as_int`). About eighty lines that pydantic would have provided.

---

## 2. PyYAML instead of ruamel.yaml

**Design (§13.1):** `ruamel.yaml`, to preserve comments for the content
editor UI.

**Built:** `PyYAML` with `yaml.safe_load`.

**Why:** the comment-preserving round-trip only pays off once there is a
content editor that writes YAML back, and that is phase 5. Until then
ruamel is a heavier dependency for a feature nobody uses. When the editor
arrives, swapping the loader is a contained change.

---

## 3. English enum values, Turkish accepted

**Design:** enum values in Turkish throughout (`type: soru`, `status:
yayinda`, `severity: orta`).

**Built:** canonical English values in the database and the code, with every
Turkish spelling from the design document accepted on input and folded to its
English equivalent (`content/schema.py`, `_ENUM_ALIASES`).

**Why:** the repository is bilingual by decision, and a contributor who does
not read Turkish still has to be able to review a pull request and tell a
`question` node from an `escalation` node. Keying the database on Turkish
strings would have made every future English-speaking contributor learn six
Turkish words before they could read a diff. Folding on input costs one dict
and keeps the design document's examples compiling unchanged.

Prose — titles, bodies, edge labels — stays in whatever language the file is
written in. That is the part that should be translated; keywords are not.

---

## 4. Three content tiers from day one

**Design:** tiers appear in §22 as a scaling concern, after the first twenty
runbooks.

**Built:** `tier` is in the schema, the compiler, the matcher and the UI from
the first commit.

**Why:** §22.4 is right that the long tail cannot be decision trees, and the
schema change it implies touches the records table, the search pool, the
scoring function and three templates. Adding it later would have meant a
migration plus a round of rewrites. Adding it now cost roughly a day and
means the first `guide` and the first `reference` card exist in the seed
content, so the model is exercised rather than theoretical.

The same argument applies to `verification`: §22.5 introduces it as a
scale-time safeguard, but it is four lines of scoring and two validation
rules, and retrofitting a confidence level onto content that was written
without one means re-reviewing everything.

---

## 5. An alias-match score floor

**Design (§8.3/§8.4):** a linear score, with ≥ 0.75 opening the runbook
directly.

**Built:** the same linear score, plus a floor when a curated alias matched
exactly (`_ALIAS_FLOOR` in `core/matcher.py`).

**Why:** the two halves of the design contradict each other on a small
corpus. `alias_exact` is capped at 0.25 of the total, so a perfect alias hit
can only reach 0.75 if BM25 also scores high — and BM25 collapses when there
are few documents, because the IDF term goes to nearly zero and everything
looks equally mediocre. Without a floor, the documented behaviour ("an exact
alias opens the runbook") would have been unreachable until the corpus was
several hundred records, which is exactly when a new deployment most needs
the tool to feel accurate.

The floor is applied *before* the verification multiplier, so unverified
content still cannot jump the queue.

---

## 6. Knowledge gaps include the clarification band

**Design (§8.4):** a search scoring below 0.20 is logged to
`knowledge_gaps`.

**Built:** anything that did not reach the shortlist band (0.40) is logged,
with its best score stored alongside.

**Why:** every FTS term is a prefix query, so almost any Turkish sentence
scrapes a weak match off some record. A strict "returned nothing" test left
the knowledge-gap report permanently empty in testing — and that report is
the most actionable output the tool has. Recording the clarify band too
turns it back into what §11.1 wants it to be: a ranked list of runbooks that
do not exist yet, written by the people who needed them. The stored
`best_score` lets an editor tell "nothing at all" from "nearly matched".

---

## 7. Records are archived, never deleted

**Design (§4):** compilation is a full rebuild of the content tables.

**Built:** child tables are rebuilt; `records` rows are upserted by `code`,
and a record whose file disappears is marked `archived`.

**Why:** this was a bug before it was a decision. `sessions.record_id` is a
foreign key into `records`, so deleting every row failed the moment anyone
had used the tool — meaning a content update was impossible after the first
call. Even with the constraint relaxed, reusing ids would silently re-point
old sessions at whatever record inherited the id. Stable ids are what make
the design's own promise work: telemetry outliving the content release that
produced it.

Archiving rather than deleting also keeps the history of a retired runbook
readable, and archived records are already excluded from search by the
`status` filter.

---

## 8. `version` is derived from the content hash

**Design (§7.1):** a `version` field on the runbook.

**Built:** the compiler bumps `version` when the source file's SHA-256
changes, and sessions pin the version they started on.

**Why:** a hand-maintained version number goes stale the first time someone
forgets to bump it, and the thing it is supposed to protect — "this session
followed DSP-001 v2, not v3" — silently stops being true. The hash is
already computed for integrity checking.

---

## 9. A tiny template renderer instead of Jinja2

**Design (§7.1):** escalation summary templates using `{{var}}` and
`{{#each}}`.

**Built:** exactly that, implemented in about forty lines
(`core/summary.py`).

**Why:** templates live in content YAML, and content arrives through pull
requests. Jinja2 on untrusted input is a sandbox-escape problem, and the
sandboxed variant is still a large attack surface for a feature that needs
string substitution and one loop. Using it would also have pulled Jinja2 into
the core, breaking the dependency rule above — `helpdesk run` produces a
summary and must work without the web extra.

---

## 10. Stemming is consistent rather than correct

**Design (§8.1):** rule-based suffix stripping for v1, Snowball or Zemberek
later.

**Built:** rule-based stripping, with two properties made explicit in the
code: every FTS term is a **prefix** query, and the stripper prefers a
candidate that lands on a known word over one that merely removes more
characters.

**Why:** prefix matching is what makes the whole approach viable. It means
over-stripping is harmless (`dosya` → `dosy` still matches `dosya*`) while
under-stripping is fatal (a query stem longer than the indexed one can never
match). That asymmetry turns a 200-line rule table into an adequate stand-in
for a morphological analyser.

The "prefer a known word" rule was added after a failing test:
`ekranda` was stripping `-nda` before `-da`, giving `ekr`, while the
protected word `ekran` stayed whole — so the two forms could never meet. That
is precisely the failure the approach exists to avoid, and longest-suffix-first
on its own does not prevent it.

---

## 10a. Tier priority is a tie-breaker, not a score term

**Design (§22.9):** "on an equal score, T1 comes first -- a tree guides
further than a card does."

**Built first:** a flat `+0.03` added to every runbook's score.

**Built now:** tier is absent from the score and appears only in the sort
key, after the score itself.

**Why:** a bonus large enough to change an outcome is also large enough to
change the *wrong* outcome. Adding six runbooks exposed it: for the query
"169.254 ip adresi alıyor", the reference card scored BM25 0.88 against the
general network runbook's 0.81 -- and lost, because +0.03 outweighed the
0.07 relevance gap. The design says "on an equal score", and a sort
tie-breaker is literally that.

---

## 10b. Synonym expansion contributes a concept, not a synonym storm

**Built first:** each query token expanded to its canonical concept *and
every synonym of it*, all OR-ed into the FTS query.

**Built now:** each token contributes at most two terms -- itself and its
concept -- and the compiler writes each record's canonical concepts into
the index.

**Why:** FTS terms are OR-ed, so one word like "ekran" was adding eight
terms, and BM25 rewarded whichever record happened to list the most
synonyms rather than the one the query meant. The query "açılırken mavi
ekrana düşüyor" ranked the *black screen* runbook first: it lists eight
display synonyms, so eight terms hit it, while the blue-screen runbook --
which the intent classifier had correctly identified -- matched one.

Indexing the concept keeps the bridge that expansion was there to build: a
record whose prose only says "display" is still reachable from "ekran",
through the shared concept rather than through a fan-out.

---

## 10c. Intent classification requires negation agreement

**Built first:** if the substring pass missed, fall back to matching the
stem of a trigger's first word against the query stems.

**Built now:** the same fallback, but every word of the trigger must be
present, and the trigger and the query must agree on negation.

**Why:** Turkish carries negation inside the verb, and the stemmer strips
it. "çalışmıyor" and "çalışıyor" both stem to "calis", so the phrase "her
şey ağır çalışıyor" -- a complaint about slowness -- was classified as
NOT_WORKING, the opposite of what it says. `is_negated` already existed
for the query; applying it to the trigger too costs one comparison.

---

## 11. Scope not yet built

Everything below is in the design and is deliberately not in v1.

| Design | Status |
|---|---|
| §19.4 `helpdesk new` interactive wizard | Partially: scaffolds from a template, prompts for code/title/category, but is not a full guided interview |
| §8.5 automatically derived clarifying questions | Authored `disambiguation` blocks only, as the design itself recommends for v1 |
| §22.9 hybrid semantic search (`sqlite-vec`) | Not built. BM25 + aliases is sufficient below roughly 500 records |
| §22.7 `helpdesk review` queue UI | Not built. `verification` is enforced; the review interface is not |
| §14 `.hgpack` signed content packages | Not built. `content-local/` covers the single-site case |
| §12.4 automated command execution | Not built, and deliberately so |
| Content editor UI | Not built. YAML plus `helpdesk lint` is the editing loop |

---

## 12. Things the design got right that are worth restating

Not everything here is a departure. Three decisions from the design turned
out to matter more during implementation than they look on paper:

**Content compiled into SQLite rather than parsed at runtime.** This makes
the runtime trivially fast, but the real benefit is that *the compiler is a
gate*. Every quality rule — dead ends, secrets, depth, alias collisions — has
somewhere to live, and bad content cannot reach the engine at all.

**One node, one action.** Almost every awkward moment while writing the seed
runbooks came from wanting to break this rule, and the result was always
better after splitting the node.

**The escalation summary as the selling point.** A technician who already
knows how to fix a monitor gains nothing from being walked through it. The
handover they would otherwise write by hand is what makes the tool worth
opening, and building it early meant the session recording was designed
around producing it rather than bolted on afterwards.
