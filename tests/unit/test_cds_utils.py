"""
Test unitari per core/cds_utils.py.
Coprono: classificatori evento (is_lancio, is_salto, is_ostacoli),
chiave atleta, staffetta, filtri programma CdS per categoria.
"""
import pytest
from core.cds_utils import CdsUtils


# ── is_lancio ─────────────────────────────────────────────────────────────────

class TestIsLancio:
    def test_peso_is_lancio(self):
        assert CdsUtils.is_lancio("Getto del peso Kg 4,000") is True

    def test_martello_is_lancio(self):
        assert CdsUtils.is_lancio("Martello Kg 3,000") is True

    def test_giavellotto_is_lancio(self):
        assert CdsUtils.is_lancio("Giavellotto gr 600") is True

    def test_disco_is_lancio(self):
        assert CdsUtils.is_lancio("Disco Kg 1,000") is True

    def test_lancio_palla_is_lancio(self):
        assert CdsUtils.is_lancio("Lancio della palla") is True

    def test_vortex_is_lancio(self):
        assert CdsUtils.is_lancio("Vortex") is True

    def test_case_insensitive(self):
        assert CdsUtils.is_lancio("PESO") is True
        assert CdsUtils.is_lancio("Martello") is True

    def test_corsa_is_not_lancio(self):
        assert CdsUtils.is_lancio("300 piani") is False
        assert CdsUtils.is_lancio("80 piani") is False

    def test_salto_is_not_lancio(self):
        assert CdsUtils.is_lancio("Salto in alto") is False
        assert CdsUtils.is_lancio("Salto in lungo") is False

    def test_empty_string_is_not_lancio(self):
        assert CdsUtils.is_lancio("") is False

    def test_staffetta_is_not_lancio(self):
        assert CdsUtils.is_lancio("Staffetta 4 X 100") is False


# ── is_salto ──────────────────────────────────────────────────────────────────

class TestIsSalto:
    def test_salto_in_alto_is_salto(self):
        assert CdsUtils.is_salto("Salto in alto") is True

    def test_salto_in_lungo_is_salto(self):
        assert CdsUtils.is_salto("Salto in lungo") is True

    def test_salto_triplo_is_salto(self):
        assert CdsUtils.is_salto("Salto triplo") is True

    def test_asta_is_salto(self):
        assert CdsUtils.is_salto("Salto con l'asta") is True

    def test_case_insensitive(self):
        assert CdsUtils.is_salto("SALTO IN ALTO") is True
        assert CdsUtils.is_salto("salto triplo") is True

    def test_corsa_is_not_salto(self):
        assert CdsUtils.is_salto("300 piani") is False
        assert CdsUtils.is_salto("1000 metri") is False

    def test_lancio_is_not_salto(self):
        assert CdsUtils.is_salto("Getto del peso Kg 4,000") is False
        assert CdsUtils.is_salto("Disco") is False

    def test_empty_string_is_not_salto(self):
        assert CdsUtils.is_salto("") is False

    def test_staffetta_is_not_salto(self):
        assert CdsUtils.is_salto("Staffetta 4 X 100") is False


# ── is_ostacoli ───────────────────────────────────────────────────────────────

class TestIsOstacoli:
    def test_ostacoli_keyword(self):
        assert CdsUtils.is_ostacoli("80 ostacoli") is True
        assert CdsUtils.is_ostacoli("100 ostacoli") is True

    def test_hs_prefix(self):
        assert CdsUtils.is_ostacoli("HS 0.914") is True
        assert CdsUtils.is_ostacoli("hs 0.762") is True

    def test_hs_suffix_with_space(self):
        assert CdsUtils.is_ostacoli("100 hs") is True
        assert CdsUtils.is_ostacoli("60 hs 0,762") is True

    def test_case_insensitive_ostacoli(self):
        assert CdsUtils.is_ostacoli("80 OSTACOLI") is True

    def test_plain_corsa_not_ostacoli(self):
        assert CdsUtils.is_ostacoli("300 piani") is False
        assert CdsUtils.is_ostacoli("1000 metri") is False

    def test_salto_is_not_ostacoli(self):
        assert CdsUtils.is_ostacoli("Salto in alto") is False

    def test_lancio_is_not_ostacoli(self):
        assert CdsUtils.is_ostacoli("Disco Kg 1,000") is False

    def test_empty_string_not_ostacoli(self):
        assert CdsUtils.is_ostacoli("") is False


# ── athlete_key ───────────────────────────────────────────────────────────────

