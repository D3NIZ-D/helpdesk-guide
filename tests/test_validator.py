"""Content quality gates.

Each test here encodes a way a runbook can strand a technician mid-call,
leak a credential, or quietly become wrong.  The shipped content is also
validated, so a regression in the rules and a regression in the content
both fail here.
"""

from __future__ import annotations

import textwrap

import pytest

from helpdesk.content.loader import LoadError, load_file, load_roots
from helpdesk.content.secrets import scan_text
from helpdesk.content.validator import validate_record, validate_records

MINIMAL = """
code: TST-001
tier: runbook
title: "Test"
category: test/kategori
status: yayinda
verification: incelendi
aliases:
  - { term: "birinci belirti", kind: belirti }
  - { term: "ikinci belirti", kind: belirti }
  - { term: "ucuncu belirti", kind: belirti }
entry: N10
nodes:
  - key: N10
    type: soru
    title: "Soru?"
    edges:
      - { label: "Evet", to: N20 }
      - { label: "Hayır", to: N90 }
  - key: N20
    type: cozum
    title: "Çözüldü"
    root_cause: TEST_SEBEP
  - key: N90
    type: eskalasyon
    title: "Eskale"
    escalate_to: "L2"
"""


def write(tmp_path, body: str, name: str = "TST-001.yaml"):
    path = tmp_path / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def codes(report) -> set[str]:
    return {f.code for f in report.findings}


def error_codes(report) -> set[str]:
    return {f.code for f in report.errors}


class TestBaseline:
    def test_a_well_formed_runbook_passes(self, tmp_path):
        report = validate_record(load_file(write(tmp_path, MINIMAL)))
        assert report.ok, [str(f) for f in report.errors]


class TestGraphIntegrity:
    def test_dead_end_is_an_error(self, tmp_path):
        # A non-terminal node with no edge strands the technician.
        body = MINIMAL.replace(
            '  - key: N20\n    type: cozum\n    title: "Çözüldü"\n    root_cause: TEST_SEBEP',
            '  - key: N20\n    type: talimat\n    title: "Bir şey yap"\n    verify_text: "Oldu mu?"',
        )
        assert "node.dead_end" in error_codes(validate_record(load_file(write(tmp_path, body))))

    def test_dangling_edge_target_is_an_error(self, tmp_path):
        body = MINIMAL.replace("to: N20 }", "to: N99 }")
        assert "edge.dangling" in error_codes(validate_record(load_file(write(tmp_path, body))))

    def test_unreachable_node_is_an_error(self, tmp_path):
        body = MINIMAL + (
            "  - key: N50\n"
            "    type: cozum\n"
            '    title: "Kimsenin ulasamadigi cozum"\n'
            "    root_cause: ORPHAN\n"
        )
        assert "node.unreachable" in error_codes(validate_record(load_file(write(tmp_path, body))))

    def test_unconditional_cycle_is_an_error(self, tmp_path):
        body = MINIMAL.replace('      - { label: "Hayır", to: N90 }',
                               '      - { label: "Hayır", to: N10 }')
        assert "tree.cycle" in error_codes(validate_record(load_file(write(tmp_path, body))))

    def test_a_conditional_back_edge_is_allowed(self, tmp_path):
        # "try again with the other cable" is a legitimate loop.
        body = MINIMAL.replace(
            '      - { label: "Hayır", to: N90 }',
            '      - { label: "Hayır", to: N90 }\n'
            '      - { label: "Tekrar dene", to: N10, condition: \'retry == "true"\' }',
        )
        assert "tree.cycle" not in error_codes(validate_record(load_file(write(tmp_path, body))))

    def test_terminal_node_with_edges_is_an_error(self, tmp_path):
        body = MINIMAL.replace(
            '    root_cause: TEST_SEBEP',
            '    root_cause: TEST_SEBEP\n    edges:\n      - { label: "Devam", to: N90 }',
        )
        assert "node.terminal_with_edges" in error_codes(
            validate_record(load_file(write(tmp_path, body)))
        )

    def test_resolution_needs_a_root_cause(self, tmp_path):
        body = MINIMAL.replace("    root_cause: TEST_SEBEP\n", "")
        assert "resolution.no_root_cause" in error_codes(
            validate_record(load_file(write(tmp_path, body)))
        )

    def test_a_tree_that_can_never_succeed_is_an_error(self, tmp_path):
        body = MINIMAL.replace(
            '  - key: N20\n    type: cozum\n    title: "Çözüldü"\n    root_cause: TEST_SEBEP',
            '  - key: N20\n    type: eskalasyon\n    title: "Eskale 2"\n    escalate_to: "L2"',
        )
        assert "tree.no_resolution" in error_codes(validate_record(load_file(write(tmp_path, body))))

    def test_depth_over_seven_warns(self, tmp_path):
        chain = ['entry: N0', 'nodes:']
        for i in range(9):
            chain += [f'  - key: N{i}', '    type: talimat', f'    title: "Adım {i}"',
                      '    verify_text: "Oldu mu?"', '    edges:',
                      f'      - {{ label: "Devam", to: N{i + 1} }}']
        chain += ['  - key: N9', '    type: cozum', '    title: "Bitti"', '    root_cause: X']
        body = MINIMAL.split("entry:")[0] + "\n".join(chain)
        report = validate_record(load_file(write(tmp_path, body)))
        assert "tree.too_deep" in codes(report)


