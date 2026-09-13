# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The engine uses [Semantic Versioning](https://semver.org/); content packs are
versioned by calendar (`content-tr 2026.09`), because the two change at very
different rates.

## [Unreleased]

### Added

- The last six runbooks of the design document's v1 list (section 20.2):
  `DSP-002` display quality, `DOC-001` docking station, `PRN-002` printer
  setup, `ACC-002` MFA device change, `ACC-003` share permissions,
  `PER-001` peripherals. 26 records in total.
- **Alias containment scoring.** An alias whose words all appear in the
  query now counts, at a lower floor than an exact match. Set-equality
  matching meant one filler word destroyed the alias entirely: "printer
  bir türlü basmıyor" contains every word of "yazıcı basmıyor" and was
  scoring nothing.
- Tests that assert the security content stays safe: no phishing path
  where the user interacted resolves instead of escalating, no quarantine
  release without the security team's approval, and no credential reset
  without an identity check first.
- Six runbooks, completing the v1 content target of 20 records
  (design doc section 20.2): `PRF-001` slow computer, `APP-001` Outlook,
  `NET-002` Wi-Fi, `VPN-001` VPN, `PWR-002` laptop charging, `SEC-002`
  antivirus alert and quarantine.
- 37 more golden queries; the set is now 110.
- Turkish third-person possessive (`-sı/-si`) suffix stripping, without
  which "bataryası" never reached "batarya".
- Canonical concepts are written into the search index at compile time.

### Changed

- **Tier priority is now a sort tie-breaker rather than a score bonus.**
  A bonus large enough to matter was also large enough to overturn a real
  relevance difference, ranking a runbook above a reference card that
  BM25 scored higher. Section 22.9 asks for a tree to win *on an equal
  score*, and that is now literally what happens.
- **Synonym expansion no longer fans out.** Each query token contributes
  itself and its canonical concept, at most two terms. Fanning out to
  every synonym made one word like "ekran" add eight OR-ed terms that all
  landed on whichever record listed the most synonyms.
- **Body text no longer outranks titles and aliases.** The FTS body
  column indexes instruction prose, not symptom descriptions, and a long
  runbook accumulated enough incidental matches on generic verbs to win:
  the MFA runbook was top hit for a query about a slow computer because
  one of its escalation notes contains the word "bekletildiğini". Its
  BM25 weight dropped from 1.0 to 0.3.
- **Intent classification now requires negation agreement.** Stemming
  "çalışmıyor" yields "calis", which is also the stem of "çalışıyor", so
  "her şey ağır çalışıyor" was being classified as NOT_WORKING — the
  opposite of what it says.

### Fixed

- Two golden queries tested an unfair expectation rather than the engine
  and were rewritten; one of them literally said "açılmıyor" while
  expecting the slowness runbook.

## [0.1.0] — 2026-09-13

First working release. Engine, both front ends, and the seed content.

### Added

**Engine**

- Turkish-aware normalisation: correct dotted/dotless `i` casing, ASCII
  folding, rule-based suffix stripping, synonym expansion, fault-intent
  classification and error-code extraction.
- Hybrid matching over SQLite FTS5: BM25 plus curated-alias, intent, context
  and success-history signals, multiplied by a verification weight.
- Deterministic decision-tree engine with back, skip, loop detection,
  silent `decision` nodes and version pinning per session.
- Session recording with PII masking, resumable walk state, and generated
  escalation summaries.
- Content compiler and validator: graph integrity, dead ends, unreachable
  nodes, unconditional cycles, depth limit, risk/rollback rules, alias
  collisions, near-duplicate titles and a hard secret-scanning gate.
- Three content tiers — `runbook`, `guide`, `reference` — sharing one search
  pool, with cross-links from cards back to trees.
- Verification ladder (`verified` / `reviewed` / `draft` / `generated`)
  enforced in code: generated content cannot be published, and high-risk
  steps cannot come from unreviewed content.
- Seven content-quality reports, including knowledge gaps and root-cause
  distribution.

**Interfaces**

- CLI: `compile`, `lint`, `search`, `run`, `show`, `tree`, `new`, `eval`,
  `report`, `stats`, `prune`, `serve`.
- Local web UI on `127.0.0.1`, keyboard-first, with a token gate, `Origin`
  checks and a strict CSP.
- Bilingual interface (Turkish and English locale files); content YAML
  accepts enum values in either language.

**Content**

- 7 decision trees: `DSP-001`, `NET-001`, `ACC-001`, `PRN-001`, `SEC-001`,
  `PWR-001`, `BOOT-001`.
- 3 checklists: `NET-014`, `PRF-002`, `APP-002`.
- 4 reference cards: `ERR-WIN-0X7B`, `ERR-NET-APIPA`, `ERR-PRN-OFFLINE`,
  `ERR-AD-4740`.
- Turkish and English lexicons; templates for all three tiers.
- Golden query set of 73 queries — Recall@1 97.3%, Recall@3 100%.

**Project**

- CI on Linux, macOS and Windows across Python 3.10–3.13, including a
  clean-environment install smoke test.
- A content workflow that renders each changed decision tree as an ASCII
  diagram in the pull request, so logic can be reviewed without reading YAML.
- Release workflow producing single-file binaries with SHA-256 checksums.
- Dual licensing: MIT for the engine, CC BY-SA 4.0 for the content.

### Security

- `yaml.safe_load` only; edge conditions evaluated by a hand-written parser
  rather than `eval`; summary templates restricted to a mustache subset;
  Markdown rendered with HTML disabled.
- Compile-time secret scanning with structural rules that cannot be defused
  by surrounding prose.
- Local-only telemetry with configurable retention.

[Unreleased]: https://github.com/D3NIZ-D/helpdesk-guide/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/D3NIZ-D/helpdesk-guide/releases/tag/v0.1.0