class TestAthleteKey:
    def test_extracts_first_word_uppercase(self):
        assert CdsUtils.athlete_key("ROSSI A.") == "ROSSI"

    def test_already_uppercase(self):
        assert CdsUtils.athlete_key("BIANCHI M.") == "BIANCHI"

    def test_lowercase_converted(self):
        assert CdsUtils.athlete_key("rossi a.") == "ROSSI"

    def test_mixed_case_converted(self):
        assert CdsUtils.athlete_key("Verdi G.") == "VERDI"

    def test_single_word(self):
        assert CdsUtils.athlete_key("NERI") == "NERI"

    def test_empty_string_returns_empty(self):
        assert CdsUtils.athlete_key("") == ""

    def test_extra_spaces_handled(self):
        assert CdsUtils.athlete_key("  FERRARI  A.") == "FERRARI"


# ── staff_athlete_keys ────────────────────────────────────────────────────────

class TestStaffAthleteKeys:
    def test_fidal_format_comma_separated(self):
        raw = "ROSSI A. CF, BIANCHI M. CF, VERDI G. CF, NERI L. CF"
        keys = CdsUtils.staff_athlete_keys(raw)
        assert keys == ["ROSSI", "BIANCHI", "VERDI", "NERI"]

    def test_slash_separated(self):
        raw = "ROSSI A. / BIANCHI M. / VERDI G."
        keys = CdsUtils.staff_athlete_keys(raw)
        assert keys == ["ROSSI", "BIANCHI", "VERDI"]

    def test_two_letter_suffix_stripped(self):
        raw = "FERRARI A. CF"
        keys = CdsUtils.staff_athlete_keys(raw)
        assert keys == ["FERRARI"]

    def test_empty_string_returns_empty(self):
        assert CdsUtils.staff_athlete_keys("") == []

    def test_none_returns_empty(self):
        assert CdsUtils.staff_athlete_keys(None) == []

    def test_four_athletes(self):
        raw = "AA BB., CC DD., EE FF., GG HH."
        keys = CdsUtils.staff_athlete_keys(raw)
        assert len(keys) == 4

    def test_keys_are_uppercase(self):
        raw = "rossi a. CF, bianchi m. CF"
        keys = CdsUtils.staff_athlete_keys(raw)
        assert all(k == k.upper() for k in keys)


# ── cds_program_cf ────────────────────────────────────────────────────────────

class TestCdsProgramCf:
    def test_80_piani_in_program(self):
        assert CdsUtils.cds_program_cf("80 piani") is True

    def test_300_piani_in_program(self):
        assert CdsUtils.cds_program_cf("300 piani") is True

    def test_1000_metri_in_program(self):
        assert CdsUtils.cds_program_cf("1000 metri") is True

    def test_2000_in_program(self):
        assert CdsUtils.cds_program_cf("2000 siepi") is True

    def test_salto_in_alto_in_program(self):
        assert CdsUtils.cds_program_cf("Salto in alto") is True

    def test_salto_in_lungo_in_program(self):
        assert CdsUtils.cds_program_cf("Salto in lungo") is True

    def test_triplo_in_program(self):
        assert CdsUtils.cds_program_cf("Salto triplo") is True

    def test_asta_in_program(self):
        assert CdsUtils.cds_program_cf("Salto con l'asta") is True

    def test_peso_in_program(self):
        assert CdsUtils.cds_program_cf("Getto del peso Kg 3,000") is True

    def test_martello_in_program(self):
        assert CdsUtils.cds_program_cf("Martello Kg 3,000") is True

    def test_disco_in_program(self):
        assert CdsUtils.cds_program_cf("Disco Kg 1,000") is True

    def test_giavellotto_in_program(self):
        assert CdsUtils.cds_program_cf("Giavellotto gr 500") is True

    def test_staffetta_4x100_in_program(self):
        assert CdsUtils.cds_program_cf("Staffetta 4 X 100") is True

    def test_marcia_in_program(self):
        assert CdsUtils.cds_program_cf("Marcia Km 3") is True

    def test_100_piani_not_in_cf_program(self):
        assert CdsUtils.cds_program_cf("100 piani") is False

    def test_200_piani_not_in_cf_program(self):
        assert CdsUtils.cds_program_cf("200 piani") is False

    def test_case_insensitive(self):
        assert CdsUtils.cds_program_cf("SALTO IN ALTO") is True
        assert CdsUtils.cds_program_cf("martello kg 3,000") is True


