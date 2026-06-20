#!/usr/bin/env python3
"""
FIDAL CdS Tool — Scheda Provinciale CdS (CF · CM · RF · RM)

Server Flask che espone le API di scraping, ottimizzazione e persistenza e
serve il frontend tramite template Jinja2 (templates/index.html) e file statici
(static/css/style.css, static/js/app.js).

Avvio:
    python fidal_cds_tool.py [porta]   (default: 5001)

Endpoint principali:
    GET  /                              Frontend web
    GET  /api/fetch                     Scarica graduatorie da FIDAL
    POST /api/ottimizza                 Calcola scheda ottimale (DFS B&B)
    GET  /api/proiezione                Proiezione regionale (cached)
    GET  /api/proiezione/build          Build proiezione SSE (tutte le società)
    GET  /api/tabelle                   Tabelle punteggi JSON
    GET  /api/discipline_list           Lista discipline per categoria
    GET  /api/manual                    Leggi manual entries
    POST /api/manual                    Salva un manual entry
    DEL  /api/manual/<id>               Elimina un manual entry
    GET  /api/manual/template_csv       Scarica template CSV
    POST /api/manual/import_csv         Importa file CSV
    POST /api/reoptimize_soc            Ricalcola ottimale singola società
    GET  /api/societa                   Lista società di una regione
    GET  /api/fidal_status              Health-check raggiungibilità FIDAL
"""
from flask import Flask, jsonify, request, Response, stream_with_context, render_template
import requests
from bs4 import BeautifulSoup
import re
import sys
import threading
import time
import webbrowser
import json
import os
from core.cds_utils import CdsUtils
from core.cds_optimizer import CdsOptimizer
from core.cds_manual import read_manual, write_manual, _data_dir

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# In modalità PyInstaller i template e gli static vengono estratti in _MEIPASS
_base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
app = Flask(
    __name__,
    template_folder=os.path.join(_base_dir, 'templates'),
    static_folder=os.path.join(_base_dir, 'static'),
)

# ── TABELLE PUNTEGGI ─────────────────────────────────────────────────────────

def _load_tabella(filename):
    """Carica un file JSON delle tabelle punteggi e lo converte in un dict annidato.

    Gestisce sia l'esecuzione normale sia la modalità frozen (PyInstaller), dove i
    file dati vengono estratti in ``sys._MEIPASS`` invece che accanto al sorgente.

    :param filename: Nome del file JSON nella cartella ``data/`` (es. ``'Cadette.json'``).
    :return: ``{nome_gara: {prestazione_str: punti_int}}`` oppure ``{}`` se il file
             non esiste o il JSON è malformato.
    """
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, 'data', filename)
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    return {g['gara']: {r['tempo'].strip(): r['punteggio']
                        for r in g.get('risultati', [])}
            for g in data.get('gare', [])}

_TABELLE = {
    'CF': _load_tabella('Cadette.json'),
    'CM': _load_tabella('Cadetti.json'),
    'RF': _load_tabella('Ragazze.json'),
    'RM': _load_tabella('Ragazzi.json'),
}

def _match_gara(fidal_name, tabella):
    """Ricerca fuzzy del nome gara FIDAL nella tabella punteggi locale.

    Applica quattro strategie di match in ordine di precisione decrescente:
    1. Corrispondenza esatta (case-sensitive poi case-insensitive).
    2. Rimozione del suffisso internazionale dopo ``/`` (es. ``"Salto in lungo/LJ"``).
    3. Match numerico + tipo gara (``"piani"``, ``"hs"``, distanze medie).
    4. Match per keyword di salti e lanci (``"lungo"``, ``"peso"``, ``"martello"``…).

    :param fidal_name: Nome evento come restituito dallo scraper FIDAL.
    :param tabella: Dict ``{nome_canonico: {perf: pts}}`` caricato da ``_TABELLE``.
    :return: Chiave canonica della tabella corrispondente, oppure ``None`` se
             nessuna strategia trova una corrispondenza.
    """
    fn = fidal_name.strip()
    fn_low = fn.lower()

    # 1 — Corrispondenza diretta
    if fn in tabella:
        return fn
    for k in tabella:
        if k.lower() == fn_low:
            return k

    # 2 — Rimuovi suffisso dopo '/' (es. "Salto in lungo/LJ" → "Salto in lungo")
    fn_strip = re.sub(r'\s*/\S+.*', '', fn).strip()
    if fn_strip in tabella:
        return fn_strip
    for k in tabella:
        if k.lower() == fn_strip.lower():
            return k

    # 3 — Numero iniziale + tipo gara
    nums = re.findall(r'\d+', fn_low)
    n0 = nums[0] if nums else None
    for k in tabella:
        kl = k.lower()
        knums = re.findall(r'\d+', kl)
        kn0 = knums[0] if knums else None
        if n0 and kn0 and n0 != kn0:
            continue
        if 'piani' in fn_low and 'piani' in kl:
            return k
        if n0 in ('600', '1000', '1200', '2000') and 'metr' in kl and 'ostac' not in kl:
            return k
        if ('hs' in fn_low or 'ostac' in fn_low) and 'ostac' in kl:
            return k
        if 'staffetta' in fn_low and 'staffetta' in kl:
            if nums and knums and nums[-1] == knums[-1]:
                return k

    # 4 — Keyword per salti e lanci
    kw_map = [
        (['lungo'],                  'salto in lungo'),
        (['triplo'],                 'salto triplo'),
        (['quadruplo'],              'salto quadruplo'),
        (['alto'],                   'salto in alto'),
        (['asta'],                   "salto con l'asta"),
        (['peso', ' sp'],            'peso'),
        (['martello', ' ht'],        'martello'),
        (['giavellotto', ' jt'],     'giavellotto'),
        (['disco'],                  'disco'),
        (['marcia'],                 'marcia'),
    ]
    for fkws, jkw in kw_map:
        if any(kw in fn_low for kw in fkws):
            for k in tabella:
                if jkw in k.lower():
                    return k
    return None

def _parse_perf_s(perf):
    """Converte una stringa di prestazione in un valore float numerico.

    Formati supportati:
    - Numero semplice: ``"42.10"`` o ``"13,45"`` (virgola → punto)
    - Minuti:secondi: ``"1:30.00"`` → 90.0
    - Ore:minuti:secondi: ``"1:00:00"`` → 3600.0

    Per le corse il valore è in **secondi**; per salti e lanci è in **metri**.

    :param perf: Stringa di prestazione (viene strip-pata prima dell'analisi).
    :return: Valore float oppure ``None`` se il formato non è riconoscibile.
    """
    perf = perf.strip()
    if ':' in perf:
        parts = perf.split(':')
        try:
            if len(parts) == 2:
                return float(parts[0]) * 60 + float(parts[1])
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        except ValueError:
            return None
    try:
        return float(perf.replace(',', '.'))
    except ValueError:
        return None