class TestRiskRules:
    def test_high_risk_without_rollback_is_an_error(self, tmp_path):
        body = MINIMAL.replace(
            '  - key: N20\n    type: cozum\n    title: "Çözüldü"\n    root_cause: TEST_SEBEP',
            '  - key: N20\n    type: talimat\n    title: "Tehlikeli iş"\n'
            '    risk: yuksek\n    verify_text: "Oldu mu?"\n'
            '    edges:\n      - { label: "Bitti", to: N90 }',
        )
        assert "risk.no_rollback" in error_codes(validate_record(load_file(write(tmp_path, body))))

    def test_high_risk_in_generated_content_is_an_error(self, tmp_path):
        # Deleting registry keys must never be suggested by content that
        # no human has read (design doc section 22.5).
        body = MINIMAL.replace("verification: incelendi", "verification: otomatik").replace(
            "status: yayinda", "status: taslak"
        ).replace(
            '  - key: N20\n    type: cozum\n    title: "Çözüldü"\n    root_cause: TEST_SEBEP',
            '  - key: N20\n    type: talimat\n    title: "Registry düzenle"\n'
            '    risk: yuksek\n    verify_text: "Oldu mu?"\n    rollback_md: "Geri al"\n'
            '    edges:\n      - { label: "Bitti", to: N90 }',
        )
        assert "risk.unverified_high" in error_codes(
            validate_record(load_file(write(tmp_path, body)))
        )

    def test_generated_content_cannot_be_published(self, tmp_path):
        body = MINIMAL.replace("verification: incelendi", "verification: otomatik")
        assert "verification.unreviewed_published" in error_codes(
            validate_record(load_file(write(tmp_path, body)))
        )


class TestSecretScanning:
    @pytest.mark.parametrize(
        "text",
        [
            "Yönetici parolası: Yaz2024!Sifre",
            "api_key: sk-abcdefghijklmnopqrstuvwx",
            "AKIA2QP7RTKBN4WVLZD3",
            "-----BEGIN RSA PRIVATE KEY-----",
            "psk: cokGizliOnPaylasimliAnahtar",
            "mysql://admin:hunter2pass@dbsunucu/vt",
        ],
    )
    def test_credentials_are_caught(self, text):
        assert scan_text(text), f"not caught: {text}"

    @pytest.mark.parametrize(
        "text",
        [
            'Kimlik bilgisi: parola kasasında **"Local-Admin-Workstations"** kaydı.',
            "Credential: vault entry Helpdesk-Unlock",
            "password: <kullanıcının kendi parolası>",
            "Parolayı kullanıcıya sesli ilet, bilete yazma.",
        ],
    )
    def test_documented_safe_phrasings_do_not_trip_the_gate(self, text):
        assert not scan_text(text), f"false positive: {text}"

    def test_a_secret_in_a_node_body_is_a_build_error(self, tmp_path):
        body = MINIMAL.replace(
            '    title: "Soru?"',
            '    title: "Soru?"\n    body_md: "Giriş yap, password: Kis2024Parola"',
        )
        report = validate_record(load_file(write(tmp_path, body)))
        assert any(f.code.startswith("secret.") for f in report.errors)

    def test_a_hit_is_never_echoed_in_full(self):
        hit = scan_text("api_key: sk-supersecretvaluethatmustnotleak")[0]
        assert "supersecretvaluethatmustnotleak" not in hit.excerpt
        assert "..." in hit.excerpt

    def test_a_high_confidence_rule_ignores_the_safe_word_allowlist(self):
        # "for example, AKIA..." is not a false positive -- it is a leaked
        # key with an excuse next to it.
        assert scan_text("for example: AKIA2QP7RTKBN4WVLZD3")

    def test_turkish_inflected_keywords_are_caught(self):
        # Turkish inflects the keyword too, so "parola" alone is not enough.
        for text in ["Yonetici parolasi: Yaz2024Sifre",
                     "sifresi = Kis2025Gizli",
                     "Kullanici parolam: Bahar2026x"]:
            assert scan_text(text), text

    def test_reviewed_false_positives_can_be_marked(self):
        assert not scan_text("password: örnek123456  # helpdesk:allow-secret")


class TestCrossRecord:
    def test_the_same_alias_in_two_records_is_an_error(self, tmp_path):
        first = load_file(write(tmp_path, MINIMAL, "a.yaml"))
        second = load_file(
            write(tmp_path, MINIMAL.replace("TST-001", "TST-002"), "b.yaml")
        )
        assert "alias.collision" in error_codes(validate_records([first, second]))

    def test_duplicate_codes_are_an_error(self, tmp_path):
        first = load_file(write(tmp_path, MINIMAL, "a.yaml"))
        second = load_file(write(tmp_path, MINIMAL.replace("birinci", "farkli"), "b.yaml"))
        assert "code.duplicate" in error_codes(validate_records([first, second]))


