"""Command-line interface.

The CLI is not a lesser sibling of the web UI -- it is the same core with
a second front end (design doc section 13).  It is also the only interface
that exists during phase 0, which is deliberate: if a runbook cannot be
walked from a terminal, it is not well formed.

``argparse`` rather than typer/rich, so that ``compile``, ``lint``,
``search`` and ``run`` work with nothing installed but PyYAML.  The web
extra pulls in FastAPI; everything here does not need it.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import sys
import textwrap
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .config import Settings
from .content.compiler import compile_content
from .content.loader import load_file, load_roots
from .content.tree import render_record_tree, render_tree
from .content.validator import validate_records
from .core import reports as reports_mod
from .core.engine import Engine, EngineError
from .core.lexicon import Lexicon
from .core.matcher import Matcher
from .core.session import SessionRecorder
from .db.repo import Database

__all__ = ["main"]


# --------------------------------------------------------------------------
# Terminal helpers
# --------------------------------------------------------------------------

_USE_COLOR = (
    sys.stdout.isatty()
    and os.environ.get("NO_COLOR") is None
    and os.environ.get("TERM") != "dumb"
)


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def bold(text: str) -> str: return _c(text, "1")
def dim(text: str) -> str: return _c(text, "2")
def red(text: str) -> str: return _c(text, "31")
def green(text: str) -> str: return _c(text, "32")
def yellow(text: str) -> str: return _c(text, "33")
def blue(text: str) -> str: return _c(text, "36")


def _wrap(text: str, indent: str = "  ", width: int = 88) -> str:
    out: list[str] = []
    for line in (text or "").splitlines():
        if not line.strip():
            out.append("")
            continue
        out.extend(textwrap.wrap(line, width=width, initial_indent=indent,
                                 subsequent_indent=indent) or [indent])
    return "\n".join(out)


def _confidence_bar(score: float, width: int = 6) -> str:
    filled = max(0, min(width, round(score * width)))
    return "●" * filled + "○" * (width - filled)


_VERIFICATION_BADGE = {
    "verified": green("✔ doğrulanmış"),
    "reviewed": blue("• incelendi"),
    "draft": yellow("◦ taslak"),
    "generated": red("⚠ otomatik"),
}


# --------------------------------------------------------------------------
# Shared plumbing
# --------------------------------------------------------------------------

def _settings_from(args: argparse.Namespace) -> Settings:
    kwargs: dict[str, Any] = {}
    if getattr(args, "content", None):
        os.environ["HELPDESK_CONTENT"] = str(args.content)
    if getattr(args, "db", None):
        os.environ["HELPDESK_DB"] = str(args.db)
    if getattr(args, "lang", None):
        kwargs["lang"] = args.lang
    return Settings(**kwargs)


def _open_db(cfg: Settings, *, require_content: bool = True) -> Database:
    db = Database(cfg.db_path)
    if require_content and not db.is_initialised:
        print(red(f"No compiled database at {cfg.db_path}."), file=sys.stderr)
        print("Run 'helpdesk compile' first.", file=sys.stderr)
        raise SystemExit(2)
    return db


def _load_lexicon(cfg: Settings) -> Lexicon:
    return Lexicon.load(cfg.content_roots, lang=cfg.lang)


# --------------------------------------------------------------------------
# compile / lint
# --------------------------------------------------------------------------

def cmd_compile(args: argparse.Namespace) -> int:
    cfg = _settings_from(args)
    db = Database(cfg.db_path)
    roots = cfg.content_roots

    print(dim(f"content roots : {', '.join(str(r) for r in roots)}"))
    print(dim(f"database      : {cfg.db_path}"))

    result = compile_content(
        db, roots,
        strict=args.strict,
        strict_secrets=not args.relaxed_secrets,
        allow_drafts=args.allow_drafts,
    )

    for error in result.load_errors:
        print(red(f"LOAD ERROR {error}"), file=sys.stderr)
    for finding in result.report.errors:
        print(red(str(finding)), file=sys.stderr)
    for finding in result.report.warnings:
        print(yellow(str(finding)), file=sys.stderr)
    if args.verbose:
        for finding in result.report.infos:
            print(dim(str(finding)))

    if not result.written:
        reason = "errors" if not result.ok else "warnings (--strict)"
        print(red(f"\nCompile failed: {reason}. Nothing was written."), file=sys.stderr)
        return 1

    tiers = ", ".join(f"{count} {tier}" for tier, count in sorted(result.by_tier.items()))
    print(green(
        f"\nCompiled {result.records} records ({tiers}) · "
        f"{result.nodes} nodes · {result.steps} steps · {result.aliases} aliases"
    ))
    if result.report.warnings:
        print(yellow(f"{len(result.report.warnings)} warning(s) -- see above"))
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    cfg = _settings_from(args)
    roots = [Path(args.path)] if args.path else cfg.content_roots
    records, load_errors = load_roots(roots)

    for error in load_errors:
        print(red(f"LOAD ERROR {error}"), file=sys.stderr)

    report = validate_records(records, strict_secrets=not args.relaxed_secrets)
    for finding in report.errors:
        print(red(str(finding)))
    for finding in report.warnings:
        print(yellow(str(finding)))
    for finding in report.infos:
        print(dim(str(finding)))

    print()
    print(
        f"{len(records)} record(s) · "
        f"{red(str(len(report.errors)) + ' error')}, "
        f"{yellow(str(len(report.warnings)) + ' warning')}, "
        f"{dim(str(len(report.infos)) + ' info')}"
    )
    if load_errors or report.errors:
        return 1
    return 1 if (args.strict and report.warnings) else 0


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------

def cmd_search(args: argparse.Namespace) -> int:
    cfg = _settings_from(args)
    db = _open_db(cfg)
    matcher = Matcher(db, _load_lexicon(cfg), lang=cfg.lang)
    result = matcher.search(" ".join(args.query), limit=args.limit)

    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 0 if result.candidates else 1

    query = result.query
    print(bold(f'"{query.raw}"'))
    print(dim(f"  normalised : {query.norm}"))
    print(dim(f"  stems      : {' '.join(query.stems) or '-'}"))
    if query.intents:
        print(dim(f"  intent     : {', '.join(query.intents)}"))
    if query.error_codes:
        print(dim(f"  error code : {', '.join(query.error_codes)}"))
    print()

    if not result.candidates:
        print(yellow("No match. Logged as a knowledge gap."))
        SessionRecorder(db).record_gap(query.raw, query.norm, 0.0)
        return 1

    if result.is_knowledge_gap:
        SessionRecorder(db).record_gap(query.raw, query.norm, result.best_score)

    for candidate in result.candidates:
        badge = _VERIFICATION_BADGE.get(candidate.verification, candidate.verification)
        print(
            f"  {_confidence_bar(candidate.score)}  {candidate.score * 100:3.0f}%  "
            f"{bold(candidate.code)}  {candidate.title}"
        )
        print(dim(f"          {candidate.category} · {candidate.tier} · {badge} · via {candidate.method}"))
        if args.verbose:
            signals = " ".join(f"{k}={v:.2f}" for k, v in sorted(candidate.signals.items()))
            print(dim(f"          {signals}"))
    print()

    if result.action == "open":
        print(green(f"Confident match -- run:  helpdesk run {result.candidates[0].code}"))
    elif result.action == "clarify" and result.clarification:
        print(yellow("Ambiguous. Clarifying question:"))
        print(f"  {bold(result.clarification['question'])}")
        for option in result.clarification["options"]:
            print(f"    - {option['answer']}  →  {option['code']}")
    elif result.action == "shortlist":
        print(dim("Several candidates -- pick one with 'helpdesk run <CODE>'."))
    return 0


# --------------------------------------------------------------------------
# show / tree
# --------------------------------------------------------------------------

def cmd_show(args: argparse.Namespace) -> int:
    cfg = _settings_from(args)
    db = _open_db(cfg)
    record = db.get_record(args.code, include_unpublished=True)
    if not record:
        print(red(f"No record with code {args.code}."), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2, default=str))
        return 0

    badge = _VERIFICATION_BADGE.get(record["verification"], record["verification"])
    print(bold(f"{record['code']}  {record['title']}"))
    print(dim(f"{record['category_code']} · {record['tier']} · {record['severity']} · {badge}"))
    if record.get("summary"):
        print()
        print(_wrap(record["summary"]))

    if record["tier"] == "runbook":
        print()
        print(render_tree(record["nodes"], record["entry_node"]))
    elif record["tier"] == "guide":
        print()
        for step in record["steps"]:
            print(f"  {step['order_idx'] + 1}. {step['title']}")
            if step.get("note"):
                print(dim(_wrap(step["note"], indent="     ")))
    else:
        if record["likely_causes"]:
            print()
            print(bold("Olası nedenler:"))
            for cause in record["likely_causes"]:
                print(f"  - {cause}")
        if record.get("fix_summary"):
            print()
            print(_wrap(record["fix_summary"]))
        if record.get("source"):
            print()
            print(dim(f"Kaynak: {record['source'].get('name')} — {record['source'].get('url')}"))
    return 0


def cmd_tree(args: argparse.Namespace) -> int:
    cfg = _settings_from(args)
    target = Path(args.target)
    if target.is_file():
        record = load_file(target)
        print(bold(f"{record.code}  {record.title}"))
        print()
        print(render_record_tree(record))
        return 0

    db = _open_db(cfg)
    record = db.get_record(args.target, include_unpublished=True)
    if not record:
        print(red(f"No record or file named {args.target}."), file=sys.stderr)
        return 2
    print(bold(f"{record['code']}  {record['title']}"))
    print()
    print(render_tree(record["nodes"], record["entry_node"]))
    return 0


# --------------------------------------------------------------------------
# run -- the interactive walk
# --------------------------------------------------------------------------

_RISK_BANNER = {
    "high": red("  ▌ YÜKSEK RİSK — kullanıcıyı bilgilendir, geri alma adımını oku"),
    "medium": yellow("  ▌ Orta risk"),
}


def _prompt(question: str) -> str:
    try:
        return input(question).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(130) from None


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _settings_from(args)
    db = _open_db(cfg)

    code = args.code
    query_raw = args.code
    match_score, match_method = None, "manual"

    if args.query:
        matcher = Matcher(db, _load_lexicon(cfg), lang=cfg.lang)
        result = matcher.search(" ".join(args.query))
        if result.is_knowledge_gap:
            SessionRecorder(db).record_gap(
                result.query.raw, result.query.norm, result.best_score
            )
        if not result.candidates:
            print(yellow("No match. Logged as a knowledge gap."))
            return 1
        best = result.candidates[0]
        code, query_raw = best.code, result.query.raw
        match_score, match_method = best.score, best.method
        print(dim(f"→ {best.code} {best.title} ({best.score * 100:.0f}%)\n"))

    record = db.get_record(code, include_unpublished=True)
    if not record:
        print(red(f"No record with code {code}."), file=sys.stderr)
        return 2
    if record["tier"] != "runbook":
        return cmd_show(argparse.Namespace(**{**vars(args), "code": code, "json": False}))

    context: dict[str, Any] = {}
    for pair in args.context or []:
        key, _, value = pair.partition("=")
        if key:
            context[key.strip()] = value.strip()

    try:
        engine = Engine(record)
        state = engine.start(context)
    except EngineError as exc:
        print(red(str(exc)), file=sys.stderr)
        return 2

    recorder = SessionRecorder(db, agent_ref=cfg.agent_ref)
    recorder.start(
        query_raw=query_raw,
        query_norm=query_raw.lower(),
        record=record,
        state=state,
        match_score=match_score,
        match_method=match_method,
    )

    print(bold(f"{record['code']}  {record['title']}"))
    print(dim("Seçenek numarasını yaz · b geri · s atla · q çık\n"))

    outcome = "abandoned"
    while True:
        view = engine.view(state)
        total = view.step_number + view.est_remaining_steps
        print(bold(f"[{view.step_number}/~{total}] {view.title}"))
        for warning in view.warnings:
            banner = _RISK_BANNER.get(view.risk) if warning == "high_risk" else None
            if banner:
                print(banner)
            elif warning == "requires_admin":
                print(yellow("  ▌ Yönetici yetkisi gerekiyor"))
            elif warning == "user_downtime":
                print(yellow("  ▌ Kullanıcı çalışamayacak — önce haber ver"))
            elif warning == "loop_detected":
                print(red("  ▌ Bu adıma tekrar geldin — ağaçta döngü olabilir"))

        if view.body_md:
            print(_wrap(view.body_md))
        if view.rollback_md:
            print(dim(_wrap(f"Geri alma: {view.rollback_md}")))
        if view.verify_text:
            print(blue(_wrap(f"Nasıl anlarsın: {view.verify_text}")))

        if view.is_terminal:
            print()
            if view.type == "resolution":
                outcome = "resolved"
                print(green(f"  ✅ ÇÖZÜLDÜ — {view.title}"))
                if view.root_cause:
                    print(f"     Kök neden: {bold(view.root_cause)}")
                if view.closure_note:
                    print(_wrap(view.closure_note, indent="     "))
                if view.part_required:
                    print(f"     Parça talebi: {view.part_required}")
                if view.followup_md:
                    print(dim(_wrap(view.followup_md, indent="     ")))
            else:
                outcome = "escalated"
                print(yellow(f"  ⚠ ESKALASYON — {view.escalate_to or 'L2'}"))
            break

        print()
        for option in view.options:
            print(f"  {bold(str(option['index']))}) {option['label']}")
        print()

        answer = _prompt("> ").lower()
        if answer in {"q", "quit", "exit"}:
            break
        if answer in {"b", "back"}:
            state = engine.back(state)
            print()
            continue

        skip = answer.startswith("s")
        if skip:
            answer = answer[1:].strip()
            if not answer:
                answer = _prompt("  atla, hangi sonuçla devam? > ").strip()

        try:
            recorder.record_step(
                state,
                node_key=view.key,
                node_title=view.title,
                answer=next(
                    (o["label"] for o in view.options if str(o["index"]) == answer), answer
                ),
                skipped=skip,
            )
            state = engine.skip(state, answer) if skip else engine.answer(state, answer)
        except EngineError as exc:
            print(red(f"  {exc}"))
        print()

    root_cause = engine.view(state).root_cause
    recorder.record_step(
        state, node_key=state.current,
        node_title=engine.view(state).title, answer=None,
    )
    recorder.finish(state, outcome=outcome, root_cause=root_cause, resolution_node=state.current)

    print()
    print(bold("── Vaka özeti (ticket'a yapıştır) " + "─" * 40))
    print(recorder.summary(engine, state, query_raw=query_raw, outcome=outcome, lang=cfg.lang))
    print("─" * 74)
    return 0


# --------------------------------------------------------------------------
# eval -- the golden query set
# --------------------------------------------------------------------------

def cmd_eval(args: argparse.Namespace) -> int:
    """Measure search quality against the golden query set (section 13.3).

    Search quality only improves if it is measured.  This runs in CI on
    every content change, so adding a runbook that breaks an older query
    is caught in the pull request rather than three weeks later on a
    phone call.
    """
    import yaml

    cfg = _settings_from(args)
    db = _open_db(cfg)
    matcher = Matcher(db, _load_lexicon(cfg), lang=cfg.lang)

    path = Path(args.golden or (cfg.root / "tests" / "golden_queries.yaml"))
    if not path.is_file():
        print(red(f"Golden query set not found: {path}"), file=sys.stderr)
        return 2
    cases = yaml.safe_load(path.read_text(encoding="utf-8")) or []

    hits_at_1 = hits_at_3 = 0
    failures: list[dict[str, Any]] = []

    for case in cases:
        query = str(case.get("query", ""))
        expect = str(case.get("expect", "")).upper()
        max_rank = int(case.get("max_rank", case.get("min_rank", 1)))
        result = matcher.search(query, limit=5)
        codes = [c.code for c in result.candidates]
        rank = codes.index(expect) + 1 if expect in codes else None

        if rank == 1:
            hits_at_1 += 1
        if rank is not None and rank <= 3:
            hits_at_3 += 1
        if rank is None or rank > max_rank:
            failures.append({
                "query": query, "expect": expect, "max_rank": max_rank,
                "got": codes[:3], "rank": rank,
                "score": round(result.best_score, 3),
            })

    total = len(cases) or 1
    recall1, recall3 = hits_at_1 / total, hits_at_3 / total

    if args.json:
        print(json.dumps({
            "total": len(cases), "recall_at_1": round(recall1, 4),
            "recall_at_3": round(recall3, 4), "failures": failures,
        }, ensure_ascii=False, indent=2))
    else:
        for failure in failures:
            print(red(
                f"MISS  {failure['query']!r} → expected {failure['expect']} "
                f"within rank {failure['max_rank']}, got {failure['got'] or '(nothing)'}"
            ))
        print()
        colour = green if recall1 >= args.min_recall else red
        print(f"{len(cases)} queries · "
              f"Recall@1 {colour(f'{recall1:.1%}')} · Recall@3 {recall3:.1%}")

    return 0 if recall1 >= args.min_recall else 1


# --------------------------------------------------------------------------
# report / stats / prune
# --------------------------------------------------------------------------

_REPORT_TITLES = {
    "knowledge_gaps": "Bilgi boşlukları — bunları yaz",
    "dead_ends": "Ölü uçlar — bu dalları yeniden yaz",
    "escalation_hotspots": "Eskalasyon isabetleri — bu dalları derinleştir",
    "skipped_steps": "Hep atlanan adımlar — gereksizse sil",
    "slow_steps": "Beklenenden uzun süren adımlar — talimatı netleştir",
    "stale_content": "Bayat içerik — gözden geçirme ata",
    "root_cause_distribution": "Kök neden dağılımı — kalıcı düzeltme fırsatı",
}


def cmd_report(args: argparse.Namespace) -> int:
    cfg = _settings_from(args)
    db = _open_db(cfg)

    if args.name:
        function = getattr(reports_mod, args.name, None)
        if function is None:
            print(red(f"Unknown report {args.name!r}. "
                      f"Available: {', '.join(_REPORT_TITLES)}"), file=sys.stderr)
            return 2
        data = {args.name: function(db)}
    else:
        data = reports_mod.all_reports(db)

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return 0

    coverage = data.pop("coverage", None)
    if coverage:
        print(bold("Genel durum"))
        print(f"  Kayıt          : {coverage['records']}  {coverage['by_tier']}")
        print(f"  Doğrulama      : {coverage['by_verification']}")
        print(f"  Oturum         : {coverage['sessions']} "
              f"(çözüldü {coverage['resolved']}, eskale {coverage['escalated']}, "
              f"terk {coverage['abandoned']})")
        if coverage["first_contact_resolution"] is not None:
            print(f"  İlk temas çözüm: {coverage['first_contact_resolution']:.1%}")
        print(f"  Açık boşluk    : {coverage['open_knowledge_gaps']}")
        print(f"  Bayat kayıt    : {coverage['stale_records']}")
        print()

    for name, rows in data.items():
        if not rows:
            continue
        print(bold(_REPORT_TITLES.get(name, name)))
        for row in rows[: args.limit]:
            cells = " · ".join(f"{k}={v}" for k, v in row.items() if v not in (None, ""))
            print(f"  {cells}")
        print()
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    cfg = _settings_from(args)
    db = _open_db(cfg)
    info = db.build_info()
    print(bold("helpdesk-guide"))
    print(f"  version     : {__version__}")
    print(f"  database    : {cfg.db_path}")
    print(f"  compiled at : {info.get('compiled_at', '-')}")
    print(f"  records     : {info.get('record_count', '-')}")
    print(f"  lexicon     : {_load_lexicon(cfg).stats()}")
    print()
    print(bold("Kategoriler"))
    for category in db.list_categories():
        if category["record_count"]:
            print(f"  {category['code']:32} {category['record_count']}")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    """Delete telemetry older than the retention window (section 12.3)."""
    cfg = _settings_from(args)
    db = _open_db(cfg)
    days = args.days or cfg.retention_days
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()

    before = db.scalar("SELECT COUNT(*) FROM sessions") or 0
    with db.transaction() as conn:
        conn.execute("DELETE FROM sessions WHERE started_at < ?", (cutoff,))
        conn.execute("DELETE FROM knowledge_gaps WHERE last_seen < ?", (cutoff,))
        conn.execute("DELETE FROM feedback WHERE created_at < ?", (cutoff,))
    after = db.scalar("SELECT COUNT(*) FROM sessions") or 0
    db.connection.execute("VACUUM")
    print(green(f"Removed {before - after} session(s) older than {cutoff} ({days} days)."))
    return 0


# --------------------------------------------------------------------------
# new -- the scaffolding wizard
# --------------------------------------------------------------------------

_TEMPLATE_NAMES = {"runbook": "_TEMPLATE.runbook.yaml",
                   "guide": "_TEMPLATE.guide.yaml",
                   "reference": "_TEMPLATE.reference.yaml"}


def cmd_new(args: argparse.Namespace) -> int:
    """Scaffold a new content file from a template.

    Exists because a blank page is the real reason runbooks do not get
    written (section 20.4).  Everything it asks has a default.
    """
    cfg = _settings_from(args)
    tier = args.tier
    template_path = cfg.content_root / "_templates" / _TEMPLATE_NAMES[tier]
    if not template_path.is_file():
        print(red(f"Template missing: {template_path}"), file=sys.stderr)
        return 2

    code = (args.code or _prompt("Kod (ör. DSP-002): ")).strip().upper()
    if not code:
        print(red("Kod zorunlu."), file=sys.stderr)
        return 2
    title = (args.title or _prompt("Başlık: ")).strip()
    category = (args.category or _prompt("Kategori [donanim/genel]: ")).strip() or "donanim/genel"
    lang = args.lang or cfg.lang

    slug = "".join(
        ch if ch.isalnum() else "-"
        for ch in title.lower().replace("ı", "i").replace("ş", "s").replace("ğ", "g")
                       .replace("ü", "u").replace("ö", "o").replace("ç", "c")
    ).strip("-")
    slug = "-".join(filter(None, slug.split("-")))[:48] or "yeni"

    target = cfg.content_root / "runbooks" / lang / f"{code}-{slug}.yaml"
    if target.exists() and not args.force:
        print(red(f"{target} already exists (use --force to overwrite)."), file=sys.stderr)
        return 2

    body = template_path.read_text(encoding="utf-8")
    body = (body
            .replace("XXX-000", code)
            .replace("REPLACE_TITLE", title or "TODO: başlık")
            .replace("REPLACE_CATEGORY", category)
            .replace("REPLACE_AUTHOR", args.author or os.environ.get("USER")
                     or os.environ.get("USERNAME") or "unknown")
            .replace("REPLACE_DATE", dt.date.today().isoformat()))

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    print(green(f"Created {target}"))
    print(dim("Next:  helpdesk lint  ·  helpdesk compile  ·  helpdesk run " + code))
    return 0


# --------------------------------------------------------------------------
# serve
# --------------------------------------------------------------------------

def cmd_serve(args: argparse.Namespace) -> int:
    cfg = _settings_from(args)
    try:
        import uvicorn
    except ImportError:
        print(red("The web UI needs the 'web' extra:  pip install 'helpdesk-guide[web]'"),
              file=sys.stderr)
        return 2

    from .api.app import create_app

    _open_db(cfg)  # fail early with a clear message if content is not compiled
    token = cfg.issue_token()
    host, port = args.host or cfg.host, args.port or cfg.port

    if host not in {"127.0.0.1", "localhost", "::1"}:
        # Section 19.7: leaving loopback must be a deliberate, visible act.
        print(red("╔" + "═" * 68 + "╗"))
        print(red(f"║ WARNING: binding to {host}, not loopback.".ljust(69) + "║"))
        print(red("║ Anyone who can reach this port can read your runbooks and     ║"))
        print(red("║ session history. Use only on a trusted network.               ║"))
        print(red("╚" + "═" * 68 + "╝"))

    url = f"http://{host}:{port}/?t={token}"
    print(bold("helpdesk-guide"))
    print(f"  {url}")
    print(dim("  Ctrl+C ile durdur\n"))

    if not args.no_browser and host in {"127.0.0.1", "localhost"}:
        import webbrowser
        webbrowser.open(url)

    uvicorn.run(
        create_app(cfg), host=host, port=port,
        log_level="warning" if not args.verbose else "info",
    )
    return 0


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="helpdesk",
        description="Offline IT support troubleshooting assistant.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              helpdesk compile                     build content into SQLite
              helpdesk search "monitorum calismiyor"
              helpdesk run DSP-001                 walk a runbook
              helpdesk run -q "ekran siyah"        search, then walk the best match
              helpdesk tree content/runbooks/tr/DSP-001-*.yaml
              helpdesk eval                        score search against the golden set
              helpdesk report knowledge_gaps       what to write next
              helpdesk serve                       open the local web UI
        """),
    )
    parser.add_argument("--version", action="version", version=f"helpdesk-guide {__version__}")
    parser.add_argument("--db", help="path to the compiled database")
    parser.add_argument("--content", help="path to the content root")
    parser.add_argument("--lang", help="content language (default: tr)")
    parser.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("compile", help="compile YAML content into SQLite")
    p.add_argument("--strict", action="store_true", help="treat warnings as errors (CI)")
    p.add_argument("--allow-drafts", action="store_true", help="include status: draft records")
    p.add_argument("--relaxed-secrets", action="store_true",
                   help="skip the noisiest secret rule (long base64)")
    p.set_defaults(func=cmd_compile)

    p = sub.add_parser("lint", help="validate content without writing anything")
    p.add_argument("path", nargs="?", help="content root to lint")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--relaxed-secrets", action="store_true")
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("search", help="match a symptom against the content")
    p.add_argument("query", nargs="+")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("show", help="print one record")
    p.add_argument("code")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("tree", help="render a decision tree as ASCII")
    p.add_argument("target", help="record code or path to a YAML file")
    p.set_defaults(func=cmd_tree)

    p = sub.add_parser("run", help="walk a runbook interactively")
    p.add_argument("code", nargs="?", help="record code")
    p.add_argument("-q", "--query", nargs="+", help="search instead of naming a code")
    p.add_argument("--context", action="append", metavar="KEY=VALUE",
                   help="session context, e.g. --context os=windows")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("eval", help="score search against the golden query set")
    p.add_argument("--golden", help="path to golden_queries.yaml")
    p.add_argument("--min-recall", type=float, default=0.80)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("report", help="content-quality reports")
    p.add_argument("name", nargs="?", choices=sorted(_REPORT_TITLES))
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("stats", help="show build and corpus statistics")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("prune", help="delete telemetry past the retention window")
    p.add_argument("--days", type=int)
    p.set_defaults(func=cmd_prune)

    p = sub.add_parser("new", help="scaffold a new content file")
    p.add_argument("--tier", choices=["runbook", "guide", "reference"], default="runbook")
    p.add_argument("--code")
    p.add_argument("--title")
    p.add_argument("--category")
    p.add_argument("--author")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("serve", help="run the local web UI")
    p.add_argument("--host", help="bind address (default 127.0.0.1)")
    p.add_argument("--port", type=int)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_serve)

    return parser


def _force_utf8_output() -> None:
    """Make Turkish text survive the console.

    The Windows console still defaults to a legacy code page, which turns
    "Monitörde görüntü yok" into mojibake and can raise UnicodeEncodeError
    on the box-drawing characters in a tree diagram.  Reconfiguring the
    streams is cheaper than asking every user to run `chcp 65001`.
    """
    for stream in (sys.stdout, sys.stderr):
        # Not every stream is a reconfigurable TextIO (a pipe under some
        # runners is not), and failing to prettify output is never worth
        # failing the command over.
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _force_utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