def _lookup_pts(fidal_name, perf, categoria):
    """Cerca il punteggio FIDAL per una prestazione nella tabella della categoria.

    Strategia a due fasi:
    1. **Match esatto** sulla stringa di prestazione (es. ``"42.10"``).
    2. **Fallback numerico**: converte la prestazione in float e trova il bucket
       immediatamente peggiore nella tabella (eccesso per corse/ostacoli,
       difetto per salti/lanci) — metodo ufficiale FIDAL.

    La direzione corsa/campo viene rilevata automaticamente confrontando i
    punteggi degli estremi della tabella (min_valore ↔ max_punti → corsa).

    :param fidal_name: Nome evento come da scraper FIDAL (viene passato a ``_match_gara``).
    :param perf: Prestazione in formato stringa (es. ``"42.10"``, ``"1:52.30"``).
    :param categoria: Sigla categoria (``"CF"``, ``"CM"``, ``"RF"``, ``"RM"``).
    :return: Tupla ``(punti: int, trovato: bool)``.
             ``trovato=False`` se la categoria o la gara non esistono in tabella,
             oppure se la stringa di prestazione non è parsabile.
             ``trovato=True`` con ``punti=0`` se la prestazione è fuori range tabella.
    """
    tabella = _TABELLE.get(categoria, {})
    if not tabella:
        return 0, False
    gara_key = _match_gara(fidal_name, tabella)
    if gara_key is None:
        return 0, False
    perf_dict = tabella[gara_key]
    perf_norm = perf.strip()

    # 1 — Corrispondenza esatta
    if perf_norm in perf_dict:
        return perf_dict[perf_norm], True

    # 2 — Fallback numerico: prossima entrata peggiore in tabella
    perf_val = _parse_perf_s(perf_norm)
    if perf_val is None:
        return 0, False

    # Costruisci lista (valore_numerico, punteggio) per tutte le entrate valide
    numeric = [(v, p) for t, p in perf_dict.items()
               if (v := _parse_perf_s(t)) is not None]
    if not numeric:
        return 0, False

    # Determina direzione: in eventi di corsa valore più basso = punti più alti
    # (campione: confronta min e max)
    sorted_n = sorted(numeric, key=lambda x: x[0])
    is_time = sorted_n[0][1] >= sorted_n[-1][1]  # True → corsa/ostacoli

    if is_time:
        # Prossima entrata con tempo >= prestazione (bucket peggiore per eccesso)
        above = [(v, p) for v, p in numeric if v >= perf_val]
        if above:
            return min(above, key=lambda x: x[0])[1], True
    else:
        # Prossima entrata con misura <= prestazione (bucket peggiore per difetto)
        below = [(v, p) for v, p in numeric if v <= perf_val]
        if below:
            return max(below, key=lambda x: x[0])[1], True

    # Prestazione fuori range tabella → 0 pt confermato (evento trovato, perf troppo lenta/corta)
    return 0, True

# ── SCRAPER ──────────────────────────────────────────────────────────────────

def classify_event(nome):
    """Classifica il tipo di evento atletico da una stringa di nome.

    Restituisce una delle cinque categorie usate dal frontend e dall'ottimizzatore:
    ``'staffetta'``, ``'ostacoli'``, ``'salto'``, ``'lancio'``, ``'corsa'``.
    La classificazione è case-insensitive e basata su regex e keyword.

    :param nome: Nome evento (es. ``"80 ostacoli"``, ``"Salto in lungo"``, ``"4x100"``).
    :return: Stringa tipo evento: ``'staffetta'`` | ``'ostacoli'`` | ``'salto'`` |
             ``'lancio'`` | ``'corsa'`` (default se nessuna keyword corrisponde).
    """
    n = nome.lower()
    if re.search(r'staffetta|[34]x\d+|\dx\d', n): return 'staffetta'
    if re.search(r'\bhs\b|ostacoli|siepi', n):     return 'ostacoli'
    if re.search(r'lungo|triplo|alto|asta|salto',n): return 'salto'
    if re.search(r'peso|martello|giavellotto|disco|lancio|vortex|palla', n): return 'lancio'
    return 'corsa'

def _expand_year(raw: str) -> str:
    """Converte l'anno FIDAL da 2 cifre a 4 cifre (es. '12' → '2012')."""
    s = raw.strip()
    if s.isdigit() and len(s) <= 2:
        y = int(s)
        return str(2000 + y if y <= 99 else 1900 + y)
    return s


def parse_graduatorie(html):
    """Estrae i risultati atletici dall'HTML delle graduatorie FIDAL.

    Naviga la struttura HTML FIDAL: ogni blocco gara è una ``<table class="graduatorie">``
    con una cella intestazione (sfondo ``#5ea2e7``) seguita da una
    ``<table class="tabella">`` con le righe dei risultati.

    Per ogni riga valida (≥ 6 colonne, prestazione non vuota) costruisce un dict
    con i campi:
    ``id``, ``ev``, ``type``, ``athlete``, ``athlete_url``, ``perf``, ``wind``,
    ``piazz``, ``citta``, ``data``, ``anno``, ``pts`` (0), ``est`` (True),
    ``isStaffetta``, ``rawStaff``.

    I punti vengono calcolati separatamente da ``_lookup_pts`` dopo il parsing.

    :param html: Testo HTML della risposta FIDAL (POST a ``graduatorie.php``).
    :return: Lista di dict risultati (può essere vuota se la pagina non contiene graduatorie).
    """
    soup = BeautifulSoup(html, 'html.parser')
    results, rid = [], 0
    for header in soup.find_all('table', class_='graduatorie'):
        gara_td = header.find('td', style=lambda s: s and '5ea2e7' in s)
        if not gara_td: continue
        nome = gara_td.get_text(strip=True)
        tipo = classify_event(nome)
        dt   = header.find_next('table', class_='tabella')
        if not dt: continue
        for row in dt.find_all('tr'):
            cols = row.find_all('td')
            if len(cols) < 6: continue
            perf = cols[0].get_text(strip=True)
            if not perf: continue
            abbr     = cols[3].find('abbr')
            athl_a   = cols[2].find('a')
            data_a   = cols[7].find('a') if len(cols) > 7 else None
            results.append({
                'id':           rid,
                'ev':           nome,
                'type':         tipo,
                'athlete':      cols[2].get_text(strip=True),
                'athlete_url':  athl_a['href'] if athl_a else '',
                'perf':         perf,
                'wind':         cols[1].get_text(strip=True),
                'piazz':        cols[5].get_text(strip=True),
                'citta':        cols[6].get_text(strip=True) if len(cols) > 6 else '',
                'data':         (data_a or cols[7]).get_text(strip=True) if len(cols) > 7 else '',
                'anno':         _expand_year(abbr.get_text(strip=True)) if abbr else '',
                'pts':          0,
                'est':          True,
                'isStaffetta':  tipo == 'staffetta',
                'rawStaff':     cols[2].get_text(strip=True) if tipo == 'staffetta' else '',
            })
            rid += 1
    return results

# ── API ──────────────────────────────────────────────────────────────────────

_FIDAL_HDRS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/124.0.0.0 Safari/537.36'),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'it-IT,it;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Referer': 'https://www.fidal.it/graduatorie.php',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

