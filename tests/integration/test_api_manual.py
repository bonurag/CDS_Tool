"""
Test di integrazione per le route API manual e CSV import.
Usa Flask test client con MANUAL_FILE reindirizzato su file temporaneo.
"""
import io
import json
import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_entry(**kw):
    base = {
        "categoria": "CF", "ev": "300 piani", "type": "corsa",
        "athlete": "ROSSI L.", "perf": "42.10", "wind": "",
        "piazz": "", "citta": "Brescia", "data": "15/05/2026",
        "anno": "", "pts": 500, "pts_ok": True,
        "isStaffetta": False, "rawStaff": "", "isManual": True,
        "soc_cod": "BS318", "soc_nome": "ATL. TEST",
    }
    base.update(kw)
    return base


def _post_json(client, url, payload):
    return client.post(url, data=json.dumps(payload),
                       content_type="application/json")


def _post_csv(client, content, filename="test.csv"):
    return client.post(
        "/api/manual/import_csv",
        data={"file": (io.BytesIO(content.encode("utf-8")), filename)},
        content_type="multipart/form-data",
    )


# ── GET /api/manual ───────────────────────────────────────────────────────────

class TestManualGet:
    def test_empty_returns_empty_list(self, flask_client):
        r = flask_client.get("/api/manual?categoria=CF")
        assert r.status_code == 200
        assert r.json["ok"] is True
        assert r.json["data"] == []

    def test_unknown_category_returns_empty_list(self, flask_client):
        r = flask_client.get("/api/manual?categoria=XX")
        assert r.json["data"] == []


# ── POST /api/manual ──────────────────────────────────────────────────────────

class TestManualPost:
    def test_save_returns_saved_id(self, flask_client):
        r = _post_json(flask_client, "/api/manual", _make_entry())
        assert r.json["ok"] is True
        assert r.json["savedId"].startswith("CF_")

    def test_saved_entry_retrievable(self, flask_client):
        _post_json(flask_client, "/api/manual", _make_entry())
        r = flask_client.get("/api/manual?categoria=CF")
        assert len(r.json["data"]) == 1
        assert r.json["data"][0]["ev"] == "300 piani"

    def test_missing_categoria_returns_error(self, flask_client):
        r = _post_json(flask_client, "/api/manual", {"ev": "300 piani"})
        assert r.json["ok"] is False

    def test_multiple_saves_accumulate(self, flask_client):
        _post_json(flask_client, "/api/manual", _make_entry(ev="300 piani"))
        _post_json(flask_client, "/api/manual", _make_entry(ev="80 piani"))
        r = flask_client.get("/api/manual?categoria=CF")
        assert len(r.json["data"]) == 2

    def test_categories_are_isolated(self, flask_client):
        _post_json(flask_client, "/api/manual", _make_entry(categoria="CF"))
        _post_json(flask_client, "/api/manual", _make_entry(categoria="CM", ev="80 piani"))
        assert len(flask_client.get("/api/manual?categoria=CF").json["data"]) == 1
        assert len(flask_client.get("/api/manual?categoria=CM").json["data"]) == 1

    def test_saved_id_is_unique(self, flask_client):
        r1 = _post_json(flask_client, "/api/manual", _make_entry())
        r2 = _post_json(flask_client, "/api/manual", _make_entry())
        assert r1.json["savedId"] != r2.json["savedId"]


# ── DELETE /api/manual/<saved_id> ─────────────────────────────────────────────

class TestManualDelete:
    def test_delete_removes_entry(self, flask_client):
        r = _post_json(flask_client, "/api/manual", _make_entry())
        saved_id = r.json["savedId"]
        flask_client.delete(f"/api/manual/{saved_id}")
        assert flask_client.get("/api/manual?categoria=CF").json["data"] == []

    def test_delete_unknown_id_is_idempotent(self, flask_client):
        r = flask_client.delete("/api/manual/CF_nonexistent_99999")
        assert r.json["ok"] is True

    def test_delete_removes_only_target(self, flask_client):
        id1 = _post_json(flask_client, "/api/manual", _make_entry(ev="300 piani")).json["savedId"]
        _post_json(flask_client, "/api/manual", _make_entry(ev="80 piani"))
        flask_client.delete(f"/api/manual/{id1}")
        data = flask_client.get("/api/manual?categoria=CF").json["data"]
        assert len(data) == 1
        assert data[0]["ev"] == "80 piani"

    def test_delete_cross_category(self, flask_client):
        """Cancellazione per savedId funziona anche se l'entry è in un'altra categoria."""
        id_cm = _post_json(flask_client, "/api/manual",
                           _make_entry(categoria="CM", ev="80 piani")).json["savedId"]
        flask_client.delete(f"/api/manual/{id_cm}")
        assert flask_client.get("/api/manual?categoria=CM").json["data"] == []


