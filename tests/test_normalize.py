"""Turkish normalisation — the edge cases that break naive implementations.

This is the most important test file in the project.  Every bug here
shows up as "the search does not find anything", which is the one failure
that makes people stop using the tool.
"""

from __future__ import annotations

import pytest

from helpdesk.core.normalize import (
    ascii_fold,
    extract_error_codes,
    normalize_query,
    normalize_term,
    stem,
    term_key,
    tr_lower,
)


class TestTurkishCasing:
    """``str.lower()`` is wrong for Turkish and the difference matters."""

    def test_dotted_capital_i_becomes_dotted_i(self):
        assert tr_lower("İSTANBUL") == "istanbul"
        assert tr_lower("MONİTÖR") == "monitör"

    def test_plain_capital_i_becomes_dotless_i(self):
        # The one everybody gets wrong: "IŞIK" must not become "ışik".
        assert tr_lower("IŞIK") == "ışık"
        assert tr_lower("AÇILMIYOR") == "açılmıyor"

    def test_python_default_would_have_been_wrong(self):
        assert "MONİTÖR".lower() != tr_lower("MONİTÖR")

    def test_english_loanwords_survive_the_full_pipeline(self):
        # tr_lower alone turns "HDMI" into "hdmı" -- correct Turkish, wrong
        # for a loanword. ASCII folding is what puts it back, which is why
        # the two steps are never used apart.
        assert tr_lower("HDMI") == "hdmı"
        assert normalize_term("HDMI Cable") == "hdmi cable"


class TestAsciiFolding:
    def test_all_turkish_diacritics(self):
        assert ascii_fold("çğıöşü") == "cgiosu"
        assert ascii_fold("ÇĞİÖŞÜ") == "CGIOSU"  # folding keeps case

    def test_folding_makes_keyboardless_typing_work(self):
        assert normalize_term("monitörüm") == normalize_term("monitorum")
        assert normalize_term("YAZICI ÇALIŞMIYOR") == normalize_term("yazici calismiyor")


class TestStemming:
    @pytest.mark.parametrize(
        "inflected",
        ["monitor", "monitorum", "monitorde", "monitorun", "monitorler", "monitorlerimiz"],
    )
    def test_monitor_inflections_collapse(self, inflected):
        assert stem(inflected) == "monitor"

    @pytest.mark.parametrize(
        "surface,expected",
        [
            ("calismiyor", "calis"),
            ("calisiyor", "calis"),
            ("baglanmiyor", "baglan"),
            ("yazdirmiyor", "yazdir"),
            ("ekranlar", "ekran"),
            ("ekranda", "ekran"),
        ],
    )
    def test_common_fault_verbs(self, surface, expected):
        assert stem(surface) == expected

    def test_short_words_are_left_alone(self):
        for word in ("usb", "ip", "dns", "led", "osd"):
            assert stem(word) == word

    def test_protected_it_terms_survive(self):
        # "veri" would lose its final vowel under the bare-vowel rule.
        assert stem("veri") == "veri"
        assert stem("kablo") == "kablo"
        assert stem("dock") == "dock"

    def test_a_stem_always_keeps_a_vowel(self):
        for word in ["monitorumuzun", "baglantilarimizdan", "yazicilarimiz"]:
            assert set("aeiou") & set(stem(word)), word

    def test_stemming_is_idempotent(self):
        # A second pass must not shorten the result further, or the query
        # and the indexed alias can end up on different tokens.
        for word in ["monitorum", "ekranlarda", "calismiyor", "yazicilar"]:
            assert stem(stem(word)) == stem(word), word


class TestTermKey:
    def test_word_order_does_not_matter(self):
        assert term_key("monitör çalışmıyor") == term_key("çalışmıyor monitör")

    def test_inflection_does_not_matter(self):
        assert term_key("monitörüm çalışmıyor") == term_key("monitör çalışmıyor")

    def test_different_symptoms_have_different_keys(self):
        assert term_key("ekran siyah") != term_key("yazıcı çalışmıyor")


class TestErrorCodes:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("mavi ekran 0x0000007B veriyor", "0x0000007b"),
            ("0X0000007b", "0x0000007b"),
            ("INACCESSIBLE_BOOT_DEVICE hatası", "inaccessible_boot_device"),
            ("DSP-001 lazım", "dsp-001"),
        ],
    )
    def test_codes_are_extracted_and_lowercased(self, text, expected):
        assert expected in extract_error_codes(text)

    def test_ordinary_prose_yields_no_codes(self):
        assert extract_error_codes("monitörüm çalışmıyor") == ()

    def test_codes_survive_punctuation_stripping(self):
        # Extraction happens before punctuation is removed, otherwise
        # "0x0000007B" becomes "0x0000007b" -> "0" "x0000007b".
        query = normalize_query("hata: 0x0000007B!")
        assert "0x0000007b" in query.error_codes