def _do_fidal_fetch(p):
    """Fetches graduatorie from FIDAL, parses and enriches with local score tables.
    Returns list of result dicts. Raises ValueError/requests.HTTPError on failure."""
    fp = dict(p)
    fp.update({'gara':'0','tipologia_estrazione':'2','submit':'Invia'})
    sess = requests.Session()
    sess.headers.update(_FIDAL_HDRS)
    try:
        sess.get('https://www.fidal.it/graduatorie.php', timeout=8)
    except Exception:
        pass
    r = sess.post('https://www.fidal.it/graduatorie.php', data=fp, timeout=20)
    r.raise_for_status()
    data = parse_graduatorie(r.text)
    if not data:
        snippet = r.text[:500].replace('\n', ' ')
        raise ValueError(f'Nessun risultato trovato. '
                         f'(HTML: {len(r.text)} byte, snippet: {snippet[:120]}…)')
    categoria = fp.get('categoria', '')
    _normalize_events(data, categoria)
    for row in data:
        pts, found = _lookup_pts(row['ev'], row['perf'], categoria)
        row['pts']    = pts
        row['pts_ok'] = found
    return data, r.url

@app.route('/api/fetch')
def api_fetch():
    """GET /api/fetch — Scarica e restituisce le graduatorie FIDAL per una società.

    Query parameters (tutti opzionali, con default):
        anno         (str)  Anno sportivo (default: ``'2026'``)
        tipo_attivita(str)  ``'P'`` outdoor, ``'I'`` indoor (default: ``'P'``)
        sesso        (str)  ``'F'`` o ``'M'`` (default: ``'F'``)
        categoria    (str)  CF · CM · RF · RM (default: ``'CF'``)
        vento        (str)  Filtro vento (default: ``'2'``)
        regione      (str)  Sigla regione FIDAL (default: ``'LOM'``)
        nazionalita  (str)  ``'0'`` = tutti (default: ``'0'``)
        limite       (str)  Max risultati per gara (default: ``'100'``)
        societa      (str)  Codice società FIDAL (es. ``'BS318'``)

    Response JSON:
        ``{ok: true, data: [...], url: str}`` oppure ``{ok: false, error: str}``
    """
    try:
        p = {k: request.args.get(k, d) for k, d in [
            ('anno','2026'),('tipo_attivita','P'),('sesso','F'),
            ('categoria','CF'),('vento','2'),('regione','LOM'),
            ('nazionalita','0'),('limite','100'),('societa',''),
        ]}
        data, url = _do_fidal_fetch(p)
        return jsonify({'ok':True,'data':data,'url':url})
    except Exception as e:
        return jsonify({'ok':False,'error':str(e)}), 500

def _proiezione_cache_path(anno, tipo, sesso, cat, reg):
    """Restituisce il percorso assoluto del file cache JSON della proiezione regionale.

    Il file viene scritto nella stessa directory di ``manual_entries.json`` (``_data_dir()``).
    Nome pattern: ``proiezione_<anno>_<tipo>_<sesso>_<cat>_<reg>.json``.

    :return: Percorso assoluto (stringa) del file cache.
    """
    fname = f'proiezione_{anno}_{tipo}_{sesso}_{cat}_{reg}.json'
    return os.path.join(_data_dir(), fname)

def _normalize_events(data, categoria):
    """Normalizza i nomi evento alla forma canonica della tabella punteggi.
    Senza questo, società diverse riportano lo stesso evento con nomi differenti
    (es. '80 piani' / '80 Piani' / '80m piani') → l'ottimizzatore vede N*2 eventi
    invece di N e la complessità combinatoria esplode."""
    tabella = _TABELLE.get(categoria, {})
    if not tabella:
        return
    for row in data:
        key = _match_gara(row['ev'], tabella)
        if key:
            row['ev'] = key

_COMPETE_THRESH = {
    'RF': (6, 1, 1),
    'RM': (6, 1, 1),
}

def _can_compete_cat(n_ev, n_la, n_sa, cat):
    """Verifica se una società soddisfa i requisiti minimi per competere nella categoria.

    Le soglie per RF/RM sono: ≥ 6 gare, ≥ 1 lancio, ≥ 1 salto.
    Per CF/CM (default): ≥ 10 gare, ≥ 2 lanci, ≥ 2 salti.

    :param n_ev: Numero di gare diverse presenti nei risultati.
    :param n_la: Numero di gare di lancio diverse.
    :param n_sa: Numero di gare di salto diverse.
    :param cat: Sigla categoria (``'CF'``, ``'CM'``, ``'RF'``, ``'RM'``).
    :return: ``True`` se la società può partecipare con una scheda valida.
    """
    min_ev, min_la, min_sa = _COMPETE_THRESH.get(cat, (10, 2, 2))
    return n_ev >= min_ev and n_la >= min_la and n_sa >= min_sa


def _soc_meta(results, cat=None):
    """Calcola le statistiche aggregate di una società a partire dai suoi risultati.

    Se ``cat`` è specificato, filtra prima i risultati con il preset programma CdS
    della categoria (``CdsUtils.get_cds_program``).

    :param results: Lista di dict risultato (output di ``_do_fidal_fetch`` o cache).
    :param cat: Sigla categoria opzionale per il filtro programma CdS.
    :return: Dict con le chiavi:
        - ``num_gare`` (int) — gare distinte presenti
        - ``total_pts`` (int) — somma punti di tutti i risultati
        - ``pts_corsa`` (int) — punti da corse e ostacoli
        - ``pts_lanci`` (int) — punti da lanci
        - ``pts_salti`` (int) — punti da salti
        - ``pts_staffette`` (int) — punti da staffette
        - ``n_lanci`` (int) — gare di lancio diverse
        - ``n_salti`` (int) — gare di salto diverse
        - ``can_compete`` (bool) — True se rispetta le soglie minime di ``_can_compete_cat``
    """
    _lanci_kw = {'peso','martello','giavellotto','disco','lancio','vortex','palla'}
    _salti_kw = {'lungo','triplo','alto','asta','salto'}
    cds_prog = CdsUtils.get_cds_program(cat) if cat else None
    if cds_prog:
        results = [r for r in results if cds_prog(r.get('ev', ''))]
    evs = {r['ev'] for r in results}
    total = pts_corsa = pts_lanci = pts_salti = pts_staff = 0
    for r in results:
        p = r.get('pts') or 0
        total += p
        t = r.get('type', 'corsa')
        if t in ('corsa', 'ostacoli'): pts_corsa += p
        elif t == 'lancio':            pts_lanci += p
        elif t == 'salto':             pts_salti += p
        elif t == 'staffetta':         pts_staff  += p
    ev_low = [e.lower() for e in evs]
    n_la = sum(1 for e in ev_low if any(k in e for k in _lanci_kw))
    n_sa = sum(1 for e in ev_low if any(k in e for k in _salti_kw))
    n_ev = len(evs)
    return {
        'num_gare':      n_ev,
        'total_pts':     total,
        'pts_corsa':     pts_corsa,
        'pts_lanci':     pts_lanci,
        'pts_salti':     pts_salti,
        'pts_staffette': pts_staff,
        'n_lanci':       n_la,
        'n_salti':       n_sa,
        'can_compete':   _can_compete_cat(n_ev, n_la, n_sa, cat),
    }

# ── OTTIMIZZATORE PYTHON (per build server-side) ─────────────────────────────


# Le funzioni di calcolo, constraint e ottimizzazione sono state modularizzate
# e si trovano ora nelle classi CdsUtils (cds_utils.py) e CdsOptimizer (cds_optimizer.py)

