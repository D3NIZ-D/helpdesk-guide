# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The engine uses [Semantic Versioning](https://semver.org/); content packs are
versioned by calendar (`content-tr 2026.09`), because the two change at very
different rates.

## [Unreleased]

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

[Unreleased]: https://github.com/OWNER/helpdesk-guide/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/OWNER/helpdesk-guide/releases/tag/v0.1.0