class TestTierRules:
    def test_a_guide_must_not_have_nodes(self, tmp_path):
        body = MINIMAL.replace("tier: runbook", "tier: guide")
        assert "guide.has_nodes" in error_codes(validate_record(load_file(write(tmp_path, body))))

    def test_a_reference_card_needs_content(self, tmp_path):
        body = """
        code: ERR-X-001
        tier: reference
        title: "Boş kart"
        category: test/kategori
        status: yayinda
        verification: incelendi
        aliases:
          - { term: "bir belirti", kind: belirti }
        """
        assert "reference.empty" in error_codes(validate_record(load_file(write(tmp_path, body))))


class TestInputSafety:
    def test_yaml_cannot_construct_python_objects(self, tmp_path):
        # Content is data. safe_load is what keeps a runbook from being code.
        path = write(tmp_path, "!!python/object/apply:os.system ['echo pwned']\n")
        with pytest.raises(LoadError):
            load_file(path)

    def test_unknown_enum_values_fail_loudly(self, tmp_path):
        body = MINIMAL.replace("type: soru", "type: sarki")
        with pytest.raises(LoadError):
            load_file(write(tmp_path, body))

    def test_turkish_and_english_enum_spellings_are_equivalent(self, tmp_path):
        turkish = load_file(write(tmp_path, MINIMAL, "tr.yaml"))
        english = load_file(write(
            tmp_path,
            MINIMAL.replace("type: soru", "type: question")
                   .replace("type: cozum", "type: resolution")
                   .replace("type: eskalasyon", "type: escalation")
                   .replace("status: yayinda", "status: published")
                   .replace("verification: incelendi", "verification: reviewed")
                   .replace("kind: belirti", "kind: symptom"),
            "en.yaml",
        ))
        assert [n.type for n in turkish.nodes] == [n.type for n in english.nodes]
        assert turkish.status == english.status == "published"
        assert turkish.verification == english.verification == "reviewed"


class TestShippedContent:
    def test_the_content_we_ship_is_clean(self, content_root):
        records, load_errors = load_roots([content_root])
        assert not load_errors, [str(e) for e in load_errors]
        report = validate_records(records)
        assert report.ok, [str(f) for f in report.errors]
        assert not report.warnings, [str(f) for f in report.warnings]

    def test_every_published_runbook_has_an_escalation_path(self, content_root):
        # Not every fault is fixable on the spot; a tree without an exit
        # sends the technician back to improvising.
        records, _ = load_roots([content_root])
        for record in records:
            if record.tier == "runbook" and record.status == "published":
                assert any(n.type == "escalation" for n in record.nodes), record.code

    def test_templates_are_not_compiled_as_content(self, content_root):
        records, _ = load_roots([content_root])
        assert not [r for r in records if r.code.startswith("XXX")]


class TestSecurityContentIsSafe:
    """Security runbooks must not talk a technician into making it worse."""

    def _record(self, content_root, code):
        records, _ = load_roots([content_root])
        found = [r for r in records if r.code == code]
        assert found, f"{code} is missing"
        return found[0]

    def test_the_phishing_runbook_never_opens_the_attachment(self, content_root):
        record = self._record(content_root, "SEC-001")
        text = record.searchable_body().lower()
        assert "eki açma" in text or "eki acma" in text
        # Every path where the user interacted must escalate, not resolve.
        interacted = [
            e for n in record.nodes for e in n.edges
            if "kimlik bilgisi girdi" in e.label.lower() or "eki açtı" in e.label.lower()
        ]
        assert interacted
        nodes = record.node_map
        for edge in interacted:
            assert nodes[edge.target].type == "escalation", edge.label

    def test_the_antivirus_runbook_never_restores_from_quarantine_alone(self, content_root):
        record = self._record(content_root, "SEC-002")
        # The only resolution that closes a quarantine case must require
        # the security team to have approved it.
        false_positive = [n for n in record.nodes if n.root_cause == "YANLIS_POZITIF"]
        assert false_positive, "SEC-002 has no false-positive resolution"
        approving = [
            n for n in record.nodes
            for e in n.edges
            if e.target == false_positive[0].key
        ]
        assert approving
        for node in approving:
            assert "güvenlik ekibi" in (node.verify_text or "").lower(), node.key

    def test_credential_resets_require_identity_verification_first(self, content_root):
        for code in ("ACC-001", "ACC-002"):
            record = self._record(content_root, code)
            resets = [
                n for n in record.nodes
                if n.risk == "high" and "sıfırla" in n.title.lower()
            ]
            assert resets, f"{code} has no high-risk reset node"
            for node in resets:
                body = (node.body_md or "").lower()
                assert "kimlik" in body and "doğrula" in body, (code, node.key)