@app.route('/api/proiezione')
def api_proiezione():
    """GET /api/proiezione — Proiezione regionale con cache su file.

    Scarica i risultati aggregati (top-10 per gara) per una regione e li
    salva in un file JSON locale per evitare re-fetch a ogni visita.

    Query parameters: anno, tipo_attivita, sesso, categoria, regione,
                      nazionalita, vento, force (``'1'`` forza il re-fetch).

    Response JSON:
        ``{ok, from_cache, data, updated_at, societies_meta}`` con cache hit,
        oppure ``{ok, from_cache, data, updated_at}`` senza cache.
        In caso di errore: ``{ok: false, error: str}``.
    """
    anno  = request.args.get('anno',  '2026')
    tipo  = request.args.get('tipo_attivita', 'P')
    sesso = request.args.get('sesso', 'F')
    cat   = request.args.get('categoria', 'CF')
    reg   = request.args.get('regione', 'LOM')
    naz   = request.args.get('nazionalita', '0')
    vento = request.args.get('vento', '2')
    force = request.args.get('force', '0') == '1'

    cache_path = _proiezione_cache_path(anno, tipo, sesso, cat, reg)

    if not force and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding='utf-8') as f:
                cached = json.load(f)
            _normalize_events(cached['data'], cat)   # fix retroattivo su cache esistenti
            # Restituisce la meta senza il campo 'data' per non gonfiare il payload.
            # Ricalcola can_compete con i criteri corretti per la categoria (fix retroattivo).
            meta_clean = {}
            for cod, m in cached.get('societies_meta', {}).items():
                entry = {k: v for k, v in m.items() if k != 'data'}
                entry['can_compete'] = _can_compete_cat(
                    m.get('num_gare', 0), m.get('n_lanci', 0), m.get('n_salti', 0), cat)
                meta_clean[cod] = entry
            return jsonify({'ok': True, 'from_cache': True,
                            'data': cached['data'], 'updated_at': cached['updated_at'],
                            'societies_meta': meta_clean})
        except Exception:
            pass  # cache corrotta → rifetch

    try:
        p = {'anno':anno,'tipo_attivita':tipo,'sesso':sesso,'categoria':cat,
             'regione':reg,'nazionalita':naz,'vento':vento,'limite':'10','societa':''}
        data, _ = _do_fidal_fetch(p)
        updated_at = time.strftime('%Y-%m-%dT%H:%M:%S')
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump({'data': data, 'updated_at': updated_at}, f,
                      ensure_ascii=False, indent=2)
        return jsonify({'ok': True, 'from_cache': False,
                        'data': data, 'updated_at': updated_at})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@app.route('/api/ottimizza', methods=['POST'])
def api_ottimizza():
    """POST /api/ottimizza — Calcola la scheda ottimale per una società.

    Esegue l'ottimizzazione DFS Branch & Bound via ``CdsOptimizer.compute_optimal``.
    Calcola sia la baseline (senza staffette) sia l'ottimale con ogni staffetta
    eleggibile, restituendo il punteggio migliore.

    Request JSON:
        ``{categoria: str, data: [risultati]}``
        I risultati devono avere i campi ``ev``, ``pts``, ``pts_ok``, ``isStaffetta``.

    Response JSON:
        ``{ok, optimal, baseline_score, staff_scores}``
        - ``optimal`` — dict con ``score``, ``ids``, ``sel``, ``updated_at``
        - ``baseline_score`` — int, punteggio senza staffette
        - ``staff_scores`` — ``{staff_id: score}`` per ogni staffetta testata
    """
    try:
        payload = request.get_json()
        results = list(payload.get('data', []))
        cat     = payload.get('categoria', 'CF')

        _normalize_events(results, cat)

        ind_results    = [r for r in results if not r.get('isStaffetta')]
        staff_eligible = [r for r in results if r.get('isStaffetta') and r.get('pts_ok')]

        # Baseline (nessuna staffetta) — sempre calcolato per l'analisi UI
        baseline = CdsOptimizer.compute_optimal(ind_results, cat)
        baseline_score = baseline['score'] if baseline else 0

        # Per ogni staffetta eleggibile: ottimale esatto con quella staffetta
        staff_scores = {}
        best_score = baseline_score
        opt = baseline
        for staff in staff_eligible:
            opt_s = CdsOptimizer.compute_optimal(ind_results + [staff], cat)
            if opt_s:
                sid = str(staff.get('id', ''))
                staff_scores[sid] = opt_s['score']
                if opt_s['score'] > best_score:
                    best_score = opt_s['score']
                    opt = opt_s

        if opt:
            opt.pop('combo_scores', None)

        return jsonify({
            'ok': True,
            'optimal': opt,
            'baseline_score': baseline_score,
            'staff_scores': staff_scores,
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/')
def index():
    """GET / — Serve il frontend tramite template Jinja2 (templates/index.html)."""
    return render_template('index.html')

@app.route('/.well-known/appspecific/com.chrome.devtools.json')
def chrome_devtools():
    """GET /.well-known/... — Risposta vuota per sopprimere i warning DevTools di Chrome."""
    return jsonify({})

@app.route('/api/fidal_status')
def api_fidal_status():
    """Verifica raggiungibilità server FIDAL. Chiamata dal frontend come health-check."""
    import urllib.request
    import time
    try:
        t0 = time.time()
        req = urllib.request.Request(
            'https://www.fidal.it/graduatorie.php',
            method='HEAD',
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            latency_ms = int((time.time() - t0) * 1000)
            return jsonify({'ok': True, 'latency_ms': latency_ms, 'http': resp.status})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:120]})

# ── MANUAL ENTRIES PERSISTENCE ────────────────────────────────────────────────
# Funzioni e costante importate da core.cds_manual:
#   read_manual(), write_manual(data), MANUAL_FILE

# Alias privati per compatibilità con il codice esistente
_read_manual  = read_manual
_write_manual = write_manual

@app.route('/api/manual', methods=['GET'])
def api_manual_get():
    """GET /api/manual?categoria=CF — Restituisce i manual entries per la categoria.

    :query categoria: Sigla categoria (CF · CM · RF · RM). Se assente restituisce [].
    :return: ``{ok: true, data: [entries]}``
    """
    categoria = request.args.get('categoria', '')
    data = _read_manual()
    return jsonify({'ok': True, 'data': data.get(categoria, [])})

