"""Decision-tree execution, session recording and the escalation summary."""

from __future__ import annotations

import pytest

from helpdesk.core.engine import Engine, EngineError, WalkState
from helpdesk.core.session import SessionRecorder, mask_pii
from helpdesk.core.summary import build_summary, render_template


@pytest.fixture
def dsp(compiled_db):
    return compiled_db.get_record("DSP-001")


@pytest.fixture
def engine(dsp):
    return Engine(dsp)


class TestWalking:
    def test_start_lands_on_the_entry_node(self, engine, dsp):
        state = engine.start()
        assert state.current == dsp["entry_node"]
        assert state.path == [dsp["entry_node"]]

    def test_answering_follows_the_labelled_edge(self, engine):
        state = engine.answer(engine.start(), "Hayır, hiç yanmıyor")
        assert state.current == "N20"
        assert state.answers["N10"] == "Hayır, hiç yanmıyor"

    def test_an_option_can_be_chosen_by_index(self, engine):
        by_label = engine.answer(engine.start(), "Hayır, hiç yanmıyor")
        by_index = engine.answer(engine.start(), "1")
        assert by_index.current == by_label.current

    def test_an_unknown_answer_is_refused(self, engine):
        with pytest.raises(EngineError, match="no edge"):
            engine.answer(engine.start(), "belki")

    def test_a_terminal_node_cannot_be_answered(self, engine):
        state = engine.start()
        for label in ["Hayır, hiç yanmıyor", "LED yandı, görüntü de geldi"]:
            state = engine.answer(state, label)
        assert engine.view(state).is_terminal
        with pytest.raises(EngineError, match="terminal"):
            engine.answer(state, "herhangi bir şey")

    def test_execution_is_deterministic(self, dsp):
        answers = ["Evet, turuncu / yanıp sönüyor", "Evet, 'sinyal yok' yazıyor",
                   "Hayır, doğrudan bağlı"]
        paths = []
        for _ in range(3):
            engine = Engine(dsp)
            state = engine.start()
            for answer in answers:
                state = engine.answer(state, answer)
            paths.append(list(state.path))
        assert paths[0] == paths[1] == paths[2]

    def test_every_reachable_branch_terminates(self, dsp):
        # Exhaustive walk: no sequence of answers may strand a technician.
        engine = Engine(dsp)

        def walk(state: WalkState, depth: int = 0) -> None:
            assert depth < 20, "tree does not terminate"
            view = engine.view(state)
            if view.is_terminal:
                return
            assert view.options, f"node {view.key} offers no way forward"
            for option in view.options:
                branch = WalkState(
                    code=state.code, version=state.version, current=state.current,
                    path=list(state.path), answers=dict(state.answers),
                    skipped=list(state.skipped), context=dict(state.context),
                )
                if option["target"] in branch.path:
                    continue  # already covered on this path
                walk(engine.answer(branch, option["label"]), depth + 1)

        walk(engine.start())


class TestBackAndSkip:
    def test_back_rewinds_and_invalidates_the_answer(self, engine):
        state = engine.answer(engine.start(), "Hayır, hiç yanmıyor")
        state = engine.back(state)
        assert state.current == "N10"
        assert "N10" not in state.answers

    def test_back_at_the_entry_node_is_a_no_op(self, engine):
        state = engine.back(engine.start())
        assert state.current == "N10"

    def test_a_skipped_step_is_recorded_separately(self, engine):
        state = engine.skip(engine.start(), "Hayır, hiç yanmıyor")
        assert "N10" in state.skipped
        # It is still answered -- the technician had to say what happened.
        assert state.answers["N10"] == "Hayır, hiç yanmıyor"

    def test_skips_are_dropped_when_rewound_past(self, engine):
        state = engine.skip(engine.start(), "Hayır, hiç yanmıyor")
        state = engine.back(state)
        assert "N10" not in state.skipped


class TestDecisionNodes:
    def test_a_decision_node_is_passed_silently(self, compiled_db):
        # NET-001 opens with a `karar` node that branches on context.
        engine = Engine(compiled_db.get_record("NET-001"))
        state = engine.start({"conn": "wifi"})
        assert engine.view(state).type != "decision"
        assert state.current == "N20", state.path

    def test_the_other_branch_is_taken_for_the_other_context(self, compiled_db):
        engine = Engine(compiled_db.get_record("NET-001"))
        assert engine.start({"conn": "ethernet"}).current == "N30"

    def test_without_context_it_falls_through_to_the_question(self, compiled_db):
        engine = Engine(compiled_db.get_record("NET-001"))
        state = engine.start()
        assert engine.view(state).type == "question"


