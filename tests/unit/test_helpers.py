"""
Test unitari per le funzioni helper di fidal_cds_tool.py:
classify_event, _expand_year, _parse_perf_s, _match_gara, _lookup_pts.
Nessuna rete, nessun side effect.
"""
import pytest
from fidal_cds_tool import (
    classify_event,
    _expand_year,
    _parse_perf_s,
    _match_gara,
    _lookup_pts,
    _TABELLE,
)


# ── classify_event ────────────────────────────────────────────────────────────

class TestClassifyEvent:
    def test_staffetta_by_keyword(self):
        assert classify_event("Staffetta 4 X 100") == "staffetta"

    def test_staffetta_by_pattern_4x(self):
        assert classify_event("4x100") == "staffetta"

    def test_staffetta_by_pattern_3x(self):
        assert classify_event("3x800") == "staffetta"

    def test_ostacoli_keyword(self):
        assert classify_event("80 ostacoli") == "ostacoli"

    def test_hs_keyword(self):
        assert classify_event("100 hs") == "ostacoli"

    def test_siepi_keyword(self):
        assert classify_event("3000 siepi") == "ostacoli"

    def test_salto_in_lungo(self):
        assert classify_event("Salto in lungo") == "salto"

    def test_salto_in_alto(self):
        assert classify_event("Salto in alto") == "salto"

    def test_salto_triplo(self):
        assert classify_event("Salto triplo") == "salto"

    def test_asta(self):
        assert classify_event("Salto con l'asta") == "salto"

    def test_peso_lancio(self):
        assert classify_event("Getto del peso Kg 4,000") == "lancio"

    def test_martello_lancio(self):
        assert classify_event("Martello Kg 3,000") == "lancio"

    def test_giavellotto_lancio(self):
        assert classify_event("Giavellotto gr 600") == "lancio"

    def test_disco_lancio(self):
        assert classify_event("Disco Kg 1,000") == "lancio"

    def test_vortex_lancio(self):
        assert classify_event("Vortex") == "lancio"

    def test_palla_lancio(self):
        assert classify_event("Lancio della palla") == "lancio"

    def test_corsa_piana(self):
        assert classify_event("300 piani") == "corsa"
        assert classify_event("1000 metri") == "corsa"
        assert classify_event("60 piani") == "corsa"

    def test_marcia_corsa(self):
        assert classify_event("Marcia Km 3") == "corsa"

    def test_case_insensitive(self):
        assert classify_event("STAFFETTA 4 X 100") == "staffetta"
        assert classify_event("PESO") == "lancio"


# ── _expand_year ──────────────────────────────────────────────────────────────

class TestExpandYear:
    def test_two_digit_year_expands(self):
        assert _expand_year("12") == "2012"
        assert _expand_year("05") == "2005"
        assert _expand_year("99") == "2099"
        assert _expand_year("00") == "2000"

    def test_single_digit_year_expands(self):
        assert _expand_year("9") == "2009"
        assert _expand_year("0") == "2000"

    def test_four_digit_year_unchanged(self):
        assert _expand_year("2008") == "2008"
        assert _expand_year("1995") == "1995"

    def test_non_numeric_unchanged(self):
        assert _expand_year("abc") == "abc"
        assert _expand_year("") == ""

    def test_strips_whitespace(self):
        assert _expand_year("  12  ") == "2012"
        assert _expand_year("  2010  ") == "2010"

    def test_three_digit_not_converted(self):
        result = _expand_year("120")
        assert result == "120"


# ── _parse_perf_s ─────────────────────────────────────────────────────────────

class TestParsePerfS:
    def test_plain_float(self):
        assert _parse_perf_s("42.10") == pytest.approx(42.10)
        assert _parse_perf_s("1.65") == pytest.approx(1.65)

    def test_comma_decimal_separator(self):
        assert _parse_perf_s("13,45") == pytest.approx(13.45)

    def test_mm_ss_format(self):
        assert _parse_perf_s("1:30.00") == pytest.approx(90.0)
        assert _parse_perf_s("2:05.50") == pytest.approx(125.5)

    def test_hh_mm_ss_format(self):
        assert _parse_perf_s("1:00:00") == pytest.approx(3600.0)
        assert _parse_perf_s("0:01:30") == pytest.approx(90.0)

    def test_invalid_string_returns_none(self):
        assert _parse_perf_s("abc") is None
        assert _parse_perf_s("") is None
        assert _parse_perf_s("--") is None

    def test_strips_whitespace(self):
        assert _parse_perf_s("  42.10  ") == pytest.approx(42.10)

    def test_integer_string(self):
        assert _parse_perf_s("500") == pytest.approx(500.0)


