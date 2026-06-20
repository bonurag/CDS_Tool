"""
Persistenza delle schede manuali (manual_entries.json).

Il file viene salvato nella stessa directory dell'eseguibile
(compatibile con la modalità PyInstaller).
"""
import os
import sys
import json


def _data_dir() -> str:
    """Restituisce la directory radice del progetto dove salvare i file di dati.

    Due rami:
    - **Modalità frozen (PyInstaller)**: la directory dell'eseguibile
      (``os.path.dirname(sys.executable)``), in modo che ``manual_entries.json``
      venga scritto accanto al ``.exe`` e non in una directory temporanea.
    - **Modalità normale**: due livelli sopra questo file
      (``core/cds_manual.py`` → ``core/`` → root del progetto).

    :return: Percorso assoluto della directory dove salvare ``manual_entries.json``.
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    # __file__ è core/cds_manual.py → salgo di un livello alla root del progetto
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


MANUAL_FILE: str = os.path.join(_data_dir(), 'manual_entries.json')


def read_manual() -> dict:
    """Carica il file ``manual_entries.json`` e lo restituisce come dizionario.

    Il file ha struttura ``{categoria: [entry, ...]}``, dove ogni ``entry`` è un
    dict risultato con i campi standard (``ev``, ``athlete``, ``perf``, ``pts``,
    ``savedId``, ``savedAt``, …).

    In caso di file assente, JSON malformato o qualsiasi errore di I/O, restituisce
    ``{}`` silenziosamente (comportamento sicuro: il chiamante ottiene uno stato vuoto
    invece di un'eccezione).

    :return: Dict ``{categoria: [entry]}`` oppure ``{}`` se il file non esiste o è corrotto.
    """
    if not os.path.exists(MANUAL_FILE):
        return {}
    try:
        with open(MANUAL_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def write_manual(data: dict) -> None:
    """Sovrascrive ``manual_entries.json`` con il dizionario fornito.

    La scrittura è **completa**: non esegue merge con il contenuto precedente.
    Il chiamante è responsabile di passare l'intero stato aggiornato
    (tipicamente ottenuto da ``read_manual()`` seguito dalle modifiche).

    Serializza con ``ensure_ascii=False`` (UTF-8 con caratteri Unicode nativi)
    e ``indent=2`` per leggibilità.

    :param data: Dict ``{categoria: [entry]}`` da serializzare.
    :raises OSError: Se il percorso non è scrivibile (permessi, disco pieno, ecc.).
    """
    with open(MANUAL_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