# ── POST /api/manual/import_csv ───────────────────────────────────────────────

class TestCsvImport:

    # ── errori strutturali ──────────────────────────────────────────────────

    def test_no_file_returns_error(self, flask_client):
        r = flask_client.post("/api/manual/import_csv")
        assert r.json["ok"] is False

    def test_missing_required_column_atleta(self, flask_client):
        csv = "categoria,gara,tipo,prestazione\nCF,300 piani,corsa,42.10\n"
        r = _post_csv(flask_client, csv)
        assert r.json["ok"] is False
        assert "atleta" in r.json["error"]

    def test_missing_required_column_gara(self, flask_client):
        csv = "categoria,tipo,prestazione,atleta\nCF,corsa,42.10,ROSSI L.\n"
        r = _post_csv(flask_client, csv)
        assert r.json["ok"] is False

    # ── riga valida ─────────────────────────────────────────────────────────

    def test_valid_row_imported(self, flask_client):
        csv = "categoria,gara,tipo,prestazione,atleta\nCF,300 piani,corsa,42.10,ROSSI L.\n"
        r = _post_csv(flask_client, csv)
        assert r.json["ok"] is True
        assert len(r.json["imported"]) == 1
        assert r.json["errors"] == []

    def test_imported_entry_has_saved_id(self, flask_client):
        csv = "categoria,gara,tipo,prestazione,atleta\nCF,300 piani,corsa,42.10,ROSSI L.\n"
        r = _post_csv(flask_client, csv)
        assert r.json["imported"][0]["savedId"].startswith("CF_")

    def test_import_persists_to_json(self, flask_client):
        csv = "categoria,gara,tipo,prestazione,atleta\nCF,300 piani,corsa,42.10,ROSSI L.\n"
        _post_csv(flask_client, csv)
        data = flask_client.get("/api/manual?categoria=CF").json["data"]
        assert len(data) == 1

    # ── normalizzazione discipline ──────────────────────────────────────────

    def test_gara_lowercase_normalized_to_canonical(self, flask_client):
        csv = "categoria,gara,tipo,prestazione,atleta\nCF,salto in alto,salto,1.65,BIANCHI M.\n"
        r = _post_csv(flask_client, csv)
        assert r.json["imported"][0]["ev"] == "Salto in alto"

    def test_gara_uppercase_normalized(self, flask_client):
        csv = "categoria,gara,tipo,prestazione,atleta\nCF,300 PIANI,corsa,42.10,ROSSI L.\n"
        r = _post_csv(flask_client, csv)
        assert r.json["imported"][0]["ev"] == "300 piani"

    # ── errori per riga ─────────────────────────────────────────────────────

    def test_invalid_categoria_produces_row_error(self, flask_client):
        csv = "categoria,gara,tipo,prestazione,atleta\nXX,300 piani,corsa,42.10,ROSSI L.\n"
        r = _post_csv(flask_client, csv)
        assert len(r.json["errors"]) == 1
        assert r.json["errors"][0]["riga"] == 2

    def test_invalid_gara_produces_row_error(self, flask_client):
        csv = "categoria,gara,tipo,prestazione,atleta\nCF,100 metri piani,corsa,11.50,ROSSI L.\n"
        r = _post_csv(flask_client, csv)
        assert len(r.json["errors"]) == 1
        assert "non riconosciuta" in r.json["errors"][0]["errori"][0]

    def test_invalid_gara_error_lists_accepted_values(self, flask_client):
        csv = "categoria,gara,tipo,prestazione,atleta\nCF,100 metri piani,corsa,11.50,ROSSI L.\n"
        r = _post_csv(flask_client, csv)
        msg = r.json["errors"][0]["errori"][0]
        assert "300 piani" in msg  # uno dei valori validi CF

    def test_invalid_tipo_produces_row_error(self, flask_client):
        csv = "categoria,gara,tipo,prestazione,atleta\nCF,300 piani,volo,42.10,ROSSI L.\n"
        r = _post_csv(flask_client, csv)
        assert len(r.json["errors"]) == 1

    def test_punti_not_integer_produces_row_error(self, flask_client):
        csv = "categoria,gara,tipo,prestazione,atleta,punti\nCF,300 piani,corsa,42.10,ROSSI L.,abc\n"
        r = _post_csv(flask_client, csv)
        assert len(r.json["errors"]) == 1

    def test_punti_negative_produces_row_error(self, flask_client):
        csv = "categoria,gara,tipo,prestazione,atleta,punti\nCF,300 piani,corsa,42.10,ROSSI L.,-10\n"
        r = _post_csv(flask_client, csv)
        assert len(r.json["errors"]) == 1

    # ── errori parziali ─────────────────────────────────────────────────────

    def test_partial_errors_imports_valid_rows(self, flask_client):
        csv = (
            "categoria,gara,tipo,prestazione,atleta\n"
            "CF,300 piani,corsa,42.10,ROSSI L.\n"        # riga 2 — valida
            "CF,100 metri piani,corsa,11.50,VERDI G.\n"  # riga 3 — gara invalida
            "CF,80 piani,corsa,10.20,BIANCHI M.\n"       # riga 4 — valida
        )
        r = _post_csv(flask_client, csv)
        assert r.json["ok"] is True
        assert len(r.json["imported"]) == 2
        assert len(r.json["errors"]) == 1
        assert r.json["errors"][0]["riga"] == 3

    def test_all_invalid_rows_ok_false_no_imported(self, flask_client):
        csv = (
            "categoria,gara,tipo,prestazione,atleta\n"
            "XX,300 piani,corsa,42.10,ROSSI L.\n"
            "CF,100 metri piani,corsa,11.50,VERDI G.\n"
        )
        r = _post_csv(flask_client, csv)
        assert r.json["imported"] == []
        assert len(r.json["errors"]) == 2

    # ── staffetta ──────────────────────────────────────────────────────────

    def test_staffetta_slash_separator(self, flask_client):
        csv = (
            'categoria,gara,tipo,prestazione,atleta\n'
            'CF,Staffetta 4 X 100,staffetta,56.42,'
            '"ROSSI L. / BIANCHI M. / VERDI G. / NERI A."\n'
        )
        r = _post_csv(flask_client, csv)
        imp = r.json["imported"][0]
        assert imp["isStaffetta"] is True
        assert len(imp["staffAthl"]) == 4

    def test_staffetta_comma_separator(self, flask_client):
        csv = (
            "categoria,gara,tipo,prestazione,atleta\n"
            '"CF","Staffetta 4 X 100","staffetta","56.42",'
            '"ROSSI L.,BIANCHI M.,VERDI G.,NERI A."\n'
        )
        r = _post_csv(flask_client, csv)
        imp = r.json["imported"][0]
        assert imp["isStaffetta"] is True
        assert len(imp["staffAthl"]) == 4

    # ── encoding ───────────────────────────────────────────────────────────

    def test_utf8_bom_accepted(self, flask_client):
        csv = "﻿" + "categoria,gara,tipo,prestazione,atleta\nCF,300 piani,corsa,42.10,ROSSI L.\n"
        r = _post_csv(flask_client, csv)
        assert r.json["ok"] is True
        assert len(r.json["imported"]) == 1

    def test_case_insensitive_headers(self, flask_client):
        csv = "CATEGORIA,GARA,TIPO,PRESTAZIONE,ATLETA\nCF,300 piani,corsa,42.10,ROSSI L.\n"
        r = _post_csv(flask_client, csv)
        assert r.json["ok"] is True
        assert len(r.json["imported"]) == 1


