"""
Test di integrazione per le route Flask: /api/tabelle, /api/ottimizza, /api/fidal_status.
Usa Flask test client. Le chiamate di rete verso FIDAL vengono mock-ate dove necessario.
"""
import json
import pytest
from unittest.mock import patch, MagicMock


# ── helpers fixture ────────────────────────────────────────────────────────────

def _rf_result(rid, ev, athlete, pts=500, is_staff=False):
    return {
        "id": rid, "ev": ev, "type": "corsa",
        "athlete": athlete, "perf": "10.00", "wind": "",
        "pts": pts, "pts_ok": True,
        "isStaffetta": is_staff, "rawStaff": "",
    }


def _rf_fixture():
    """8 risultati RF validi: 8 atleti distinti in 8 discipline diverse."""
    events = [
        ("60 piani",               "ALFA A.",   500),
        ("1000 metri",             "BETA B.",   480),
        ("Marcia Km 2",            "GAMMA C.",  460),
        ("Salto in alto",          "DELTA D.",  520),
        ("Salto in lungo",         "EPSILON E.", 510),
        ("Getto del peso Kg 2,000","ZETA F.",   490),
        ("Vortex",                 "ETA G.",    470),
        ("60 ostacoli H 0,60",     "THETA H.",  450),
    ]
    return [_rf_result(i, ev, athlete, pts) for i, (ev, athlete, pts) in enumerate(events)]


# ── GET /api/tabelle ──────────────────────────────────────────────────────────

class TestApiTabelle:
    def test_no_param_returns_all_categories(self, flask_client):
        r = flask_client.get("/api/tabelle")
        assert r.status_code == 200
        data = r.json
        assert data["ok"] is True
        for cat in ["CF", "CM", "RF", "RM"]:
            assert cat in data["tabelle"]

    def test_single_category_cf(self, flask_client):
        r = flask_client.get("/api/tabelle?categoria=CF")
        assert r.status_code == 200
        assert r.json["ok"] is True
        assert "CF" in r.json["tabelle"]
        assert "CM" not in r.json["tabelle"]

    def test_single_category_rf(self, flask_client):
        r = flask_client.get("/api/tabelle?categoria=RF")
        assert r.status_code == 200
        assert "RF" in r.json["tabelle"]

    def test_case_insensitive_category(self, flask_client):
        r = flask_client.get("/api/tabelle?categoria=cf")
        assert r.status_code == 200
        assert "CF" in r.json["tabelle"]

    def test_unknown_category_returns_404(self, flask_client):
        r = flask_client.get("/api/tabelle?categoria=XX")
        assert r.status_code == 404
        assert r.json["ok"] is False

    def test_tabelle_non_empty(self, flask_client):
        r = flask_client.get("/api/tabelle")
        for cat, table in r.json["tabelle"].items():
            assert len(table) > 0, f"Tabella vuota per {cat}"

    def test_cache_control_header_present(self, flask_client):
        r = flask_client.get("/api/tabelle?categoria=CF")
        assert "Cache-Control" in r.headers

    def test_each_event_has_scores_dict(self, flask_client):
        r = flask_client.get("/api/tabelle?categoria=RF")
        for ev, scores in r.json["tabelle"]["RF"].items():
            assert isinstance(scores, dict), f"{ev}: scores non è un dict"
            assert len(scores) > 0, f"{ev}: scores è vuoto"


# ── POST /api/ottimizza ───────────────────────────────────────────────────────

