"""The local HTTP surface: security gate, walk lifecycle, JSON API."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from helpdesk.api.security import COOKIE_NAME


class TestSecurityGate:
    def test_no_token_means_no_access(self, client):
        client.cookies.clear()
        assert client.get("/").status_code == 403
        assert client.get("/api/search", params={"q": "x"}).status_code == 403

    def test_the_url_token_is_exchanged_for_a_cookie(self, tmp_path, content_root, monkeypatch):
        from fastapi.testclient import TestClient

        from helpdesk.api.app import create_app
        from helpdesk.config import Settings
        from helpdesk.content.compiler import compile_content
        from helpdesk.db.repo import Database

        home = tmp_path / "h"
        home.mkdir()
        monkeypatch.setenv("HELPDESK_HOME", str(home))
        cfg = Settings(home=home)
        compile_content(Database(cfg.db_path), [content_root], strict=False)
        token = cfg.issue_token()
        c = TestClient(create_app(cfg, token=token))

        assert c.get("/").status_code == 403
        response = c.get(f"/?t={token}")
        assert response.status_code == 200
        assert c.cookies.get(COOKIE_NAME) == token
        # The token is now in a cookie, so it no longer needs to be in the URL.
        assert c.get("/").status_code == 200

    def test_a_wrong_token_is_rejected(self, client):
        client.cookies.clear()
        assert client.get("/?t=kesinlikle-yanlis-token").status_code == 403

    def test_cross_origin_writes_are_refused(self, client):
        # The classic attack: a page in another tab drives the loopback API.
        response = client.post(
            "/run/DSP-001",
            data={"q": "x"},
            headers={"Origin": "http://kotu-site.example"},
            follow_redirects=False,
        )
        assert response.status_code == 403

    def test_same_origin_writes_are_allowed(self, client):
        response = client.post(
            "/run/DSP-001",
            data={"q": "x"},
            headers={"Origin": "http://127.0.0.1:8756"},
            follow_redirects=False,
        )
        assert response.status_code == 303

    def test_security_headers_are_present(self, client):
        headers = client.get("/").headers
        assert "script-src 'self'" in headers["Content-Security-Policy"]
        assert "'unsafe-inline'" not in headers["Content-Security-Policy"]
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Referrer-Policy"] == "no-referrer"

    def test_health_check_needs_no_token(self, client):
        client.cookies.clear()
        assert client.get("/healthz").status_code == 200

    def test_interactive_api_docs_are_not_exposed(self, client):
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404


class TestSearch:
    def test_the_search_page_renders_results(self, client):
        response = client.get("/", params={"q": "monitorum calismiyor"})
        assert response.status_code == 200
        assert "DSP-001" in response.text

    def test_the_json_api_returns_scored_candidates(self, client):
        payload = client.get("/api/search", params={"q": "yazıcı çıktı vermiyor"}).json()
        assert payload["candidates"][0]["code"] == "PRN-001"
        assert 0 < payload["candidates"][0]["confidence"] <= 100

    def test_a_fruitless_search_is_logged_as_a_gap(self, client):
        client.get("/", params={"q": "bulunamayacak tuhaf bir sorgu"})
        gaps = client.get("/api/reports", params={"name": "knowledge_gaps"}).json()
        assert any("tuhaf" in row["query_norm"] for row in gaps["knowledge_gaps"])


class TestWalkLifecycle:
    def _start(self, client, code="DSP-001"):
        response = client.post(f"/run/{code}", data={"q": "ekran siyah"},
                               follow_redirects=False)
        assert response.status_code == 303
        return response.headers["location"].rsplit("/", 1)[-1]

    def test_a_walk_can_be_started_and_resumed(self, client):
        session_id = self._start(client)
        first = client.get(f"/s/{session_id}")
        assert first.status_code == 200
        # A refresh must show the same step, not restart the call.
        assert client.get(f"/s/{session_id}").text == first.text

    def test_answering_advances_and_back_rewinds(self, client):
        session_id = self._start(client)
        client.post(f"/s/{session_id}/answer",
                    data={"label": "Hayır, hiç yanmıyor"}, follow_redirects=False)
        assert "Güç zincirini kontrol et" in client.get(f"/s/{session_id}").text

        client.post(f"/s/{session_id}/back", follow_redirects=False)
        assert "güç LED" in client.get(f"/s/{session_id}").text

    def test_reaching_a_resolution_closes_the_session(self, client):
        session_id = self._start(client)
        for label in ["Hayır, hiç yanmıyor", "LED yandı, görüntü de geldi"]:
            client.post(f"/s/{session_id}/answer", data={"label": label},
                        follow_redirects=False)
        page = client.get(f"/s/{session_id}")
        assert "Çözüldü" in page.text

        reports = client.get("/api/reports").json()
        assert reports["coverage"]["resolved"] >= 1

    def test_the_escalation_summary_is_generated(self, client):
        session_id = self._start(client)
        for label in ["Evet, turuncu / yanıp sönüyor",
                      "Hayır, tamamen siyah, OSD de açılmıyor"]:
            client.post(f"/s/{session_id}/answer", data={"label": label},
                        follow_redirects=False)
        summary = client.get(f"/s/{session_id}/summary").text
        assert "DSP-001" in summary
        assert "Monitörün güç LED'i yanıyor mu?" in summary
        assert "L2-Donanim" in summary

    def test_an_invalid_answer_is_rejected(self, client):
        session_id = self._start(client)
        response = client.post(f"/s/{session_id}/answer",
                               data={"label": "böyle bir seçenek yok"},
                               follow_redirects=False)
        assert response.status_code == 400

    def test_a_guide_cannot_be_walked(self, client):
        assert client.post("/run/NET-014", data={"q": "x"},
                           follow_redirects=False).status_code == 400

    def test_an_unknown_record_is_a_404(self, client):
        assert client.post("/run/YOK-999", data={"q": "x"},
                           follow_redirects=False).status_code == 404


class TestContentPages:
    @pytest.mark.parametrize("code", ["DSP-001", "NET-014", "ERR-WIN-0X7B"])
    def test_every_tier_renders(self, client, code):
        assert client.get(f"/r/{code}").status_code == 200

    def test_a_runbook_page_offers_the_ascii_tree(self, client):
        tree = client.get("/r/DSP-001/tree").text
        assert "N10" in tree and "[OK]" in tree

    def test_browse_and_reports_render(self, client):
        assert client.get("/browse").status_code == 200
        assert client.get("/reports").status_code == 200

    def test_markdown_renders_with_html_disabled(self):
        # Content is data: markup inside a runbook body must render as
        # visible text, not as markup. The CSP is the second line of
        # defence; this is the first.
        from helpdesk.api.app import _render_markdown

        rendered = _render_markdown(
            'Adım <script>alert("x")</script> ve <img src=x onerror=y>'
        )
        assert "<script>" not in rendered
        assert "onerror" not in rendered or "&lt;img" in rendered
        assert "alert" in rendered  # shown as text, so an editor can see it

    def test_runbook_markdown_still_formats(self):
        from helpdesk.api.app import _render_markdown

        assert "<strong>" in _render_markdown("**kalın**")
        assert "<li>" in _render_markdown("1. birinci\n2. ikinci")

    def test_feedback_is_recorded(self, client):
        response = client.post(
            "/api/feedback",
            json={"kind": "did_not_work", "code": "DSP-001", "node_key": "N20"},
        )
        assert response.status_code == 200 and response.json()["ok"]

    def test_an_unknown_feedback_kind_is_rejected(self, client):
        assert client.post("/api/feedback", json={"kind": "sabotaj"}).status_code == 400


class TestJsonSessionApi:
    def test_the_whole_lifecycle_works_over_json(self, client):
        started = client.post("/api/session", json={"code": "DSP-001", "query": "ekran siyah"})
        assert started.status_code == 200
        session_id = started.json()["state"]["session_id"]
        assert started.json()["node"]["key"] == "N10"

        answered = client.post(
            f"/api/session/{session_id}/answer",
            json={"label": "Hayır, hiç yanmıyor"},
        )
        assert answered.json()["node"]["key"] == "N20"

        state = client.get(f"/api/session/{session_id}").json()
        assert state["state"]["path"] == ["N10", "N20"]

    def test_a_skipped_step_is_flagged_over_json(self, client):
        session_id = client.post(
            "/api/session", json={"code": "DSP-001"}
        ).json()["state"]["session_id"]
        response = client.post(
            f"/api/session/{session_id}/answer",
            json={"label": "Hayır, hiç yanmıyor", "skip": True},
        )
        assert "N10" in response.json()["state"]["skipped"]
