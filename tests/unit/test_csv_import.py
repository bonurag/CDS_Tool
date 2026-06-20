"""
Test unitari per la logica di validazione e normalizzazione del CSV import.
Coprono: _find_gara_canonica, _gare_valide — nessuna rete, nessun file.
"""
import pytest
from fidal_cds_tool import _find_gara_canonica, _gare_valide


# ── _find_gara_canonica ───────────────────────────────────────────────────────

class TestFindGaraCanonica:

    # Match base
    def test_exact_match_returns_canonical(self):
        assert _find_gara_canonica("300 piani", "CF") == "300 piani"

    def test_uppercase_input_accepted(self):
        assert _find_gara_canonica("300 PIANI", "CF") == "300 piani"

    def test_mixed_case_accepted(self):
        assert _find_gara_canonica("Salto In Alto", "CF") == "Salto in alto"

    def test_leading_trailing_spaces_stripped(self):
        assert _find_gara_canonica("  80 piani  ", "CF") == "80 piani"

    def test_canonical_name_returned_not_input(self):
        # L'input è minuscolo, il canone ha maiuscola → deve tornare il canone
        result = _find_gara_canonica("salto in alto", "CF")
        assert result == "Salto in alto"
        assert result != "salto in alto"

    # Categoria corretta / errata
    def test_discipline_in_wrong_category_returns_none(self):
        # "80 piani" esiste per CF/CM ma non per RF/RM
        assert _find_gara_canonica("80 piani", "RF") is None
        assert _find_gara_canonica("80 piani", "RM") is None

    def test_discipline_correct_category_found(self):
        assert _find_gara_canonica("80 piani", "CF") == "80 piani"
        assert _find_gara_canonica("80 piani", "CM") == "80 piani"

    def test_ragazzi_discipline_found_in_rf_rm(self):
        assert _find_gara_canonica("60 piani", "RF") == "60 piani"
        assert _find_gara_canonica("60 piani", "RM") == "60 piani"

    def test_ragazzi_discipline_not_in_cadetti(self):
        assert _find_gara_canonica("60 piani", "CF") is None
        assert _find_gara_canonica("60 piani", "CM") is None

    # Staffetta
    def test_staffetta_case_insensitive(self):
        assert _find_gara_canonica("staffetta 4 x 100", "CF") == "Staffetta 4 X 100"
        assert _find_gara_canonica("STAFFETTA 4 X 100", "CM") == "Staffetta 4 X 100"

    # Errori
    def test_unknown_discipline_returns_none(self):
        assert _find_gara_canonica("100 metri piani", "CF") is None

    def test_unknown_category_returns_none(self):
        assert _find_gara_canonica("300 piani", "XX") is None

    def test_empty_string_returns_none(self):
        assert _find_gara_canonica("", "CF") is None


# ── _gare_valide ──────────────────────────────────────────────────────────────

class TestGareValide:

    def test_all_categories_have_disciplines(self):
        for cat in ["CF", "CM", "RF", "RM"]:
            assert len(_gare_valide(cat)) > 0, f"{cat} ha lista vuota"

    def test_returns_sorted_list(self):
        for cat in ["CF", "CM", "RF", "RM"]:
            gare = _gare_valide(cat)
            assert gare == sorted(gare), f"{cat} non è ordinata"

    def test_returns_list_type(self):
        assert isinstance(_gare_valide("CF"), list)

    def test_unknown_category_returns_empty(self):
        assert _gare_valide("XX") == []

    def test_all_categories_contain_staffetta(self):
        for cat in ["CF", "CM", "RF", "RM"]:
            names_lower = [g.lower() for g in _gare_valide(cat)]
            assert any("staffetta" in n for n in names_lower), \
                f"{cat} non contiene staffetta"

    def test_cadette_contain_lancio(self):
        # CF deve avere almeno un lancio (peso, martello, disco, giavellotto)
        lanci = [g for g in _gare_valide("CF")
                 if any(k in g.lower() for k in ("peso", "martello", "disco", "giavellotto"))]
        assert len(lanci) >= 2

    def test_no_duplicates(self):
        for cat in ["CF", "CM", "RF", "RM"]:
            gare = _gare_valide(cat)
            assert len(gare) == len(set(gare)), f"{cat} ha duplicati"

    def test_cf_cm_have_more_disciplines_than_rf_rm(self):
        # Cadetti hanno più discipline dei Ragazzi
        assert len(_gare_valide("CF")) > len(_gare_valide("RF"))
        assert len(_gare_valide("CM")) > len(_gare_valide("RM"))
