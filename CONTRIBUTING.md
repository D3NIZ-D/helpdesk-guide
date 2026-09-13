# Contributing

Thank you for considering a contribution.

**If you want to add or fix a runbook, read
[CONTRIBUTING-CONTENT.md](CONTRIBUTING-CONTENT.md) instead.** Content matters
more than code in this project, and that guide assumes no programming.

---

## Setting up

```bash
git clone https://github.com/D3NIZ-D/helpdesk-guide && cd helpdesk-guide
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[web,dev]"

helpdesk compile      # build the bundled content
pytest -q             # should be green before you change anything
```

Python 3.10+. The core has exactly one runtime dependency (PyYAML) and that
is a deliberate constraint — see below.

## Before opening a pull request

```bash
ruff check src tests
pytest -q
helpdesk compile --strict
helpdesk eval --min-recall 0.90
```

CI runs the same four on Linux, macOS and Windows across Python 3.10–3.13.

---

## Architecture in one paragraph

Content is authored as YAML, compiled into SQLite by
`helpdesk.content.compiler`, and only ever *read* at runtime. The core
(`helpdesk.core`) holds the Turkish normaliser, the matcher, the tree engine
and the session recorder; it knows nothing about HTTP. `helpdesk.api` and
`helpdesk.cli` are two front ends over that same core. If you find yourself
writing domain logic inside a route handler, it belongs in `core`.

```
content/*.yaml  ──compile──►  SQLite + FTS5  ──►  core  ──►  cli
                                                       └──►  api ──► web UI
```

## Constraints worth knowing before you change things

These are not arbitrary; each one is load-bearing.

**The core must keep working with PyYAML alone.** `compile`, `lint`, `search`,
`run` and `report` may not import FastAPI, Jinja2 or anything else in the web
extra. That is what keeps `pipx install helpdesk-guide` fast and a single-file
PyInstaller build possible — and this is a tool for the moment the network is
down, so install friction is a real cost. Import web dependencies lazily,
inside the function that needs them.

**Content is data, never code.** `yaml.safe_load` only. Edge conditions are
parsed by a small hand-written evaluator, not `eval`. Summary templates use a
mustache subset, not Jinja2. Markdown renders with HTML disabled. Each of
these is a place where a runbook file could otherwise become arbitrary code
execution.

**The stemmer must stay consistent, not correct.** Queries and aliases go
through the same pipeline, and FTS terms are prefix queries, so over-stripping
is safe and under-stripping is not. If you make the stemmer more
linguistically accurate, run `helpdesk eval` — the number is the arbiter, not
intuition.

**Record ids are stable across recompiles.** `sessions.record_id` points at
them, and session history is meant to outlive the content release that
produced it. Records are upserted by `code`; a record whose file disappears is
archived, not deleted.

**Telemetry improves content, never ranks people.** There is deliberately no
per-technician report and there will not be one. Please do not add one.

## Adding a validation rule

Quality gates live in `helpdesk/content/validator.py`. Pick the level
carefully:

* `error` — the build stops. Use it when the content would strand a
  technician, leak a credential, or make the engine misbehave.
* `warning` — the build continues, but `--strict` (CI) fails. Use it for
  things that are wrong but not dangerous.
* `info` — reported by `helpdesk lint` only.

Add a test in `tests/test_validator.py` that fails without your rule. Then run
`helpdesk lint` against the shipped content: if your new rule fires on
existing runbooks, either the rule is too strict or the content needs fixing —
decide which, in the PR description.

## Touching search

Any change to `normalize.py`, `lexicon.py` or `matcher.py` must report its
effect on the golden query set:

```bash
helpdesk eval --json > before.json
# ... your change ...
helpdesk eval --json > after.json
```

Include the before/after Recall@1 in the PR. Improving one query while quietly
breaking three is the failure mode this suite exists to catch. If your change
is an improvement, add the queries that motivated it to
`tests/golden_queries.yaml`.

## Internationalisation

UI strings live in `src/helpdesk/web/locales/{tr,en}.json`. Both files must
carry the same keys — a test enforces it. Never hard-code user-facing text in a
template; use `t.key_name` with a sensible fallback.

Enum values in content YAML may be written in Turkish or English; the mapping
is in `helpdesk/content/schema.py`. Prose stays in whatever language the file
is written in, and content is organised by language under
`content/runbooks/<lang>/`.

## Commit and PR style

* One logical change per PR. A content PR and an engine PR are two PRs.
* Conventional-ish commit subjects are appreciated (`fix:`, `feat:`,
  `content:`, `docs:`) but not enforced.
* Explain *why* in the body. The what is visible in the diff.

## Security

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).

## Licence

Code contributions are licensed under the MIT licence; content contributions
under CC BY-SA 4.0. By opening a pull request you agree to license your work
under the relevant one.
