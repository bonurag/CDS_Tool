"""
Persistenza delle schede manuali (manual_entries.json).

Il file viene salvato nella stessa directory dell'eseguibile
(compatibile con la modalità PyInstaller).
"""
import os, sys, json


def _data_dir() -> str:
    """Restituisce la directory dove salvare manual_entries.json."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    # __file__ è core/cds_manual.py → salgo di un livello alla root del progetto
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


MANUAL_FILE: str = os.path.join(_data_dir(), 'manual_entries.json')


def read_manual() -> dict:
    """Carica manual_entries.json; restituisce {} in caso di assenza o errore."""
    if not os.path.exists(MANUAL_FILE):
        return {}
    try:
        with open(MANUAL_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def write_manual(data: dict) -> None:
    """Sovrascrive manual_entries.json con i dati forniti."""
    with open(MANUAL_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