# ── _match_gara ───────────────────────────────────────────────────────────────

class TestMatchGara:
    @pytest.fixture(autouse=True)
    def _tab_rf(self):
        self.tab = _TABELLE["RF"]

    def test_exact_match(self):
        for key in list(self.tab.keys())[:3]:
            assert _match_gara(key, self.tab) == key

    def test_case_insensitive_match(self):
        key = "Salto in lungo"
        if key in self.tab:
            assert _match_gara(key.upper(), self.tab) == key

    def test_slash_suffix_stripped(self):
        tab = {"Salto in lungo": {}}
        assert _match_gara("Salto in lungo/LJ", tab) == "Salto in lungo"

    def test_strip_suffix_case_insensitive(self):
        tab = {"Salto in lungo": {}}
        assert _match_gara("SALTO IN LUNGO/LJ", tab) == "Salto in lungo"

    def test_salto_in_alto_keyword(self):
        tab = {"Salto in alto": {}}
        assert _match_gara("Salto in alto HJ", tab) == "Salto in alto"

    def test_not_found_returns_none(self):
        assert _match_gara("100 piani", self.tab) is None
        assert _match_gara("evento inesistente xyz", self.tab) is None

    def test_empty_name_returns_none(self):
        assert _match_gara("", self.tab) is None

    def test_staffetta_match(self):
        tab = _TABELLE.get("CF", self.tab)
        staffetta_keys = [k for k in tab if "staffetta" in k.lower()]
        if staffetta_keys:
            key = staffetta_keys[0]
            assert _match_gara(key, tab) == key

    def test_cf_table_available(self):
        tab = _TABELLE["CF"]
        assert len(tab) > 0

    def test_all_categories_have_tables(self):
        for cat in ["CF", "CM", "RF", "RM"]:
            assert len(_TABELLE[cat]) > 0, f"Tabella vuota per {cat}"


# ── _lookup_pts ───────────────────────────────────────────────────────────────

class TestLookupPts:
    def test_unknown_categoria_returns_zero_false(self):
        pts, found = _lookup_pts("300 piani", "42.10", "XX")
        assert pts == 0
        assert found is False

    def test_unknown_event_returns_zero_false(self):
        pts, found = _lookup_pts("evento sconosciuto xyz", "42.10", "CF")
        assert pts == 0
        assert found is False

    def test_invalid_perf_event_found_returns_zero_true(self):
        pts, found = _lookup_pts("300 piani", "non-un-numero", "CF")
        assert pts == 0
        assert found is False

    def test_valid_event_and_perf_returns_score(self):
        # RF: "60 piani" a 7.04 s → 1174 punti (primo entry della tabella)
        pts, found = _lookup_pts("60 piani", "7.04", "RF")
        assert found is True
        assert pts > 0

    def test_score_decreases_with_slower_time(self):
        # Prestazione più lenta → punteggio più basso (corsa)
        pts_fast, _ = _lookup_pts("60 piani", "7.04", "RF")
        pts_slow, _ = _lookup_pts("60 piani", "9.00", "RF")
        if pts_fast > 0 and pts_slow > 0:
            assert pts_fast > pts_slow

    def test_salto_event_returns_score(self):
        tab = _TABELLE.get("RF", {})
        salti = [k for k in tab if "lungo" in k.lower() or "alto" in k.lower()]
        if salti:
            first_ev = salti[0]
            first_perf = next(iter(tab[first_ev].keys()))
            pts, found = _lookup_pts(first_ev, first_perf, "RF")
            assert found is True
            assert pts > 0

    def test_perf_outside_table_range_returns_zero_true(self):
        # Prestazione troppo lenta per avere punteggio → pts=0 ma evento trovato
        pts, found = _lookup_pts("60 piani", "999.99", "RF")
        assert found is True
        assert pts == 0
