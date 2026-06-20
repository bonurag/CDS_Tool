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
        Determina se un evento è una corsa ad ostacoli.
        
        :param ev: Nome dell'evento
        :return: True se l'evento è ad ostacoli, False altrimenti
        """
        e = ev.lower()
        return 'ostac' in e or ' hs' in e or 'hs ' in e or e.startswith('hs')

    @staticmethod
    def athlete_key(name: str) -> str:
        """
        Estrae una chiave univoca per un atleta (es. cognome) per evitare che superi 
        il limite di gare partecipabili. Usiamo la prima parola del nome.
        
        :param name: Nome completo dell'atleta
        :return: Chiave normalizzata dell'atleta (es. cognome maiuscolo)
        """
        return name.split()[0].upper() if name else ''

    @staticmethod
    def staff_athlete_keys(raw_staff: str) -> list:
        """
        Estrae le chiavi (cognomi) delle atlete facenti parte di una staffetta dalla stringa grezza FIDAL.
        
        :param raw_staff: Stringa della composizione staffetta (es. 'LORINI A. CF, ROSSI B. CF')
        :return: Lista delle chiavi degli atleti della staffetta
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
        """Filtro tecnico (M/F): Cadette (CF)"""
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
        """Filtro tecnico (M/F): Cadetti (CM)"""
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
        """Filtro tecnico (M/F): Ragazzi/e (RM/RF)"""
        e = ev.lower()
        return ((bool(re.search(r'(?<!\d)60(?!\d)', e)) and ('piani' in e or CdsUtils.is_ostacoli(e))) or
                (bool(re.search(r'(?<!\d)1000(?!\d)', e)) and '3x' not in e and '3 x' not in e) or
                'marcia' in e or 'in alto' in e or 'in lungo' in e or
                ('peso' in e and '2' in e) or 'vortex' in e or
                (re.search(r'4\s*[xX]\s*100(?!0)', ev) and 'staffetta' in e))

    @staticmethod
    def get_cds_program(cat: str):
        """Restituisce il filtro programma per la categoria specificata."""
        programs = {
            'CF': CdsUtils.cds_program_cf,
            'CM': CdsUtils.cds_program_cm,
            'RF': CdsUtils.cds_program_rm,
            'RM': CdsUtils.cds_program_rm,
        }
        return programs.get(cat)