@app.route('/api/manual', methods=['POST'])
def api_manual_save():
    """POST /api/manual — Salva un nuovo manual entry nel file JSON persistente.

    Request JSON: dict entry con almeno il campo ``categoria``.
    Aggiunge automaticamente ``savedId`` (``<cat>_<ns_timestamp>``) e ``savedAt``.

    :return: ``{ok: true, savedId: str}`` oppure ``{ok: false, error: str}``.
    """
    try:
        entry = request.get_json(force=True)
        categoria = entry.get('categoria', '')
        if not categoria:
            return jsonify({'ok': False, 'error': 'Categoria mancante'})
        saved_id = f"{categoria}_{time.time_ns()}"
        entry['savedId'] = saved_id
        entry['savedAt'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        data = _read_manual()
        data.setdefault(categoria, []).append(entry)
        _write_manual(data)
        return jsonify({'ok': True, 'savedId': saved_id})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/api/manual/<saved_id>', methods=['DELETE'])
def api_manual_delete(saved_id):
    """DELETE /api/manual/<saved_id> — Rimuove un manual entry da tutte le categorie.

    :param saved_id: Valore del campo ``savedId`` dell'entry da eliminare.
    :return: ``{ok: true}`` oppure ``{ok: false, error: str}``.
    """
    try:
        data = _read_manual()
        for cat in list(data.keys()):
            data[cat] = [e for e in data[cat] if e.get('savedId') != saved_id]
        _write_manual(data)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


_CSV_COLUMNS_REQUIRED = ['categoria', 'gara', 'tipo', 'prestazione', 'atleta']
_CSV_COLUMNS_OPTIONAL = ['punti', 'vento', 'piazzamento', 'citta', 'data']
_CSV_VALID_CATEGORIE = {'CF', 'CM', 'RF', 'RM'}
_CSV_VALID_TIPI = {'corsa', 'ostacoli', 'salto', 'lancio', 'staffetta'}

def _gare_valide(categoria):
    """Restituisce la lista ordinata dei nomi canonici per la categoria."""
    return sorted(_TABELLE.get(categoria, {}).keys())

def _find_gara_canonica(gara_input, categoria):
    """Match case-insensitive del nome gara; restituisce il nome canonico o None."""
    needle = gara_input.strip().lower()
    for nome in _TABELLE.get(categoria, {}):
        if nome.lower() == needle:
            return nome
    return None

_CSV_TEMPLATE_ROWS = [
    ','.join(_CSV_COLUMNS_REQUIRED + _CSV_COLUMNS_OPTIONAL),
    'CF,Staffetta 4 X 100,staffetta,56.42,"ROSSI L. / BIANCHI M. / VERDI G. / NERI A.",638,,1,Brescia,18/04/2026',
    'CM,"Getto del peso Kg 4,000",lancio,13.45,FERRARI A.,720,,2,Milano,10/05/2026',
    'CF,300 piani,corsa,42.10,CONTI B.,,,,Bergamo,15/05/2026',
]

@app.route('/api/discipline_list', methods=['GET'])
def api_discipline_list():
    """GET /api/discipline_list — Lista discipline valide per ogni categoria.

    :return: ``{CF: [...], CM: [...], RF: [...], RM: [...]}`` con i nomi canonici
             ordinati alfabeticamente, estratti dalle tabelle punteggi JSON.
    """
    return jsonify({cat: _gare_valide(cat) for cat in _CSV_VALID_CATEGORIE})

@app.route('/api/tabelle', methods=['GET'])
def api_tabelle():
    """Restituisce le tabelle punteggi per categoria.
    ?categoria=CF  → solo CF
    (nessun param) → tutte le categorie
    """
    cat = request.args.get('categoria', '').upper()
    if cat:
        if cat not in _TABELLE:
            return jsonify({'ok': False, 'error': f'Categoria {cat} non trovata'}), 404
        data = {cat: _TABELLE[cat]}
    else:
        data = _TABELLE
    from flask import make_response
    resp = make_response(jsonify({'ok': True, 'tabelle': data}))
    resp.headers['Cache-Control'] = 'public, max-age=3600'
    return resp

@app.route('/api/manual/template_csv', methods=['GET'])
def api_manual_template_csv():
    """GET /api/manual/template_csv — Scarica un file CSV di esempio precompilato.

    Il file contiene l'intestazione con tutte le colonne (obbligatorie + opzionali)
    e tre righe di esempio (staffetta CF, lancio CM, corsa CF).

    :return: Risposta CSV con Content-Disposition attachment.
    """
    content = '\r\n'.join(_CSV_TEMPLATE_ROWS) + '\r\n'
    from flask import Response
    return Response(
        content,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename="template_importazione_cds.csv"'}
    )

@app.route('/api/manual/import_csv', methods=['POST'])
def api_manual_import_csv():
    """POST /api/manual/import_csv — Importa risultati da un file CSV multipart.

    Accetta un file CSV (campo ``file`` nel form) con codifica UTF-8, UTF-8-BOM o Latin-1.
    Colonne obbligatorie: ``categoria``, ``gara``, ``tipo``, ``prestazione``, ``atleta``.
    Colonne opzionali: ``punti``, ``vento``, ``piazzamento``, ``citta``, ``data``.

    Per ogni riga:
    - Valida categoria (CF · CM · RF · RM), tipo (corsa · ostacoli · salto · lancio · staffetta).
    - Verifica che ``gara`` corrisponda a una disciplina canonica della categoria
      (case-insensitive); se non corrisponde la riga viene rifiutata con messaggio esplicito.
    - Se ``punti`` è vuoto, esegue il lookup automatico via ``_lookup_pts``.
    - Normalizza il nome staffetta in ``"COGNOME1 / COGNOME2 / ..."``.

    Le righe valide vengono scritte in ``manual_entries.json``; le righe errate
    vengono raccolte in ``errors`` ma non bloccano l'importazione delle righe valide.

    :return: ``{ok: true, imported: [entries], errors: [{riga, errori, anteprima}]}``
             oppure ``{ok: false, errors: [...], imported: []}`` se nessuna riga è valida,
             oppure ``{ok: false, error: str}`` per errori di sistema.
    """
    import csv
    import io
    try:
        if 'file' not in request.files:
            return jsonify({'ok': False, 'error': 'Nessun file ricevuto'})
        f = request.files['file']
        raw = f.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(raw))
        fieldnames = [c.strip().lower() for c in (reader.fieldnames or [])]

        missing_cols = [c for c in _CSV_COLUMNS_REQUIRED if c not in fieldnames]
        if missing_cols:
            return jsonify({'ok': False, 'error': f'Colonne obbligatorie mancanti: {", ".join(missing_cols)}'})

        saved_data = _read_manual()
        errors = []
        imported = []

        for row_idx, row in enumerate(reader, start=2):
            row_norm = {k.strip().lower(): (v or '').strip() for k, v in row.items()}
            row_errors = []

            categoria = row_norm.get('categoria', '').upper()
            gara      = row_norm.get('gara', '')
            tipo      = row_norm.get('tipo', '').lower()
            perf      = row_norm.get('prestazione', '')
            atleta    = row_norm.get('atleta', '')
            punti_raw = row_norm.get('punti', '')
            vento     = row_norm.get('vento', '')
            piazz     = row_norm.get('piazzamento', '')
            citta     = row_norm.get('citta', '')
            data_val  = row_norm.get('data', '')

            if not categoria:
                row_errors.append('categoria mancante')
            elif categoria not in _CSV_VALID_CATEGORIE:
                row_errors.append(f'categoria "{categoria}" non valida (ammessi: CF, CM, RF, RM)')

            gara_canonica = None
            if not gara:
                row_errors.append('gara mancante')
            elif categoria in _CSV_VALID_CATEGORIE:
                gara_canonica = _find_gara_canonica(gara, categoria)
                if gara_canonica is None:
                    valide = ', '.join(_gare_valide(categoria))
                    row_errors.append(
                        f'gara "{gara}" non riconosciuta per {categoria}. '
                        f'Valori ammessi: {valide}'
                    )
                else:
                    gara = gara_canonica  # normalizza al nome canonico

            if not tipo:
                row_errors.append('tipo mancante')
            elif tipo not in _CSV_VALID_TIPI:
                row_errors.append(f'tipo "{tipo}" non valido (ammessi: {", ".join(sorted(_CSV_VALID_TIPI))})')

            if not perf:
                row_errors.append('prestazione mancante')

            if not atleta:
                row_errors.append('atleta mancante')

            pts_num = 0
            pts_ok  = False
            if punti_raw:
                try:
                    pts_num = int(punti_raw)
                    if pts_num < 0:
                        row_errors.append('punti deve essere >= 0')
                    else:
                        pts_ok = True
                except ValueError:
                    row_errors.append(f'punti "{punti_raw}" non è un numero intero')
            elif perf and gara_canonica and categoria:
                pts_num, pts_ok = _lookup_pts(gara_canonica, perf, categoria)

            if row_errors:
                errors.append({'riga': row_idx, 'errori': row_errors,
                               'anteprima': f'{gara} / {atleta} / {perf}'})
                continue

            is_staff = (tipo == 'staffetta')
            staff_list = [s.strip() for s in atleta.replace('/', ',').split(',') if s.strip()] if is_staff else None
            athlete_display = (' / '.join(staff_list)) if is_staff else atleta

            saved_id = f"{categoria}_{int(time.time()*1000)}_{row_idx}"
            entry = {
                'categoria': categoria,
                'ev': gara,
                'type': tipo,
                'athlete': athlete_display,
                'athlete_url': '',
                'perf': perf,
                'wind': vento,
                'piazz': piazz,
                'citta': citta,
                'data': data_val,
                'anno': '',
                'pts': pts_num,
                'pts_ok': pts_ok,
                'isStaffetta': is_staff,
                'rawStaff': atleta if is_staff else '',
                'staffAthl': staff_list,
                'isManual': True,
                'soc_cod': '',
                'soc_nome': '',
                'savedId': saved_id,
                'savedAt': time.strftime('%Y-%m-%dT%H:%M:%S'),
            }
            saved_data.setdefault(categoria, []).append(entry)
            imported.append(entry)

        if errors and not imported:
            return jsonify({'ok': False, 'errors': errors, 'imported': []})

        if imported:
            _write_manual(saved_data)

        return jsonify({'ok': True, 'imported': imported, 'errors': errors})

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@app.route('/api/reoptimize_soc', methods=['POST'])
def api_reoptimize_soc():
    """Ricalcola l'ottimale per una singola società nel JSON della proiezione cached.
    Chiamato in background quando un record manuale viene aggiunto o rimosso."""
    try:
        payload  = request.get_json(force=True)
        soc_cod  = payload.get('soc_cod', '')
        cat      = payload.get('categoria', '')
        anno     = payload.get('anno', '2026')
        tipo     = payload.get('tipo_attivita', 'P')
        sesso    = payload.get('sesso', 'F')
        reg      = payload.get('regione', 'LOM')

        if not soc_cod or not cat:
            return jsonify({'ok': False, 'error': 'soc_cod e categoria obbligatori'})

        cache_path = _proiezione_cache_path(anno, tipo, sesso, cat, reg)
        if not os.path.exists(cache_path):
            return jsonify({'ok': False, 'error': 'Nessuna cache trovata — esegui prima il build'})

        with open(cache_path, encoding='utf-8') as f:
            cache = json.load(f)

        meta = cache.get('societies_meta', {}).get(soc_cod)
        if not meta:
            return jsonify({'ok': False, 'error': 'Società non in cache'})

        # Risultati FIDAL di questa società dalla cache
        soc_fidal = [r for r in cache.get('data', []) if r.get('soc_cod') == soc_cod]
        if not soc_fidal:
            return jsonify({'ok': False, 'error': 'Nessun dato FIDAL per la società'})

        # Manual entries aggiornati
        manual_entries = _read_manual().get(cat, [])
        soc_manual = _match_manual_to_soc(manual_entries, soc_cod, meta.get('nome', ''), soc_fidal)
        _normalize_events(soc_manual, cat)
        results_full = soc_fidal + soc_manual

        # Ricalcola con lo stesso engine di api_ottimizza
        _normalize_events(results_full, cat)
        opt = _compute_optimal_best(results_full, cat)
        if opt:
            opt.pop('combo_scores', None)

        # Aggiorna cache su disco
        meta['manual_count'] = len(soc_manual)
        if opt:
            meta['optimal'] = opt
        cache['societies_meta'][soc_cod] = meta
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False)

        return jsonify({
            'ok': True,
            'score': opt['score'] if opt else 0,
            'manual_count': len(soc_manual),
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)})