# ── cds_program_cm ────────────────────────────────────────────────────────────

class TestCdsProgramCm:
    def test_80_piani_in_program(self):
        assert CdsUtils.cds_program_cm("80 piani") is True

    def test_100_ostacoli_in_program(self):
        assert CdsUtils.cds_program_cm("100 ostacoli") is True

    def test_300_piani_in_program(self):
        assert CdsUtils.cds_program_cm("300 piani") is True

    def test_1000_metri_in_program(self):
        assert CdsUtils.cds_program_cm("1000 metri") is True

    def test_salto_in_lungo_in_program(self):
        assert CdsUtils.cds_program_cm("Salto in lungo") is True

    def test_giavellotto_in_program(self):
        assert CdsUtils.cds_program_cm("Giavellotto gr 700") is True

    def test_staffetta_4x100_in_program(self):
        assert CdsUtils.cds_program_cm("Staffetta 4 X 100") is True

    def test_marcia_in_program(self):
        assert CdsUtils.cds_program_cm("Marcia Km 3") is True

    def test_100_piani_not_in_cm_program(self):
        assert CdsUtils.cds_program_cm("100 piani") is False

    def test_cm_and_cf_share_most_events(self):
        common = ["80 piani", "300 piani", "1000 metri", "Salto in lungo",
                  "Salto in alto", "Salto triplo", "Martello Kg 3,000", "Giavellotto gr 700"]
        for ev in common:
            assert CdsUtils.cds_program_cm(ev), f"{ev!r} dovrebbe essere nel programma CM"
            assert CdsUtils.cds_program_cf(ev), f"{ev!r} dovrebbe essere nel programma CF"


# ── cds_program_rm (shared by RF and RM) ─────────────────────────────────────

class TestCdsProgramRm:
    def test_60_piani_in_program(self):
        assert CdsUtils.cds_program_rm("60 piani") is True

    def test_1000_metri_in_program(self):
        assert CdsUtils.cds_program_rm("1000 metri") is True

    def test_marcia_in_program(self):
        assert CdsUtils.cds_program_rm("Marcia Km 2") is True

    def test_salto_in_alto_in_program(self):
        assert CdsUtils.cds_program_rm("Salto in alto") is True

    def test_salto_in_lungo_in_program(self):
        assert CdsUtils.cds_program_rm("Salto in lungo") is True

    def test_peso_2_in_program(self):
        assert CdsUtils.cds_program_rm("Getto del peso Kg 2,000") is True

    def test_vortex_in_program(self):
        assert CdsUtils.cds_program_rm("Vortex") is True

    def test_staffetta_4x100_in_program(self):
        assert CdsUtils.cds_program_rm("Staffetta 4 X 100") is True

    def test_80_piani_not_in_rf_rm_program(self):
        assert not CdsUtils.cds_program_rm("80 piani")

    def test_300_piani_not_in_rf_rm_program(self):
        assert not CdsUtils.cds_program_rm("300 piani")

    def test_disco_not_in_rf_rm_program(self):
        assert not CdsUtils.cds_program_rm("Disco Kg 1,000")


# ── get_cds_program ───────────────────────────────────────────────────────────

class TestGetCdsProgram:
    def test_cf_returns_callable(self):
        fn = CdsUtils.get_cds_program("CF")
        assert callable(fn)

    def test_cm_returns_callable(self):
        fn = CdsUtils.get_cds_program("CM")
        assert callable(fn)

    def test_rf_returns_callable(self):
        fn = CdsUtils.get_cds_program("RF")
        assert callable(fn)

    def test_rm_returns_callable(self):
        fn = CdsUtils.get_cds_program("RM")
        assert callable(fn)

    def test_rf_and_rm_use_same_filter(self):
        assert CdsUtils.get_cds_program("RF") is CdsUtils.get_cds_program("RM")

    def test_unknown_category_returns_none(self):
        assert CdsUtils.get_cds_program("XX") is None

    def test_cf_filter_accepts_cf_events(self):
        fn = CdsUtils.get_cds_program("CF")
        assert fn("80 piani") is True
        assert fn("Salto in alto") is True

    def test_rm_filter_accepts_rm_events(self):
        fn = CdsUtils.get_cds_program("RM")
        assert fn("60 piani") is True
        assert fn("Vortex") is True

    def test_rm_filter_rejects_cadetti_events(self):
        fn = CdsUtils.get_cds_program("RM")
        assert not fn("80 piani")
        assert not fn("300 piani")