# ── GET /api/discipline_list ──────────────────────────────────────────────────

class TestDisciplineList:
    def test_returns_200(self, flask_client):
        assert flask_client.get("/api/discipline_list").status_code == 200

    def test_contains_all_categories(self, flask_client):
        data = flask_client.get("/api/discipline_list").json
        for cat in ["CF", "CM", "RF", "RM"]:
            assert cat in data

    def test_each_category_non_empty(self, flask_client):
        data = flask_client.get("/api/discipline_list").json
        for cat in ["CF", "CM", "RF", "RM"]:
            assert len(data[cat]) > 0

    def test_lists_are_sorted(self, flask_client):
        data = flask_client.get("/api/discipline_list").json
        for cat, gare in data.items():
            assert gare == sorted(gare), f"{cat} non è ordinata"


# ── GET /api/manual/template_csv ─────────────────────────────────────────────

class TestTemplateCSV:
    def test_returns_200(self, flask_client):
        assert flask_client.get("/api/manual/template_csv").status_code == 200

    def test_content_type_is_csv(self, flask_client):
        r = flask_client.get("/api/manual/template_csv")
        assert "text/csv" in r.content_type

    def test_first_line_contains_required_headers(self, flask_client):
        r = flask_client.get("/api/manual/template_csv")
        first_line = r.data.decode("utf-8").splitlines()[0]
        for col in ["categoria", "gara", "tipo", "prestazione", "atleta"]:
            assert col in first_line

    def test_has_example_rows(self, flask_client):
        r = flask_client.get("/api/manual/template_csv")
        lines = [l for l in r.data.decode("utf-8").splitlines() if l.strip()]
        assert len(lines) >= 2  # intestazione + almeno una riga esempio
