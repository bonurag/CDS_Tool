import re

class CdsUtils:
    """
    Classe di utilità per tutte le elaborazioni generiche del Campionato di Società (CdS).
    Fornisce metodi statici per la gestione di atleti, categorie e classificazioni eventi.
    """

    _OPT_LANCI = {'peso','martello','giavellotto','disco','lancio','vortex','palla'}
    _OPT_SALTI = {'lungo','triplo','alto','asta','salto'}

    @staticmethod
    def is_lancio(ev: str) -> bool:
        """
        Determina se un evento rientra nella categoria dei Lanci.
        
        :param ev: Nome dell'evento (es. 'Lancio del Peso')
        :return: True se è un lancio, False altrimenti
        """
        return any(k in ev.lower() for k in CdsUtils._OPT_LANCI)

    @staticmethod
    def is_salto(ev: str) -> bool:
        """
        Determina se un evento rientra nella categoria dei Salti.
        
        :param ev: Nome dell'evento (es. 'Salto in Lungo')
        :return: True se è un salto, False altrimenti
        """
        return any(k in ev.lower() for k in CdsUtils._OPT_SALTI)

    @staticmethod
    def is_ostacoli(ev: str) -> bool:
        """
        Determina se un evento è una corsa ad ostacoli o siepi.

        Riconosce: ``"ostac"`` (ostacoli), ``"hs"`` come parola/prefisso
        (High Steps — notazione FIDAL abbreviata), ``"siepi"`` (3000 siepi).

        :param ev: Nome dell'evento (es. ``'80 hs'``, ``'100 ostacoli'``).
        :return: True se è una corsa ad ostacoli/siepi, False altrimenti.
        """
        e = ev.lower()
        return 'ostac' in e or ' hs' in e or 'hs ' in e or e.startswith('hs')

    @staticmethod
    def athlete_key(name: str) -> str:
        """
        Estrae la chiave identificativa di un atleta dalla stringa nome FIDAL.

        FIDAL usa il formato ``"COGNOME Nome"`` (cognome in maiuscolo, nome in
        misto), quindi la prima parola corrisponde al cognome ed è sufficiente
        come chiave per il controllo del limite di partecipazione (max 2 gare
        per atleta in CF/CM, max 1 individuale in RF/RM).

        :param name: Nome completo atleta (es. ``'ROSSI Mario'``). Stringa vuota → ``''``.
        :return: Prima parola in maiuscolo (es. ``'ROSSI'``), oppure ``''``.
        """
        return name.split()[0].upper() if name else ''

    @staticmethod
    def staff_athlete_keys(raw_staff: str) -> list:
        """
        Estrae le chiavi (cognomi) degli atleti di una staffetta dalla stringa grezza FIDAL.

        La stringa FIDAL tipicamente ha il formato:
        ``"COGNOME1 N. CF, COGNOME2 M. CF / COGNOME3 L. CF"``.
        Il metodo divide per ``,`` o ``/``, rimuove il suffisso categoria a due
        lettere maiuscole (es. ``"CF"``) e applica ``athlete_key`` a ogni parte.

        :param raw_staff: Stringa composizione staffetta (può essere ``None`` o vuota).
        :return: Lista di chiavi cognome (stringhe maiuscole), senza duplicati di ordine.
        """
        keys = []
        for part in re.split(r'[,/]', raw_staff or ''):
            cleaned = re.sub(r'\s+[A-Z]{2}\s*$', '', part.strip()).strip()
            k = CdsUtils.athlete_key(cleaned)
            if k:
                keys.append(k)
        return keys

    @staticmethod
    def cds_program_cf(ev: str) -> bool:
        """Verifica se un evento rientra nel programma tecnico CdS per le Cadette (CF).

        Discipline incluse: 80 piani, 80 hs, 300 piani, 300 hs, 1000 m, 1200 m, 2000 m,
        salto in alto, salto in lungo, salto triplo, salto con l'asta, getto del peso,
        lancio del martello, lancio del disco, lancio del giavellotto,
        staffetta 4x100, marcia.

        :param ev: Nome evento (case-insensitive).
        :return: True se l'evento è nel programma CF.
        """
        e = ev.lower()
        return (('80' in e and ('piani' in e or CdsUtils.is_ostacoli(e))) or
                ('300' in e and (CdsUtils.is_ostacoli(e) or 'piani' in e)) or
                (bool(re.search(r'(?<!\d)1000(?!\d)', e)) and '3x' not in e and '3 x' not in e) or
                '2000' in e or '1200' in e or
                'asta' in e or 'in alto' in e or 'in lungo' in e or 'triplo' in e or
                'peso' in e or 'martello' in e or 'disco' in e or 'giavellott' in e or
                (re.search(r'4\s*[xX]\s*100(?!0)', ev) and 'staffetta' in e) or
                'marcia' in e)

    @staticmethod
    def cds_program_cm(ev: str) -> bool:
        """Verifica se un evento rientra nel programma tecnico CdS per i Cadetti (CM).

        Discipline incluse: 80 piani, 100 hs, 300 piani, 300 hs, 1000 m, 1200 m, 2000 m,
        salto in alto, salto in lungo, salto triplo, salto con l'asta,
        getto del peso (Kg 4), lancio del martello, lancio del disco, lancio del giavellotto,
        staffetta 4x100, marcia.

        :param ev: Nome evento (case-insensitive).
        :return: True se l'evento è nel programma CM.
        """
        e = ev.lower()
        return (('80' in e and 'piani' in e) or
                (bool(re.search(r'(?<!\d)100(?!\d)', e)) and CdsUtils.is_ostacoli(e)) or
                ('300' in e and (CdsUtils.is_ostacoli(e) or 'piani' in e)) or
                (bool(re.search(r'(?<!\d)1000(?!\d)', e)) and '3x' not in e and '3 x' not in e) or
                '2000' in e or '1200' in e or
                'asta' in e or 'in alto' in e or 'in lungo' in e or 'triplo' in e or
                ('peso' in e and '4' in e) or
                'martello' in e or 'disco' in e or 'giavellott' in e or
                (re.search(r'4\s*[xX]\s*100(?!0)', ev) and 'staffetta' in e) or
                'marcia' in e)

    @staticmethod
    def cds_program_rm(ev: str) -> bool:
        """Verifica se un evento rientra nel programma tecnico CdS per Ragazzi/e (RM/RF).

        Questo filtro è condiviso tra le categorie RM e RF (stesso programma).
        Discipline incluse: 60 piani, 60 hs, 1000 m, salto in alto, salto in lungo,
        getto del peso (Kg 2), lancio del vortex, staffetta 4x100, marcia.

        :param ev: Nome evento (case-insensitive).
        :return: True se l'evento è nel programma RM/RF.
        """
        e = ev.lower()
        return ((bool(re.search(r'(?<!\d)60(?!\d)', e)) and ('piani' in e or CdsUtils.is_ostacoli(e))) or
                (bool(re.search(r'(?<!\d)1000(?!\d)', e)) and '3x' not in e and '3 x' not in e) or
                'marcia' in e or 'in alto' in e or 'in lungo' in e or
                ('peso' in e and '2' in e) or 'vortex' in e or
                (re.search(r'4\s*[xX]\s*100(?!0)', ev) and 'staffetta' in e))

    @staticmethod
    def get_cds_program(cat: str):
        """Restituisce la funzione filtro programma CdS per la categoria specificata.

        :param cat: Sigla categoria (``'CF'``, ``'CM'``, ``'RF'``, ``'RM'``).
        :return: Callable ``(ev: str) -> bool`` da usare come predicato di filtro,
                 oppure ``None`` se la categoria non ha un programma definito
                 (in quel caso nessun evento viene escluso).
        """
        programs = {
            'CF': CdsUtils.cds_program_cf,
            'CM': CdsUtils.cds_program_cm,
            'RF': CdsUtils.cds_program_rm,
            'RM': CdsUtils.cds_program_rm,
        }
        return programs.get(cat)