class TestNegation:
    @pytest.mark.parametrize(
        "text", ["monitör çalışmıyor", "internet yok", "ses gelmiyor", "açılmadı"]
    )
    def test_negative_reports_are_detected(self, text):
        assert normalize_query(text).negated is True

    def test_positive_statement_is_not_negated(self):
        assert normalize_query("monitör çalışıyor").negated is False


class TestFullPipeline:
    def test_stopwords_removed_only_with_a_lexicon(self, lexicon):
        without = normalize_query("bu bir sorun")
        with_lex = normalize_query("bu bir sorun", lexicon)
        assert len(with_lex.tokens) < len(without.tokens)

    def test_meaningful_negations_are_never_stopwords(self, lexicon):
        # "yok" and "değil" carry the entire meaning of a fault report.
        query = normalize_query("internet yok", lexicon)
        assert "yok" in query.tokens

    def test_synonyms_expand(self, lexicon):
        expanded = normalize_query("ekran siyah", lexicon).expanded
        assert "monitor" in expanded, expanded

    def test_intent_inferred(self, lexicon):
        assert "NO_DISPLAY" in normalize_query("görüntü gelmiyor", lexicon).intents

    def test_empty_query_is_empty(self):
        assert normalize_query("").is_empty
        assert normalize_query("   !!!  ").is_empty

    def test_fts_query_uses_prefix_matching(self, lexicon):
        # Prefix matching is what absorbs residual Turkish suffixes on the
        # indexed side; without the trailing '*' the whole design breaks.
        expression = normalize_query("monitörüm çalışmıyor", lexicon).fts_query()
        assert '"monitor"*' in expression
        assert " OR " in expression

    def test_fts_query_cannot_inject_fts_syntax(self):
        # Content and queries are data. An FTS5 operator typed by a user
        # must not become part of the MATCH expression.
        expression = normalize_query('monitor" OR aliases:x NEAR(').fts_query()
        assert "NEAR" not in expression.upper().replace('"NEAR"*', "")
        assert expression.count('"') % 2 == 0


class TestPossessiveSuffix:
    """Third-person possessive is the most common suffix in fault reports."""

    @pytest.mark.parametrize(
        "base,inflected",
        [("batarya", "bataryasi"), ("dosya", "dosyasi"), ("ses", "sesi")],
    )
    def test_the_possessive_form_reaches_the_base_form(self, base, inflected):
        # "bataryası dolmuyor" used to stem to a token that shared nothing
        # with "batarya", so the lexicon entry and every alias built on it
        # were unreachable from the query.
        assert stem(inflected) == stem(base)

    def test_stacked_suffixes_still_collapse(self):
        assert stem("bataryasinin") == stem("batarya")


class TestNegationAgreement:
    def test_a_positive_verb_is_not_read_as_its_negative(self, lexicon):
        # Stemming "çalışmıyor" yields "calis", which is also the stem of
        # "çalışıyor". Without a negation gate the intent classifier read
        # "her şey ağır çalışıyor" as NOT_WORKING -- the opposite of what
        # the sentence says.
        assert "NOT_WORKING" not in normalize_query("her sey agir calisiyor", lexicon).intents

    def test_the_negative_form_still_classifies(self, lexicon):
        assert "NOT_WORKING" in normalize_query("monitör çalışmıyor", lexicon).intents


class TestExpansionBudget:
    def test_a_token_contributes_at_most_its_concept(self, lexicon):
        # Fanning out to every synonym made one query word add eight OR
        # terms, all landing on whichever record listed the most synonyms.
        expanded = normalize_query("ekran", lexicon).expanded
        assert "monitor" in expanded          # the bridge still works
        assert len(expanded) <= 3, expanded   # but it is not a storm

    def test_abbreviations_still_expand(self, lexicon):
        # Expansion pushes *stems*, so "bsod" -> "mavi ekran" arrives as
        # ("mav", "ekran"); the trailing wildcard reaches "mavi" in the index.
        expanded = normalize_query("bsod aliyorum", lexicon).expanded
        assert stem("mavi") in expanded
        assert "ekran" in expanded
