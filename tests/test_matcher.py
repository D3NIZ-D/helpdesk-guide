"""Search quality: scoring, confidence bands and the golden query set."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

GOLDEN = Path(__file__).with_name("golden_queries.yaml")

#: The bar CI holds the search to.  Raise it as content improves; never
#: lower it to make a failing build pass.
MIN_RECALL_AT_1 = 0.90
MIN_RECALL_AT_3 = 0.97


@pytest.fixture(scope="session")
def golden_cases() -> list[dict]:
    cases = yaml.safe_load(GOLDEN.read_text(encoding="utf-8")) or []
    assert len(cases) >= 40, "the golden set is too small to measure anything"
    return cases


@pytest.fixture(scope="session")
def golden_results(matcher, golden_cases) -> list[tuple[dict, list[str]]]:
    return [
        (case, [c.code for c in matcher.search(case["query"], limit=5).candidates])
        for case in golden_cases
    ]


class TestGoldenQuerySet:
    def test_recall_at_1(self, golden_results):
        misses = [
            (case["query"], case["expect"], codes[:3])
            for case, codes in golden_results
            if not codes or codes[0] != case["expect"]
        ]
        recall = 1 - len(misses) / len(golden_results)
        assert recall >= MIN_RECALL_AT_1, (
            f"Recall@1 {recall:.1%} < {MIN_RECALL_AT_1:.0%}\nMisses: {misses}"
        )

    def test_recall_at_3(self, golden_results):
        misses = [
            (case["query"], case["expect"], codes[:3])
            for case, codes in golden_results
            if case["expect"] not in codes[:3]
        ]
        recall = 1 - len(misses) / len(golden_results)
        assert recall >= MIN_RECALL_AT_3, (
            f"Recall@3 {recall:.1%} < {MIN_RECALL_AT_3:.0%}\nMisses: {misses}"
        )

    def test_every_case_meets_its_declared_rank(self, golden_results):
        failures = [
            (case["query"], case["expect"], codes[:3])
            for case, codes in golden_results
            if case["expect"] not in codes[: int(case.get("max_rank", 1))]
        ]
        assert not failures, failures

    def test_every_published_record_is_covered(self, compiled_db, golden_cases):
        # A record nobody searches for in the golden set is a record whose
        # search behaviour is unmeasured.
        expected = {str(case["expect"]).upper() for case in golden_cases}
        published = {r["code"] for r in compiled_db.list_records(limit=1000)}
        assert not (published - expected), f"no golden queries for: {published - expected}"


class TestConfidenceBands:
    def test_an_exact_alias_opens_the_record(self, matcher):
        result = matcher.search("ekran siyah")
        assert result.best.code == "DSP-001"
        assert result.best.score >= matcher.thresholds.direct - 0.01

    def test_an_error_code_is_an_unambiguous_match(self, matcher):
        result = matcher.search("0x0000007B")
        assert result.best.code == "ERR-WIN-0X7B"
        assert result.best.score >= matcher.thresholds.direct

    def test_nonsense_finds_nothing(self, matcher):
        assert matcher.search("zzzz qqqq xxxx").candidates == []

    def test_an_empty_query_is_handled(self, matcher):
        assert matcher.search("   ").action == "none"


class TestScoringSignals:
    def test_verification_level_multiplies_the_score(self, matcher, compiled_db):
        # Two records, same query strength, different verification: the
        # verified one must win (design doc section 22.5).
        from helpdesk.content.schema import VERIFICATION_WEIGHT
        assert VERIFICATION_WEIGHT["verified"] > VERIFICATION_WEIGHT["reviewed"]
        assert VERIFICATION_WEIGHT["reviewed"] > VERIFICATION_WEIGHT["draft"]
        assert VERIFICATION_WEIGHT["draft"] > VERIFICATION_WEIGHT["generated"]

        result = matcher.search("ekran siyah")
        signals = result.best.signals
        assert signals["verification_weight"] == VERIFICATION_WEIGHT[result.best.verification]

    def test_intent_agreement_is_a_signal(self, matcher):
        result = matcher.search("görüntü gelmiyor")
        assert result.best.signals["intent"] == 1.0

    def test_a_generic_complaint_gives_no_intent_opinion(self, matcher):
        # "çalışmıyor" says nothing about which category; scoring it as a
        # mismatch would bury every correct record.
        best = matcher.search("monitör çalışmıyor").best
        assert best.signals["intent"] >= 0.5

    def test_context_narrows_but_absence_is_neutral(self, matcher):
        without = matcher.search("ekran siyah").best
        with_ctx = matcher.search("ekran siyah", context={"os": "windows"}).best
        assert with_ctx.signals["context"] >= without.signals["context"]

    def test_signals_are_reported_for_debugging(self, matcher):
        signals = matcher.search("yazıcı çıktı vermiyor").best.signals
        for name in ("bm25", "alias_exact", "intent", "context", "history"):
            assert name in signals


class TestTierBehaviour:
    def test_all_tiers_share_one_search_pool(self, matcher):
        tiers = set()
        for query in ["ekran siyah", "ağ sürücüsü kayboldu", "0x0000007B"]:
            tiers.add(matcher.search(query).best.tier)
        assert tiers == {"runbook", "guide", "reference"}

    def test_a_runbook_outranks_a_card_on_a_tie(self):
        from helpdesk.content.schema import TIER_PRIORITY
        assert TIER_PRIORITY["runbook"] > TIER_PRIORITY["guide"] > TIER_PRIORITY["reference"]


class TestClarification:
    def test_an_ambiguous_query_offers_the_authored_question(self, matcher):
        result = matcher.search("bilgisayar")
        if result.action == "clarify":
            assert result.clarification["question"]
            assert result.clarification["options"]

    def test_a_near_tie_is_shortlisted_rather_than_opened(self, matcher):
        # High confidence in the top hit means nothing if the second is
        # just as good.
        for query in ["ekran", "yazıcı", "internet"]:
            result = matcher.search(query)
            if len(result.candidates) > 1:
                gap = result.candidates[0].score - result.candidates[1].score
                if gap < 0.05:
                    assert result.action != "open", query


class TestKnowledgeGaps:
    def test_a_failed_search_is_logged(self, fresh_db):
        from helpdesk.core.session import SessionRecorder
        recorder = SessionRecorder(fresh_db)
        recorder.record_gap("bulunamayan bir sey", "bulunamayan bir sey", 0.0)
        recorder.record_gap("bulunamayan bir sey", "bulunamayan bir sey", 0.0)
        row = fresh_db.query_one(
            "SELECT hit_count FROM knowledge_gaps WHERE query_norm = ?",
            ("bulunamayan bir sey",),
        )
        assert row["hit_count"] == 2

    def test_a_gap_is_masked_before_storage(self, fresh_db):
        from helpdesk.core.session import SessionRecorder
        SessionRecorder(fresh_db).record_gap(
            "Ahmet Yılmaz outlook açamıyor", "ahmet yilmaz outlook", 0.0
        )
        row = fresh_db.query_one("SELECT query_raw FROM knowledge_gaps")
        assert "Ahmet" not in row["query_raw"]