def _fetch_society_list(regione):
    """Scarica e restituisce la lista società di una regione da mappa.php."""
    url = f'https://www.fidal.it/mappa.php?x=1&regione={regione}'
    hdrs = {
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/124.0.0.0 Safari/537.36'),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'it-IT,it;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': 'https://www.fidal.it/mappa.php',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    sess = requests.Session()
    sess.headers.update(hdrs)
    try:
        sess.get('https://www.fidal.it/mappa.php', timeout=8)
    except Exception:
        pass
    r = sess.get(url, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    seen, result = set(), []
    for a in soup.find_all('a', href=re.compile(r'/societa/[^/]+/[A-Z]{2}\d')):
        m = re.search(r'/([A-Z]{2}\d+)$', a['href'])
        if m:
            cod  = m.group(1)
            nome = a.get_text(strip=True)
            if cod not in seen and nome:
                seen.add(cod)
                result.append({'cod': cod, 'nome': nome})
    result.sort(key=lambda x: x['nome'])
    return result

@app.route('/api/societa')
def api_societa():
    """GET /api/societa?regione=LOM — Lista delle società affiliate di una regione FIDAL.

    Scrapa ``mappa.php`` di FIDAL per estrarre i codici e i nomi delle società.

    :query regione: Sigla regione FIDAL in maiuscolo (es. ``'LOM'``, ``'PIE'``).
    :return: ``{ok: true, data: [{cod, nome}]}`` ordinato per nome,
             oppure ``{ok: false, error: str}`` se la regione è assente o senza società.
    """
    regione = request.args.get('regione', '').strip().upper()
    if not regione:
        return jsonify({'ok': False, 'error': 'Regione mancante'})
    try:
        data = _fetch_society_list(regione)
        if not data:
            return jsonify({'ok': False,
                'error': f'Nessuna società trovata per la regione {regione}.'})
        return jsonify({'ok': True, 'data': data})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

def _match_manual_to_soc(manual_entries, soc_cod, soc_nome, soc_results):
    """
    Abbina i manual entries a una società.
    Priorità: soc_cod esplicito → overlap cognomi atleti.
    Restituisce i manual entries abbinati, taggati con soc_cod/soc_nome e un id sintetico.
    """
    if not manual_entries:
        return []

    # Cognomi degli atleti della società (dalla lookup FIDAL)
    soc_last_names = set()
    for r in soc_results:
        athletes = [r.get('athlete', '')]
        if r.get('staffAthl'):
            athletes = r['staffAthl']
        elif r.get('rawStaff'):
            athletes = [p.strip() for p in re.split(r'[,/]', r['rawStaff']) if p.strip()]
        for a in athletes:
            last = a.split()[0].upper() if a else ''
            if last:
                soc_last_names.add(last)

    matched, seen_ids = [], set()
    for me in manual_entries:
        mid = me.get('savedId', '')
        if mid in seen_ids:
            continue

        # Match per soc_cod esplicito
        me_cod = me.get('soc_cod', '')
        if me_cod and me_cod != soc_cod:
            continue

        # Se non ha soc_cod, verifica overlap cognomi
        if not me_cod:
            me_athletes = me.get('staffAthl') or [me.get('athlete', '')]
            me_lasts = {a.split()[0].upper() for a in me_athletes if a}
            if not (me_lasts & soc_last_names):
                continue

        entry = dict(me)
        entry['soc_cod']  = soc_cod
        entry['soc_nome'] = soc_nome
        entry.setdefault('pts_ok', True)
        entry.setdefault('isStaffetta', False)
        # Assegna id sintetico per non collidere con gli id FIDAL
        if 'id' not in entry:
            entry['id'] = f'manual_{mid}'
        seen_ids.add(mid)
        matched.append(entry)
    return matched

def _compute_optimal_best(results, cat):
    """Calcola la scheda ottimale eseguendo una chiamata separata per ogni staffetta
    eleggibile più una baseline senza staffette. Garantisce il vero ottimale globale:
    una singola chiamata multi-combo lascerebbe il B&B pottare rami superiori dopo
    aver fissato best_total sul primo combo valutato."""
    ind_results    = [r for r in results if not r.get('isStaffetta')]
    staff_eligible = [r for r in results if r.get('isStaffetta') and r.get('pts_ok')]

    baseline   = CdsOptimizer.compute_optimal(ind_results, cat)
    best_score = baseline['score'] if baseline else 0
    best_opt   = baseline

    for staff in staff_eligible:
        opt_s = CdsOptimizer.compute_optimal(ind_results + [staff], cat)
        if opt_s and opt_s['score'] > best_score:
            best_score = opt_s['score']
            best_opt   = opt_s

    if best_opt:
        best_opt.pop('combo_scores', None)
    return best_opt


def _opt_keepalive(results_full, cat):
    """Generatore: avvia l'ottimizzatore in un thread, emette ': keep\\n\\n' ogni 5s
    mentre aspetta, poi restituisce il risultato come valore del generatore."""
    import threading
    result_box = [None]
    done = threading.Event()
    def _worker():
        result_box[0] = _compute_optimal_best(results_full, cat)
        done.set()
    threading.Thread(target=_worker, daemon=True).start()
    while not done.wait(5.0):
        yield ': keep\n\n'
    return result_box[0]

@app.route('/api/proiezione/build')
def api_proiezione_build():
    """GET /api/proiezione/build — Build completo della proiezione regionale via SSE.

    Per ogni società della regione:
    1. Scarica le graduatorie FIDAL (top-5 per gara).
    2. Abbina i manual entries (``_match_manual_to_soc``).
    3. Se la società può competere (``_can_compete_cat``), calcola la scheda ottimale
       via ``_opt_keepalive`` (ottimizzatore in thread separato, keepalive SSE ogni 5 s).
    4. Implementa refresh incrementale: le società con dati invariati (stessi
       ``num_gare`` e ``total_pts``) riutilizzano la cache senza re-fetch FIDAL.

    Il risultato viene salvato in ``proiezione_<params>.json`` e ogni passo emette
    un evento SSE con tipo ``'status'`` | ``'total'`` | ``'found'`` | ``'unchanged'``
    | ``'skip'`` | ``'done'`` | ``'error'``.
    Un log testuale viene scritto in ``logs/build_log_<params>_<timestamp>.txt``.

    Query parameters: anno, tipo_attivita, sesso, categoria, regione, nazionalita, vento.

    :return: ``Response`` SSE (``text/event-stream``) con ``Cache-Control: no-cache``.
    """
    anno  = request.args.get('anno',  '2026')
    tipo  = request.args.get('tipo_attivita', 'P')
    sesso = request.args.get('sesso', 'F')
    cat   = request.args.get('categoria', 'CF')
    reg   = request.args.get('regione', 'LOM')
    naz   = request.args.get('nazionalita', '0')
    vento = request.args.get('vento', '2')

    def _ev(d):
        return f'data: {json.dumps(d, ensure_ascii=False)}\n\n'

    def generate():
        log_dir = os.path.join(_data_dir(), 'logs')
        os.makedirs(log_dir, exist_ok=True)
        log_name = f'build_log_{anno}_{tipo}_{sesso}_{cat}_{reg}_{time.strftime("%Y%m%d_%H%M%S")}.txt'
        log_path = os.path.join(log_dir, log_name)
        log = open(log_path, 'w', encoding='utf-8')
        def _log(line=''):
            log.write(line + '\n')
            log.flush()

        try:
            _log('=== Build proiezione CdS ===')
            _log(f'Anno: {anno}  Tipo: {tipo}  Sesso: {sesso}  Categoria: {cat}  Regione: {reg}')
            _log(f'Nazionalità: {naz}  Vento: {vento}')
            _log(f'Avviato: {time.strftime("%Y-%m-%d %H:%M:%S")}')
            _log()

            # Carica manual entries per questa categoria
            manual_entries = _read_manual().get(cat, [])

            # Carica cache esistente per refresh incrementale
            cache_path = _proiezione_cache_path(anno, tipo, sesso, cat, reg)
            cached_meta  = {}   # soc_cod → {num_gare, total_pts, ...}
            cached_by_soc = {}  # soc_cod → [results]
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, encoding='utf-8') as f:
                        old_cache = json.load(f)
                    _normalize_events(old_cache.get('data', []), cat)
                    cached_meta = old_cache.get('societies_meta', {})
                    for r in old_cache.get('data', []):
                        cod = r.get('soc_cod')
                        if cod:
                            cached_by_soc.setdefault(cod, []).append(r)
                except Exception:
                    pass

            yield _ev({'type': 'status', 'msg': 'Caricamento lista società…'})
            societies = _fetch_society_list(reg)
            if not societies:
                _log(f'ERRORE: nessuna società trovata per {reg}')
                yield _ev({'type': 'error', 'msg': f'Nessuna società trovata per {reg}'})
                return
            total = len(societies)
            _log(f'Società trovate: {total}')
            _log()
            yield _ev({'type': 'total', 'n': total})

            all_results, new_societies_meta = [], {}
            found_soc, unchanged_soc = 0, 0
            _lanci = {'peso','martello','giavellotto','disco','lancio','vortex','palla'}
            _salti = {'lungo','triplo','alto','asta','salto'}

            for i, soc in enumerate(societies):
                try:
                    p = {'anno': anno, 'tipo_attivita': tipo, 'sesso': sesso,
                         'categoria': cat, 'regione': reg, 'nazionalita': naz,
                         'vento': vento, 'limite': '5', 'societa': soc['cod']}
                    results, _ = _do_fidal_fetch(p)
                    if results:
                        new_meta = _soc_meta(results, cat)
                        old_meta = cached_meta.get(soc['cod'], {})

                        # Forza aggiornamento se can_compete ma manca optimal/data,
                        # o se il numero di manual entries è cambiato
                        soc_manual_count = len(_match_manual_to_soc(
                            manual_entries, soc['cod'], soc['nome'], results))
                        needs_optimal = new_meta.get('can_compete') and (
                            not old_meta.get('optimal') or not old_meta.get('data') or
                            old_meta.get('manual_count', 0) != soc_manual_count
                        )
                        if (not needs_optimal and old_meta and
                                old_meta.get('num_gare') == new_meta['num_gare'] and
                                old_meta.get('total_pts') == new_meta['total_pts'] and
                                soc['cod'] in cached_by_soc):
                            # Dati FIDAL invariati: riutilizza risultati dalla cache
                            cached_results = cached_by_soc[soc['cod']]
                            all_results.extend(cached_results)
                            unchanged_soc += 1

                            # Anche per le società invariate: verifica e integra manual entries
                            soc_manual = _match_manual_to_soc(
                                manual_entries, soc['cod'], soc['nome'], cached_results)
                            _normalize_events(soc_manual, cat)
                            new_manual_count = len(soc_manual)
                            old_manual_count = old_meta.get('manual_count', 0)

                            if (new_meta.get('can_compete') and
                                    (new_manual_count != old_manual_count or
                                     not old_meta.get('optimal'))):
                                # Manual entries cambiati o optimal mancante:
                                # ricalcola con dati FIDAL cached + manual (senza re-fetch FIDAL)
                                results_full = cached_results + soc_manual
                                opt = yield from _opt_keepalive(results_full, cat)
                                meta_upd = dict(old_meta)
                                meta_upd['manual_count'] = new_manual_count
                                meta_upd['data'] = results_full
                                if opt:
                                    meta_upd['optimal'] = opt
                                new_societies_meta[soc['cod']] = meta_upd
                            else:
                                new_societies_meta[soc['cod']] = old_meta

                            _log(f'[{i+1:3}/{total}] = {soc["nome"]} — invariata ({new_meta["num_gare"]} gare · Σ {new_meta["total_pts"]}pt)')
                            yield _ev({'type': 'unchanged', 'soc': soc['nome'],
                                       'num_gare': new_meta['num_gare'],
                                       'total_pts': new_meta['total_pts'],
                                       'done': i+1, 'total': total,
                                       'found': found_soc, 'unchanged': unchanged_soc})
                        else:
                            # Dati nuovi o variati: aggiorna
                            for r in results:
                                r['soc_cod']  = soc['cod']
                                r['soc_nome'] = soc['nome']
                            all_results.extend(results)

                            # Abbina manual entries a questa società
                            soc_manual = _match_manual_to_soc(
                                manual_entries, soc['cod'], soc['nome'], results)
                            _normalize_events(soc_manual, cat)
                            results_full = results + soc_manual  # dati FIDAL + manuali

                            meta_entry = {
                                **new_meta, 'nome': soc['nome'],
                                'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                            }
                            if new_meta.get('can_compete'):
                                meta_entry['data'] = results_full
                                meta_entry['manual_count'] = len(soc_manual)
                                # Calcola scheda ottimale con dati FIDAL + manuali
                                opt = yield from _opt_keepalive(results_full, cat)
                                if opt:
                                    meta_entry['optimal'] = opt
                            new_societies_meta[soc['cod']] = meta_entry
                            found_soc += 1
                            n_athl = len({r['athlete'] for r in results
                                          if not r.get('isStaffetta', False)})
                            ev_set = {r['ev'] for r in results if not r.get('isStaffetta', False)}
                            n_ev = len(ev_set)
                            n_la = sum(1 for ev in ev_set if any(k in ev.lower() for k in _lanci))
                            n_sa = sum(1 for ev in ev_set if any(k in ev.lower() for k in _salti))
                            can_compete = _can_compete_cat(n_ev, n_la, n_sa, cat)
                            opt_score = meta_entry.get('optimal', {}).get('score', -1)
                            compete_tag = '🏆' if can_compete else '⚠'
                            opt_str = f' · ottimale Σ {opt_score}' if (can_compete and opt_score > 0) else ''
                            _log(f'[{i+1:3}/{total}] {compete_tag} {soc["nome"]} — {n_athl} atleti · {n_ev} gare ({n_la} lanci, {n_sa} salti) · Σ {new_meta["total_pts"]}pt{opt_str}')
                            yield _ev({'type': 'found', 'soc': soc['nome'],
                                       'n': len(results), 'n_athl': n_athl,
                                       'n_ev': n_ev, 'n_la': n_la, 'n_sa': n_sa,
                                       'can_compete': can_compete,
                                       'num_gare': new_meta['num_gare'],
                                       'total_pts': new_meta['total_pts'],
                                       'optimal_score': opt_score,
                                       'done': i+1, 'total': total,
                                       'found': found_soc, 'unchanged': unchanged_soc})
                    else:
                        _log(f'[{i+1:3}/{total}] - {soc["nome"]} — nessun risultato')
                        yield _ev({'type': 'skip', 'soc': soc['nome'],
                                   'done': i+1, 'total': total,
                                   'found': found_soc, 'unchanged': unchanged_soc})
                except ValueError as exc:
                    if 'Nessun risultato' in str(exc):
                        _log(f'[{i+1:3}/{total}] - {soc["nome"]} — nessun atleta {cat}')
                    else:
                        _log(f'[{i+1:3}/{total}] ERRORE {soc["nome"]} — {exc}')
                    yield _ev({'type': 'skip', 'soc': soc['nome'],
                               'done': i+1, 'total': total,
                               'found': found_soc, 'unchanged': unchanged_soc})
                except Exception as exc:
                    _log(f'[{i+1:3}/{total}] ERRORE {soc["nome"]} — {exc}')
                    yield _ev({'type': 'skip', 'soc': soc['nome'],
                               'done': i+1, 'total': total,
                               'found': found_soc, 'unchanged': unchanged_soc})
                time.sleep(0.2)   # delay per non sovraccaricare FIDAL

            if all_results:
                updated_at = time.strftime('%Y-%m-%dT%H:%M:%S')
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump({'data': all_results, 'updated_at': updated_at,
                               'societies_meta': new_societies_meta},
                              f, ensure_ascii=False, indent=2)
                _log()
                _log(f'=== Completato: {updated_at} ===')
                _log(f'Società aggiornate: {found_soc}  invariate: {unchanged_soc}  totale prestazioni: {len(all_results)}')
                _log(f'Log salvato in: {log_path}')
                yield _ev({'type': 'done', 'n_results': len(all_results),
                           'found_societies': found_soc, 'unchanged_societies': unchanged_soc,
                           'updated_at': updated_at, 'log_path': log_path})
            else:
                _log(f'ERRORE: nessun risultato {cat} trovato nella regione {reg}')
                yield _ev({'type': 'error',
                           'msg': f'Nessun risultato {cat} trovato nella regione {reg}'})
        except Exception as e:
            _log(f'ERRORE FATALE: {e}')
            yield _ev({'type': 'error', 'msg': str(e)})
        finally:
            log.close()

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )


# ── ENTRYPOINT ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5001

    def open_browser():
        time.sleep(1.2)
        webbrowser.open(f'http://localhost:{port}')

    threading.Thread(target=open_browser, daemon=True).start()
    print(f"""
╔══════════════════════════════════════════════╗
║   FIDAL CdS Tool — Scheda Provinciale        ║
║   → http://localhost:{port}                     ║
║   Ctrl+C per uscire                          ║
║   Build: e62966d (optimizer v3 + fix manual) ║
╚══════════════════════════════════════════════╝
""")
    app.run(port=port, debug=False, use_reloader=False)
