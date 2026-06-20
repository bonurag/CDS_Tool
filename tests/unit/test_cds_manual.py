"""
Test unitari per core/cds_manual.py.
Coprono: lettura, scrittura, robustezza su file assente o corrotto.
"""
import json
from pathlib import Path

import core.cds_manual as manual_module
from core.cds_manual import read_manual, write_manual


# ── read_manual ───────────────────────────────────────────────────────────────

class TestReadManual:
    def test_missing_file_returns_empty_dict(self, isolated_manual):
        assert read_manual() == {}

    def test_empty_file_returns_empty_dict(self, isolated_manual):
        Path(isolated_manual).write_text("", encoding="utf-8")
        assert read_manual() == {}

    def test_corrupted_json_returns_empty_dict(self, isolated_manual):
        Path(isolated_manual).write_text("not valid json {{", encoding="utf-8")
        assert read_manual() == {}

    def test_valid_file_returns_data(self, isolated_manual):
        data = {"CF": [{"ev": "300 piani", "pts": 500}]}
        Path(isolated_manual).write_text(json.dumps(data), encoding="utf-8")
        assert read_manual() == data

    def test_multiple_categories_preserved(self, isolated_manual):
        data = {
            "CF": [{"ev": "300 piani"}],
            "CM": [{"ev": "80 piani"}, {"ev": "1000 metri"}],
        }
        Path(isolated_manual).write_text(json.dumps(data), encoding="utf-8")
        result = read_manual()
        assert len(result["CF"]) == 1
        assert len(result["CM"]) == 2


# ── write_manual ──────────────────────────────────────────────────────────────

class TestWriteManual:
    def test_write_creates_file(self, isolated_manual):
        write_manual({"CF": []})
        assert Path(isolated_manual).exists()

    def test_roundtrip_preserves_data(self, isolated_manual):
        data = {"CM": [{"ev": "80 piani", "pts": 700, "athlete": "ROSSI M."}]}
        write_manual(data)
        assert read_manual() == data

    def test_write_overwrites_previous(self, isolated_manual):
        write_manual({"CF": [{"ev": "vecchio"}]})
        write_manual({"CF": [{"ev": "nuovo"}]})
        assert read_manual()["CF"][0]["ev"] == "nuovo"

    def test_write_produces_valid_json(self, isolated_manual):
        write_manual({"RF": [{"ev": "60 piani", "pts": 300}]})
        raw = Path(isolated_manual).read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert parsed["RF"][0]["pts"] == 300

    def test_unicode_preserved(self, isolated_manual):
        data = {"CF": [{"ev": "Salto con l'asta", "citta": "Città di Castello"}]}
        write_manual(data)
        assert read_manual()["CF"][0]["citta"] == "Città di Castello"


# ── MANUAL_FILE path ──────────────────────────────────────────────────────────

class TestManualFilePath:
    def test_manual_file_is_string(self):
        assert isinstance(manual_module.MANUAL_FILE, str)

    def test_manual_file_ends_with_json(self):
        assert manual_module.MANUAL_FILE.endswith("manual_entries.json")

    def test_data_dir_not_frozen(self):
        import sys
        # In modalità non-frozen _data_dir deve puntare alla root del progetto
        assert not getattr(sys, "frozen", False)
        data_dir = manual_module._data_dir()
        # Deve contenere 'core' come sottocartella (siamo in core/)
        assert Path(data_dir, "core").is_dir()
