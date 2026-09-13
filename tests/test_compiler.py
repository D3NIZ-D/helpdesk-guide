"""Compilation: rebuilding content without destroying accumulated history."""

from __future__ import annotations

from helpdesk.content.compiler import compile_content
from helpdesk.core.engine import Engine
from helpdesk.core.session import SessionRecorder
from helpdesk.db.repo import Database


def _record_a_session(db: Database, code: str = "DSP-001") -> int:
    record = db.get_record(code)
    engine = Engine(record)
    state = engine.start()
    recorder = SessionRecorder(db)
    recorder.start(query_raw="ekran siyah", query_norm="ekran siyah",
                   record=record, state=state)
    state = engine.answer(state, "Hayır, hiç yanmıyor")
    recorder.save_state(state)
    recorder.finish(state, outcome="abandoned")
    return state.session_id


class TestRecompilation:
    def test_content_can_be_recompiled_after_the_tool_has_been_used(
        self, tmp_path, content_root
    ):
        # Sessions reference records by id.  Rebuilding content by deleting
        # every row used to fail on that foreign key, which meant a content
        # update was impossible the moment anybody ran a single call.
        db = Database(tmp_path / "h.db")
        assert compile_content(db, [content_root], strict=False).written
        _record_a_session(db)

        result = compile_content(db, [content_root], strict=False)
        assert result.written, [str(f) for f in result.report.errors]
        assert db.scalar("SELECT COUNT(*) FROM sessions") == 1

    def test_record_ids_stay_stable_across_recompiles(self, tmp_path, content_root):
        db = Database(tmp_path / "h.db")
        compile_content(db, [content_root], strict=False)
        before = db.scalar("SELECT id FROM records WHERE code = 'DSP-001'")
        compile_content(db, [content_root], strict=False)
        assert db.scalar("SELECT id FROM records WHERE code = 'DSP-001'") == before

    def test_a_session_still_resolves_its_record_after_a_recompile(
        self, tmp_path, content_root
    ):
        db = Database(tmp_path / "h.db")
        compile_content(db, [content_root], strict=False)
        session_id = _record_a_session(db)
        compile_content(db, [content_root], strict=False)

        row = db.query_one(
            "SELECT r.code FROM sessions s JOIN records r ON r.id = s.record_id "
            "WHERE s.id = ?",
            (session_id,),
        )
        assert row is not None and row["code"] == "DSP-001"

    def test_telemetry_survives_a_content_release(self, tmp_path, content_root):
        db = Database(tmp_path / "h.db")
        compile_content(db, [content_root], strict=False)
        SessionRecorder(db).record_gap("bulunamadi", "bulunamadi", 0.0)
        compile_content(db, [content_root], strict=False)
        assert db.scalar("SELECT COUNT(*) FROM knowledge_gaps") == 1

    def test_children_are_rebuilt_not_duplicated(self, tmp_path, content_root):
        db = Database(tmp_path / "h.db")
        compile_content(db, [content_root], strict=False)
        first = db.scalar("SELECT COUNT(*) FROM nodes")
        compile_content(db, [content_root], strict=False)
        assert db.scalar("SELECT COUNT(*) FROM nodes") == first

    def test_a_removed_record_is_archived_rather_than_deleted(
        self, tmp_path, temp_content
    ):
        db = Database(tmp_path / "h.db")
        compile_content(db, [temp_content], strict=False)
        # PRF-002 is a guide, so it is rendered rather than walked; the
        # session row is what matters here, not how it was produced.
        record_id = db.scalar("SELECT id FROM records WHERE code = 'PRF-002'")
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO sessions(started_at, query_raw, query_norm, record_id, "
                "outcome) VALUES('2026-09-01T10:00:00', 'disk doldu', 'disk doldu', ?, "
                "'resolved')",
                (record_id,),
            )

        removed = next(temp_content.glob("runbooks/tr/PRF-002-*.yaml"))
        removed.unlink()
        assert compile_content(db, [temp_content], strict=False).written

        row = db.query_one("SELECT status FROM records WHERE code = 'PRF-002'")
        assert row["status"] == "archived"
        # Archived content is invisible to search but its history remains.
        assert db.get_record("PRF-002") is None
        assert db.scalar("SELECT COUNT(*) FROM sessions") == 1