class TestConditions:
    @pytest.mark.parametrize(
        "condition,context,expected",
        [
            ('os == "windows"', {"os": "windows"}, True),
            ('os == "windows"', {"os": "macos"}, False),
            ('os != "windows"', {"os": "macos"}, True),
            ('dock == "true"', {"dock": True}, True),
            (None, {}, True),
            ("garbage", {}, True),          # unparseable must not hide a branch
        ],
    )
    def test_condition_evaluation(self, condition, context, expected):
        assert Engine._condition_holds(condition, context) is expected

    def test_conditions_are_never_evaluated_as_python(self):
        # If this were eval(), the expression would raise or execute.
        assert Engine._condition_holds('__import__("os").system("echo x")', {}) is True


class TestVersionPinning:
    def test_the_session_pins_the_record_version(self, engine, dsp):
        # Recompiling content mid-call must not reroute a technician who
        # is already three steps in.
        assert engine.start().version == dsp["version"]


class TestSessionRecording:
    def test_a_walk_is_persisted_and_resumable(self, compiled_db):
        recorder = SessionRecorder(compiled_db)
        record = compiled_db.get_record("DSP-001")
        engine = Engine(record)
        state = engine.start()
        recorder.start(query_raw="ekran siyah", query_norm="ekran siyah",
                       record=record, state=state)
        state = engine.answer(state, "Hayır, hiç yanmıyor")
        recorder.save_state(state)

        resumed = recorder.load_state(state.session_id)
        assert resumed is not None
        assert resumed.current == state.current
        assert resumed.path == state.path

    def test_outcome_and_root_cause_are_stored(self, compiled_db):
        recorder = SessionRecorder(compiled_db)
        record = compiled_db.get_record("DSP-001")
        engine = Engine(record)
        state = engine.start()
        recorder.start(query_raw="q", query_norm="q", record=record, state=state)
        for label in ["Hayır, hiç yanmıyor", "LED yandı, görüntü de geldi"]:
            state = engine.answer(state, label)
        view = engine.view(state)
        recorder.finish(state, outcome="resolved", root_cause=view.root_cause)

        row = compiled_db.query_one(
            "SELECT outcome, root_cause FROM sessions WHERE id = ?", (state.session_id,)
        )
        assert row["outcome"] == "resolved"
        assert row["root_cause"] == "GUC_BAGLANTI"


class TestPiiMasking:
    @pytest.mark.parametrize(
        "raw,must_not_contain",
        [
            ("Ahmet Yılmaz outlook açamıyor", "Ahmet"),
            ("ahmet.yilmaz@sirket.com giriş yapamıyor", "@sirket.com"),
            ("kullanıcı 0532 123 45 67 numaradan aradı", "123 45 67"),
        ],
    )
    def test_personal_data_is_removed(self, raw, must_not_contain):
        assert must_not_contain not in mask_pii(raw)

    def test_the_symptom_survives_masking(self):
        masked = mask_pii("Ahmet Yılmaz outlook açamıyor")
        assert "outlook" in masked and "açamıyor" in masked

    def test_ordinary_queries_are_untouched(self):
        assert mask_pii("monitörüm çalışmıyor") == "monitörüm çalışmıyor"


class TestSummaryTemplates:
    def test_variables_and_loops_render(self):
        out = render_template(
            "A: {{a}}\n{{#each rows}}- {{name}}\n{{/each}}B",
            {"a": "1", "rows": [{"name": "x"}, {"name": "y"}]},
        )
        assert out == "A: 1\n- x\n- y\nB"

    def test_unknown_variables_become_empty(self):
        assert render_template("[{{nope}}]", {}) == "[]"

    def test_templates_cannot_execute_code(self):
        # Templates live in content, and content is untrusted input. This
        # renderer substitutes strings; it has no expression evaluator.
        out = render_template("{{ self.__class__ }}{% raw %}x{% endraw %}", {})
        assert "class" not in out
        assert "{% raw %}" in out  # Jinja syntax is inert text here

    def test_a_skipped_step_is_labelled_in_the_summary(self, compiled_db):
        record = compiled_db.get_record("DSP-001")
        engine = Engine(record)
        state = engine.skip(engine.start(), "Hayır, hiç yanmıyor")
        text = build_summary(
            record=record, state_dict=state.as_dict(),
            steps=engine.steps_taken(state), outcome="escalated",
            query_raw="ekran siyah", lang="tr",
        )
        assert "atlandı" in text

    def test_the_runbook_template_is_used_when_present(self, compiled_db):
        record = compiled_db.get_record("DSP-001")
        engine = Engine(record)
        state = engine.start()
        for label in ["Evet, turuncu / yanıp sönüyor",
                      "Hayır, tamamen siyah, OSD de açılmıyor"]:
            state = engine.answer(state, label)
        node = engine.nodes[state.current]
        text = build_summary(
            record=record, state_dict=state.as_dict(),
            steps=engine.steps_taken(state), outcome="escalated",
            query_raw="ekran siyah", node=node, lang="tr",
        )
        assert "Monitör ve GPU donanım testi gerekiyor" in text
        assert "DSP-001" in text