class TestApiOttimizza:
    def test_empty_data_returns_ok_no_optimal(self, flask_client):
        r = flask_client.post(
            "/api/ottimizza",
            data=json.dumps({"data": [], "categoria": "CF"}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert r.json["ok"] is True
        assert r.json["optimal"] is None

    def test_response_contains_required_keys(self, flask_client):
        r = flask_client.post(
            "/api/ottimizza",
            data=json.dumps({"data": [], "categoria": "CF"}),
            content_type="application/json",
        )
        for key in ("ok", "optimal", "baseline_score", "staff_scores"):
            assert key in r.json, f"Chiave mancante: {key}"

    def test_invalid_json_returns_error(self, flask_client):
        r = flask_client.post(
            "/api/ottimizza",
            data="non-json-!!",
            content_type="application/json",
        )
        assert r.status_code in (400, 500)

    def test_rf_valid_fixture_returns_optimal(self, flask_client):
        payload = {"data": _rf_fixture(), "categoria": "RF"}
        r = flask_client.post(
            "/api/ottimizza",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert r.json["ok"] is True
        opt = r.json["optimal"]
        assert opt is not None
        assert "score" in opt
        assert "sel" in opt
        assert opt["score"] > 0

    def test_rf_optimal_score_is_positive(self, flask_client):
        payload = {"data": _rf_fixture(), "categoria": "RF"}
        r = flask_client.post(
            "/api/ottimizza",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert r.json["optimal"]["score"] > 0

    def test_rf_optimal_sel_count_equals_nsel(self, flask_client):
        payload = {"data": _rf_fixture(), "categoria": "RF"}
        r = flask_client.post(
            "/api/ottimizza",
            data=json.dumps(payload),
            content_type="application/json",
        )
        sel = r.json["optimal"]["sel"]
        assert len(sel) == 8  # nSel=8 per RF

    def test_rf_optimal_has_required_sel_fields(self, flask_client):
        payload = {"data": _rf_fixture(), "categoria": "RF"}
        r = flask_client.post(
            "/api/ottimizza",
            data=json.dumps(payload),
            content_type="application/json",
        )
        for entry in r.json["optimal"]["sel"]:
            for field in ("ev", "athlete", "pts"):
                assert field in entry, f"Campo mancante in sel: {field}"

    def test_rf_baseline_score_positive(self, flask_client):
        payload = {"data": _rf_fixture(), "categoria": "RF"}
        r = flask_client.post(
            "/api/ottimizza",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert r.json["baseline_score"] > 0

    def test_rf_staff_scores_is_dict(self, flask_client):
        payload = {"data": _rf_fixture(), "categoria": "RF"}
        r = flask_client.post(
            "/api/ottimizza",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert isinstance(r.json["staff_scores"], dict)

    def test_default_categoria_cf_without_enough_data(self, flask_client):
        payload = {"data": [_rf_result(0, "60 piani", "ALFA A.", 500)]}
        r = flask_client.post(
            "/api/ottimizza",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert r.json["ok"] is True
        assert r.json["optimal"] is None


# ── GET /api/fidal_status ──────────────────────────────────────────────────────

class TestApiFidalStatus:
    def test_returns_200_when_reachable(self, flask_client):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            r = flask_client.get("/api/fidal_status")

        assert r.status_code == 200
        assert r.json["ok"] is True
        assert "latency_ms" in r.json
        assert isinstance(r.json["latency_ms"], int)
        assert r.json["http"] == 200

    def test_returns_ok_false_when_unreachable(self, flask_client):
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            r = flask_client.get("/api/fidal_status")

        assert r.status_code == 200
        assert r.json["ok"] is False
        assert "error" in r.json

    def test_error_message_truncated(self, flask_client):
        long_err = "x" * 200
        with patch("urllib.request.urlopen", side_effect=OSError(long_err)):
            r = flask_client.get("/api/fidal_status")

        assert len(r.json["error"]) <= 120

    def test_latency_ms_is_non_negative(self, flask_client):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            r = flask_client.get("/api/fidal_status")

        assert r.json["latency_ms"] >= 0


# ── GET / (index) ─────────────────────────────────────────────────────────────

class TestIndex:
    def test_index_returns_html(self, flask_client):
        r = flask_client.get("/")
        assert r.status_code == 200
        assert b"html" in r.data.lower() or b"<!DOCTYPE" in r.data

    def test_index_content_type_html(self, flask_client):
        r = flask_client.get("/")
        assert "text/html" in r.content_type


# ── GET /.well-known/appspecific/... ──────────────────────────────────────────

class TestWellKnown:
    def test_chrome_devtools_returns_empty_json(self, flask_client):
        r = flask_client.get("/.well-known/appspecific/com.chrome.devtools.json")
        assert r.status_code == 200
        assert r.json == {}