class TestVersioning:
    def test_the_version_is_bumped_only_when_the_file_changes(
        self, tmp_path, temp_content
    ):
        db = Database(tmp_path / "h.db")
        compile_content(db, [temp_content], strict=False)
        assert db.scalar("SELECT version FROM records WHERE code='DSP-001'") == 1

        compile_content(db, [temp_content], strict=False)
        assert db.scalar("SELECT version FROM records WHERE code='DSP-001'") == 1

        path = next(temp_content.glob("runbooks/tr/DSP-001-*.yaml"))
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "Monitörde görüntü yok", "Monitörde görüntü yok (güncellendi)"
            ),
            encoding="utf-8",
        )
        compile_content(db, [temp_content], strict=False)
        assert db.scalar("SELECT version FROM records WHERE code='DSP-001'") == 2


class TestContentOverride:
    def test_a_local_record_overrides_the_public_one(self, tmp_path, content_root):
        # The mechanism that lets an organisation keep private runbooks out
        # of the repository while still taking upstream updates.
        local = tmp_path / "content-local"
        (local / "runbooks" / "tr").mkdir(parents=True)
        (local / "runbooks" / "tr" / "DSP-001.yaml").write_text(
            """
code: DSP-001
tier: reference
title: "Kuruma özel monitör kaydı"
category: donanim/goruntu
status: yayinda
verification: dogrulanmis
aliases:
  - { term: "kuruma ozel belirti", kind: belirti }
  - { term: "ikinci kurum belirtisi", kind: belirti }
  - { term: "ucuncu kurum belirtisi", kind: belirti }
likely_causes: ["Kuruma özel sebep"]
fix_summary: "Kuruma özel çözüm."
related_runbook: NET-001
""",
            encoding="utf-8",
        )
        db = Database(tmp_path / "h.db")
        result = compile_content(db, [content_root, local], strict=False)
        assert result.written, [str(f) for f in result.report.errors]

        record = db.get_record("DSP-001")
        assert record["title"] == "Kuruma özel monitör kaydı"
        assert record["tier"] == "reference"


class TestBuildMetadata:
    def test_build_info_is_recorded(self, tmp_path, content_root):
        db = Database(tmp_path / "h.db")
        compile_content(db, [content_root], strict=False)
        info = db.build_info()
        assert info["compiled_at"]
        assert int(info["record_count"]) > 0

    def test_strict_mode_refuses_to_write_on_a_warning(self, tmp_path, temp_content):
        # CI compiles with --strict so a warning blocks the merge.
        path = next(temp_content.glob("runbooks/tr/DSP-001-*.yaml"))
        body = path.read_text(encoding="utf-8")
        # Leave a single alias: enough to warn (alias.too_few), not to error.
        head, _, tail = body.partition("aliases:")
        trimmed = tail.split("\n\n", 1)[1]
        path.write_text(
            head + 'aliases:\n  - { term: "tek bir belirti", kind: belirti }\n\n' + trimmed,
            encoding="utf-8",
        )
        db = Database(tmp_path / "h.db")
        result = compile_content(db, [temp_content], strict=True)
        assert not result.written
        assert result.report.warnings


class TestContentDiscovery:
    def test_templates_are_skipped_but_records_are_not(self, content_root):
        from helpdesk.content.loader import iter_content_files

        files = list(iter_content_files(content_root))
        assert files, "no content files discovered"
        assert not any("_TEMPLATE" in f.name for f in files)

    def test_discovery_survives_an_underscored_parent_directory(self, tmp_path, content_root):
        # An installed copy reads from site-packages/helpdesk/_bundled_content/.
        # Matching the template prefix against the *absolute* path made every
        # record vanish there, and the compile still reported success.
        import shutil

        from helpdesk.content.loader import iter_content_files

        bundled = tmp_path / "_bundled_content"
        shutil.copytree(content_root, bundled)

        files = list(iter_content_files(bundled))
        assert len(files) == len(list(iter_content_files(content_root)))
        assert not any("_TEMPLATE" in f.name for f in files)

    def test_an_installed_layout_compiles_the_same_records(self, tmp_path, content_root):
        import shutil

        bundled = tmp_path / "_bundled_content"
        shutil.copytree(content_root, bundled)

        db = Database(tmp_path / "h.db")
        result = compile_content(db, [bundled], strict=False)
        assert result.written
        assert result.records > 0
