#!/usr/bin/env python3
"""
FIDAL CdS Tool — Scheda Provinciale Cadette/Cadetti
Uso: python fidal_cds_tool.py
Poi apri http://localhost:5001
"""
from flask import Flask, jsonify, request, Response, stream_with_context
import requests
from bs4 import BeautifulSoup
import re, sys, threading, time, webbrowser, json, os
from itertools import combinations
from core.cds_utils import CdsUtils
from core.cds_optimizer import CdsOptimizer
from core.cds_manual import read_manual, write_manual, MANUAL_FILE, _data_dir

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

app = Flask(__name__)

# ── TABELLE PUNTEGGI ─────────────────────────────────────────────────────────

def _load_tabella(filename):
    # In modalità PyInstaller i dati vengono estratti in sys._MEIPASS
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
    """Restituisce la chiave della tabella che corrisponde al nome FIDAL, o None."""
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
    """Converte una prestazione in float (secondi per corse, metri per campo)."""
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
    min_ev, min_la, min_sa = _COMPETE_THRESH.get(cat, (10, 2, 2))
    return n_ev >= min_ev and n_la >= min_la and n_sa >= min_sa


def _soc_meta(results, cat=None):
    """Statistiche aggregate per una società: totale punti, breakdown per tipo, eleggibilità."""
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
    from core.cds_optimizer import CdsOptimizer
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
        import traceback; traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/')
def index():
    return Response(FRONTEND_HTML, mimetype='text/html')

@app.route('/.well-known/appspecific/com.chrome.devtools.json')
def chrome_devtools():
    return jsonify({})

@app.route('/api/fidal_status')
def api_fidal_status():
    """Verifica raggiungibilità server FIDAL. Chiamata dal frontend come health-check."""
    import urllib.request, time
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
    categoria = request.args.get('categoria', '')
    data = _read_manual()
    return jsonify({'ok': True, 'data': data.get(categoria, [])})

@app.route('/api/manual', methods=['POST'])
def api_manual_save():
    try:
        entry = request.get_json(force=True)
        categoria = entry.get('categoria', '')
        if not categoria:
            return jsonify({'ok': False, 'error': 'Categoria mancante'})
        saved_id = f"{categoria}_{int(time.time()*1000)}"
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
    content = '\r\n'.join(_CSV_TEMPLATE_ROWS) + '\r\n'
    from flask import Response
    return Response(
        content,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename="template_importazione_cds.csv"'}
    )

@app.route('/api/manual/import_csv', methods=['POST'])
def api_manual_import_csv():
    import csv, io
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
        import traceback; traceback.print_exc()
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
    from core.cds_optimizer import CdsOptimizer
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
            _log(f'=== Build proiezione CdS ===')
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
                yield _ev({'type': 'error', 'msg': f'Nessuna società trovata per {reg}'}); return
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

# ── FRONTEND HTML ─────────────────────────────────────────────────────────────

FRONTEND_HTML = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FIDAL CdS Tool</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Barlow+Condensed:wght@400;600;700;800&family=Barlow:wght@400;500&display=swap');
:root{
  --blue:#054FAE;--blue2:#1a6dd4;--accent:#00C9FF;
  --green:#1a7f3c;--red:#c0392b;--orange:#d46b08;
  --bg:#f0f4f9;--card:#fff;--text:#0d1f3c;--muted:#6b82a0;--border:#dce6f0;
  --mono:'DM Mono',monospace;--head:'Barlow Condensed',sans-serif;--body:'Barlow',sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--body);min-height:100vh}

/* SCREENS */
.screen{display:none} .screen.active{display:block}

/* HEADER */
.hbar{background:var(--blue);padding:.85rem 2rem;display:flex;align-items:center;gap:1rem;
  box-shadow:0 2px 10px rgba(5,79,174,.4);position:sticky;top:0;z-index:100}
.hbadge{width:40px;height:40px;background:var(--accent);border-radius:7px;
  display:flex;align-items:center;justify-content:center;
  font-family:var(--head);font-size:1.1rem;font-weight:800;color:var(--blue);flex-shrink:0}
.htitle{font-family:var(--head);font-size:1.1rem;font-weight:800;color:#fff;letter-spacing:.02em}
.hsub{font-size:.7rem;color:rgba(255,255,255,.6);text-transform:uppercase;letter-spacing:.06em}
.hmeta{margin-left:auto;display:flex;gap:.6rem;flex-wrap:wrap}
.tag{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);
  color:rgba(255,255,255,.8);font-size:.68rem;font-family:var(--mono);
  padding:.2rem .6rem;border-radius:20px;white-space:nowrap}
.tag.acc{background:var(--accent);color:var(--blue);border-color:var(--accent);font-weight:600}
.btn-back{background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.3);
  color:#fff;font-family:var(--head);font-size:.8rem;font-weight:600;letter-spacing:.04em;
  padding:.3rem .85rem;border-radius:5px;cursor:pointer;transition:background .15s}
.btn-back:hover{background:rgba(255,255,255,.25)}

/* FORM SCREEN */
.form-wrap{max-width:700px;margin:3rem auto;padding:0 1.5rem}
.form-card{background:var(--card);border-radius:12px;padding:2rem;
  box-shadow:0 4px 24px rgba(5,79,174,.1)}
.form-card h2{font-family:var(--head);font-size:1.4rem;font-weight:800;
  color:var(--blue);letter-spacing:.03em;margin-bottom:.25rem}
.form-card p.sub{font-size:.82rem;color:var(--muted);margin-bottom:1.5rem}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
.form-group{display:flex;flex-direction:column;gap:.35rem}
.form-group label{font-size:.7rem;font-weight:700;text-transform:uppercase;
  letter-spacing:.07em;color:var(--muted)}
.form-group select,.form-group input{
  font-family:var(--body);font-size:.875rem;color:var(--text);
  background:var(--bg);border:1.5px solid var(--border);border-radius:6px;
  padding:.5rem .75rem;outline:none;transition:border-color .15s}
.form-group select:focus,.form-group input:focus{border-color:var(--blue)}
.form-group.span2{grid-column:1/-1}
/* FIDAL CONNECTION STATUS */
.fidal-status{display:flex;align-items:center;gap:.55rem;margin:.9rem 0 .2rem;
  font-size:.78rem;color:var(--muted);min-height:1.4rem}
.fidal-led{width:9px;height:9px;border-radius:50%;flex-shrink:0;transition:background .3s}
.fidal-led.idle{background:#bbb}
.fidal-led.checking{background:#f0c040;animation:led-pulse .9s ease-in-out infinite}
.fidal-led.ok{background:#2ecc71}
.fidal-led.error{background:#e74c3c}
@keyframes led-pulse{0%,100%{opacity:1}50%{opacity:.35}}
.btn-primary:disabled,.btn-secondary:disabled,.btn-build:disabled{
  opacity:.38;cursor:not-allowed;pointer-events:none}

.btn-primary{width:100%;margin-top:1.25rem;font-family:var(--head);font-size:1rem;
  font-weight:700;letter-spacing:.05em;text-transform:uppercase;background:var(--blue);
  color:#fff;border:none;border-radius:7px;padding:.75rem;cursor:pointer;transition:opacity .15s}
.btn-primary:hover{opacity:.88}
.btn-secondary{width:100%;margin-top:.5rem;font-family:var(--head);font-size:.88rem;
  font-weight:600;letter-spacing:.04em;text-transform:uppercase;background:transparent;
  border:1.5px solid var(--blue);color:var(--blue);border-radius:7px;
  padding:.6rem;cursor:pointer;transition:all .15s}
.btn-secondary:hover{background:var(--blue);color:#fff}
.btn-build{width:100%;margin-top:.4rem;font-family:var(--head);font-size:.8rem;
  font-weight:600;letter-spacing:.04em;text-transform:uppercase;background:transparent;
  border:1.5px solid var(--muted);color:var(--muted);border-radius:7px;
  padding:.5rem;cursor:pointer;transition:all .15s}
.btn-build:hover{border-color:var(--blue2);color:var(--blue2)}
.btn-csv-form{width:100%;margin-top:.4rem;font-family:var(--head);font-size:.8rem;
  font-weight:600;letter-spacing:.04em;text-transform:uppercase;background:transparent;
  border:1.5px dashed #198754;color:#198754;border-radius:7px;
  padding:.5rem;cursor:pointer;transition:all .15s}
.btn-csv-form:hover{background:#198754;color:#fff}
.csv-form-note{background:#e8f5ea;border:1px solid #a3d9a5;border-radius:6px;
  padding:.45rem .8rem;font-size:.78rem;color:#1a5c35;margin-bottom:.75rem}
.csv-disc-cat{font-weight:700;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;
  color:var(--blue);margin-bottom:.2rem}
.csv-disc-pill{display:inline-block;background:#f0f4fa;border:1px solid var(--border);
  border-radius:3px;padding:.05rem .35rem;font-size:.72rem;font-family:monospace;
  margin:.1rem .15rem .1rem 0;color:var(--text)}
.build-area{margin-top:1rem;padding:1rem;background:var(--bg);border:1.5px solid var(--border);
  border-radius:8px}
.build-area h4{font-family:var(--head);font-size:.8rem;font-weight:700;
  text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-bottom:.5rem;
  display:flex;justify-content:space-between;align-items:center}
.build-progress-track{background:#dce6f0;border-radius:4px;height:7px;overflow:hidden;margin-bottom:.45rem}
.build-progress-fill{height:100%;background:var(--blue2);border-radius:4px;
  transition:width .4s ease;width:0%}
.build-status{font-size:.75rem;color:var(--text);font-weight:500;margin-bottom:.35rem}
.build-log{font-size:.68rem;color:var(--muted);max-height:90px;overflow-y:auto;
  line-height:1.5;border-top:1px solid var(--border);padding-top:.35rem;margin-top:.35rem}
.proiezione-bar{background:#e8f0fe;border-bottom:2px solid var(--blue2);
  padding:.55rem 2rem;font-size:.8rem;color:var(--blue);font-weight:600;
  display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}
.btn-refresh-cache{font-family:var(--head);font-size:.73rem;font-weight:700;
  letter-spacing:.04em;text-transform:uppercase;background:transparent;
  border:1.5px solid var(--blue);color:var(--blue);padding:.2rem .65rem;
  border-radius:5px;cursor:pointer;transition:all .15s;white-space:nowrap}
.btn-refresh-cache:hover{background:var(--blue);color:#fff}
.btn-refresh-cache:disabled{opacity:.5;cursor:not-allowed}
.clas-panel{background:var(--card);border-bottom:2px solid var(--border);padding:1rem 2rem 1.25rem}
.clas-panel h3{font-family:var(--head);font-size:1rem;font-weight:800;color:var(--blue);margin-bottom:.75rem}
.clas-table{width:100%;border-collapse:collapse;font-size:.8rem}
.clas-table th{font-family:var(--head);font-size:.67rem;font-weight:700;text-transform:uppercase;
  letter-spacing:.06em;color:var(--muted);padding:.35rem .6rem;border-bottom:2px solid var(--border);text-align:left}
.clas-table td{padding:.28rem .6rem;border-bottom:1px solid var(--border);vertical-align:middle}
.clas-table tr:nth-child(odd) td{background:#f7faff}
.clas-rank{font-family:var(--mono);font-weight:700;color:var(--muted);width:36px;white-space:nowrap}
.clas-score{font-family:var(--mono);font-weight:700;color:var(--blue);white-space:nowrap}
.clas-bar-cell{width:200px}
.clas-bar{height:7px;background:var(--blue2);border-radius:4px;min-width:2px;transition:width .3s}
.clas-detail{display:none;background:#f7faff;border-bottom:2px solid var(--border)}
.clas-detail.open{display:table-row}
.clas-detail td{padding:.5rem 1rem .75rem 2.5rem}
.clas-detail-table{width:100%;border-collapse:collapse;font-size:.76rem}
.clas-detail-table td{padding:.18rem .5rem;border-bottom:1px solid var(--border)}
.clas-detail-table tr:last-child td{border:none}
.clas-gap-badge{display:inline-block;background:#fff3cd;color:#7a4f00;border:1px solid #ffc107;
  border-radius:4px;font-size:.72rem;font-weight:700;padding:1px 7px;white-space:nowrap}
.clas-gap-zero{color:var(--muted);font-size:.72rem;font-style:italic}
.gap-panel{margin-top:.65rem;border-top:1px solid var(--border);padding-top:.55rem}
.gap-panel-title{font-family:var(--head);font-size:.7rem;font-weight:700;letter-spacing:.05em;
  text-transform:uppercase;color:var(--muted);margin-bottom:.35rem}
.gap-impr-table{width:100%;border-collapse:collapse;font-size:.74rem}
.gap-impr-table th{background:#f0f4fa;font-family:var(--head);font-size:.65rem;font-weight:700;
  letter-spacing:.04em;text-transform:uppercase;padding:.22rem .5rem;text-align:left}
.gap-impr-table td{padding:.2rem .5rem;border-bottom:1px solid var(--border)}
.gap-impr-table tr:last-child td{border:none}
.gap-delta{font-family:var(--mono);font-weight:700;color:var(--green)}
.gap-perf-arr{color:var(--muted);margin:0 .25rem}
.gap-tot-row td{font-weight:700;background:#f7faff;font-size:.75rem}
.clas-expand{background:none;border:1px solid var(--border);border-radius:4px;
  cursor:pointer;font-size:.75rem;padding:.1rem .4rem;color:var(--muted);
  line-height:1;transition:all .15s}
.clas-expand:hover{border-color:var(--blue);color:var(--blue)}
.btn-open-soc{background:none;border:1px solid var(--blue);border-radius:4px;
  cursor:pointer;font-size:.72rem;padding:.1rem .45rem;color:var(--blue);
  line-height:1;transition:all .15s;white-space:nowrap}
.btn-open-soc:hover{background:var(--blue);color:#fff}
@keyframes bspin{to{transform:rotate(360deg)}}
.bspin{display:inline-block;animation:bspin .8s linear infinite}
.url-preview{margin-top:.75rem;font-family:var(--mono);font-size:.68rem;
  color:var(--muted);word-break:break-all;padding:.5rem .75rem;
  background:var(--bg);border-radius:5px;border:1px solid var(--border)}

/* LOADING */
.loading-overlay{position:fixed;inset:0;background:rgba(5,10,30,.65);
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  z-index:999;gap:1rem}
.loading-overlay.hidden{display:none}
.spinner{width:44px;height:44px;border:4px solid rgba(255,255,255,.2);
  border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.loading-overlay p{color:#fff;font-family:var(--head);font-size:1rem;font-weight:600}
.loading-bar-track{width:280px;height:5px;background:rgba(255,255,255,.2);border-radius:3px;overflow:hidden}
.loading-bar-fill{height:100%;border-radius:3px;background:var(--accent);width:0%;transition:width .25s}
@keyframes indeterminate{0%{left:-40%;width:40%}100%{left:100%;width:40%}}
.loading-bar-fill.indeterminate{position:relative;animation:indeterminate 1.2s ease infinite;width:40%!important}
.error-msg{background:#fdf0f0;border:1.5px solid var(--red);color:var(--red);
  border-radius:7px;padding:.75rem 1rem;font-size:.82rem;margin-top:1rem}

/* TOOL SCREEN — CONSTRAINT PANEL */
.cbar{background:#fff;border-bottom:1px solid var(--border);
  padding:.6rem 2rem;display:flex;gap:.75rem;flex-wrap:wrap;align-items:center}
.cbox{display:flex;align-items:center;gap:.4rem;padding:.3rem .7rem;border-radius:6px;
  border:1.5px solid var(--border);font-size:.75rem;font-weight:600;
  transition:all .2s;white-space:nowrap}
.cbox.ok{border-color:var(--green);color:var(--green);background:#f0faf4}
.cbox.warn{border-color:var(--orange);color:var(--orange);background:#fff8f0}
.cbox.err{border-color:var(--red);color:var(--red);background:#fdf0f0}

/* EVENT FILTER PANEL */
.ev-filter-panel{background:#fff;border-bottom:2px solid var(--border);padding:.65rem 2rem;display:flex;flex-direction:column;gap:.5rem}
.ev-filter-panel h3{font-family:var(--head);font-size:.82rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.ev-chips-row{display:flex;gap:.4rem;flex-wrap:wrap;align-items:center}
.ev-chip{font-size:.72rem;font-weight:600;padding:.22rem .65rem;border-radius:12px;
  border:1.5px solid var(--green);background:#f0faf4;color:var(--green);
  cursor:pointer;transition:all .15s;white-space:nowrap;
  font-family:var(--head);letter-spacing:.02em;user-select:none}
.ev-chip:hover{opacity:.75}
.ev-chip.excl{border-color:#bbb;background:#f2f2f2;color:#aaa;text-decoration:line-through}
.ev-chip.excl:hover{border-color:var(--green);background:#f0faf4;color:var(--green);text-decoration:none;opacity:1}

/* STAFFETTA PANEL */
.staff-panel{background:#fff;border-bottom:2px solid var(--border);padding:.85rem 2rem}
.staff-panel h3{font-family:var(--head);font-size:.82rem;font-weight:700;
  text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-bottom:.6rem}
.staff-cards{display:flex;gap:.85rem;flex-wrap:wrap}
.staff-card{border:1.5px solid var(--border);border-radius:8px;padding:.7rem .9rem;
  min-width:260px;flex:1;max-width:460px}
.staff-card.ok{border-color:var(--green);background:#f0faf4}
.staff-card.warn{border-color:#f0c040;background:#fffbeb}
.staff-card.no{border-color:var(--red);background:#fdf0f0}
.scard-verdict.warn{color:#7a5700}
.scard-head{display:flex;align-items:center;gap:.6rem;margin-bottom:.5rem}
.scard-ev{font-family:var(--head);font-size:.9rem;font-weight:700}
.scard-perf{font-family:var(--mono);font-size:.78rem;color:var(--blue)}
.scard-pts{font-family:var(--mono);font-size:.82rem;font-weight:600;color:var(--orange)}
.chips{display:flex;flex-wrap:wrap;gap:.3rem;margin-bottom:.45rem}
.chip{font-size:.7rem;padding:.12rem .5rem;border-radius:10px;
  background:#eef2f8;color:var(--text);font-weight:500}
.scard-verdict{font-size:.75rem;font-weight:600;margin-top:.35rem}
.scard-verdict.ok{color:var(--green)}
.scard-verdict.no{color:var(--orange)}

/* AUTO-OPTIONS BAR */
.auto-opts{background:#f0f4ff;border-bottom:1px solid var(--border);
  padding:.3rem 2rem;display:flex;align-items:center;gap:1.4rem;flex-wrap:wrap;font-size:.75rem}
.auto-opts label{display:flex;align-items:center;gap:.3rem;cursor:pointer;
  color:var(--muted);user-select:none}
.auto-opts input[type=checkbox]{accent-color:var(--blue);width:13px;height:13px;cursor:pointer}

/* TOTALS BAR */
.totbar{background:var(--blue);display:flex;align-items:center;
  padding:.6rem 2rem;gap:2rem;flex-wrap:wrap}
.tstat{display:flex;align-items:baseline;gap:.4rem}
.tstat .val{font-family:var(--head);font-size:1.4rem;font-weight:800;color:var(--accent)}
.tstat .lbl{font-size:.68rem;color:rgba(255,255,255,.6);text-transform:uppercase;letter-spacing:.05em}
.note-est{font-size:.63rem;color:rgba(255,255,255,.45);margin-right:auto}
.btn-opt{font-family:var(--head);font-size:.85rem;font-weight:700;letter-spacing:.05em;
  text-transform:uppercase;background:var(--accent);border:none;color:var(--blue);
  padding:.5rem 1.2rem;border-radius:6px;cursor:pointer;transition:opacity .15s}
.btn-opt:hover{opacity:.85}
.btn-clr{font-family:var(--head);font-size:.8rem;font-weight:600;letter-spacing:.04em;
  text-transform:uppercase;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.3);
  color:rgba(255,255,255,.8);padding:.5rem .9rem;border-radius:6px;cursor:pointer}
.btn-clr:hover{background:rgba(255,255,255,.2)}
.btn-pdf{font-family:var(--head);font-size:.8rem;font-weight:600;letter-spacing:.04em;
  text-transform:uppercase;background:rgba(255,255,255,.18);border:1px solid rgba(255,255,255,.4);
  color:#fff;padding:.5rem .9rem;border-radius:6px;cursor:pointer;transition:background .15s}
.btn-pdf:hover{background:rgba(255,255,255,.28)}

/* MAIN LAYOUT */
.main{padding:1.25rem 2rem;display:grid;gap:1.25rem}

.card{background:var(--card);border-radius:10px;
  box-shadow:0 1px 3px rgba(0,0,0,.06),0 4px 16px rgba(5,79,174,.07);overflow:hidden}
.card-head{background:var(--blue);padding:.55rem 1rem;display:flex;align-items:center;gap:.5rem}
.card-head h2{font-family:var(--head);font-size:.88rem;font-weight:700;
  letter-spacing:.06em;text-transform:uppercase;color:#fff}
.badge-n{background:var(--accent);color:var(--blue);font-family:var(--mono);
  font-size:.68rem;font-weight:600;padding:.1rem .42rem;border-radius:12px}

/* TABLES */
.tbl{width:100%;border-collapse:collapse;font-size:.81rem}
.tbl thead th{background:#f5f8fd;border-bottom:2px solid var(--border);
  padding:.45rem .7rem;text-align:left;font-family:var(--head);
  font-size:.7rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--muted);white-space:nowrap;cursor:pointer;user-select:none}
.tbl thead th:hover{color:var(--blue)}
.tbl tbody tr{border-bottom:1px solid var(--border);transition:background .1s}
.tbl tbody tr:last-child{border-bottom:none}
.tbl td{padding:.45rem .7rem;vertical-align:middle}

/* ROW STATUS COLORS */
.row-sel{background:#e8f5e9 !important}
.row-free{cursor:pointer} .row-free:hover{background:#f0f7ff}
.row-warn{cursor:pointer;background:#fffbf0} .row-warn:hover{background:#fff5d6}
.row-block{opacity:.5;cursor:not-allowed;background:#fafafa}

/* EVENT TYPE BADGES */
.etype{font-size:.63rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;
  padding:.12rem .42rem;border-radius:4px;white-space:nowrap}
.etype.corsa{background:#dbeafe;color:#1e40af}
.etype.ostacoli{background:#ede9fe;color:#5b21b6}
.etype.salto{background:#d1fae5;color:#065f46}
.etype.lancio{background:#fef3c7;color:#92400e}
.etype.staffetta{background:#fce7f3;color:#9d174d}

/* STATUS ICONS */
.si{font-size:.85rem;flex-shrink:0}

.perf{font-family:var(--mono);font-weight:500;color:var(--blue);white-space:nowrap}
.athl-link{color:inherit;text-decoration:none;border-bottom:1px dotted var(--muted)}
.athl-link:hover{color:var(--blue2);border-bottom-color:var(--blue2)}
.best-mark{color:#c0392b;font-weight:800;font-size:.85rem;margin-right:.15rem;vertical-align:baseline}
#calcola-err{background:#fdf0f0;border-bottom:2px solid var(--red);padding:.9rem 2rem;
  font-family:var(--head);font-size:1.05rem;font-weight:700;
  color:var(--red);letter-spacing:.02em}
.opt-diag{margin-top:.55rem;font-size:.82rem;font-weight:400;letter-spacing:0;
  border-top:1px solid rgba(192,57,43,.2);padding-top:.45rem;display:flex;
  flex-direction:column;gap:.22rem}
.opt-diag-row{display:flex;gap:.5rem;align-items:baseline}
.opt-diag-row .diag-ico{font-size:.85rem;flex-shrink:0}
.opt-diag-row .diag-ev{color:rgba(192,57,43,.75);font-size:.76rem;margin-left:.3rem}
.pts-inp{font-family:var(--mono);font-size:.82rem;font-weight:600;width:70px;
  border:1.5px solid var(--border);border-radius:4px;padding:.18rem .38rem;
  text-align:right;color:var(--blue);background:transparent;outline:none}
.pts-inp:focus{border-color:var(--blue2)}
.pts-inp.est{color:var(--orange)}
.del-btn{background:none;border:none;cursor:pointer;color:var(--muted);
  font-size:.85rem;padding:.15rem .38rem;border-radius:4px;transition:all .1s}
.del-btn:hover{background:#fdf0f0;color:var(--red)}
.add-btn{background:none;border:1.5px solid var(--border);border-radius:5px;
  font-size:.7rem;font-weight:700;font-family:var(--head);letter-spacing:.04em;
  text-transform:uppercase;color:var(--muted);cursor:pointer;padding:.18rem .52rem;transition:all .15s}
.add-btn:hover:not(:disabled){border-color:var(--blue);color:var(--blue)}
.add-btn.sel{border-color:var(--green);color:var(--green);background:#f0faf4}
.add-btn.sel:hover{border-color:var(--red);color:var(--red);background:#fdf0f0}
.add-btn:disabled{opacity:.35;cursor:not-allowed}

/* ATHLETE TRACKER */
.atl-tracker{display:flex;gap:.5rem;flex-wrap:wrap;padding:.7rem 1rem;
  border-bottom:1px solid var(--border);background:#fafbfd}
.atl-chip{display:flex;align-items:center;gap:.3rem;font-size:.72rem;
  padding:.2rem .55rem;border-radius:12px;border:1.5px solid var(--border);
  font-weight:500;white-space:nowrap;transition:all .15s}
.atl-chip.free{border-color:#bde0bd;background:#f0faf4;color:var(--green)}
.atl-chip.half{border-color:#e6c000;background:#fffbe6;color:#7a5200;font-weight:700}
.atl-chip.full{border-color:#c0392b;background:#fde8e8;color:#c0392b;font-weight:700}
.atl-cnt{font-family:var(--mono);font-size:.65rem;font-weight:700}

/* FILTERS */
.frow{padding:.6rem 1rem;border-bottom:1px solid var(--border);
  display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}
.frow select,.frow input{font-family:var(--body);font-size:.79rem;
  border:1.5px solid var(--border);border-radius:5px;padding:.28rem .55rem;
  background:var(--bg);color:var(--text);outline:none}
.frow select:focus,.frow input:focus{border-color:var(--blue)}

.dbl-badge{background:#fef3c7;color:#92400e;font-size:.62rem;font-weight:700;
  padding:.1rem .32rem;border-radius:4px;margin-left:.3rem}
.tie-badge{background:#fff3cd;color:#7a5700;font-size:.62rem;font-weight:800;
  padding:.1rem .32rem;border-radius:4px;margin-left:.4rem;cursor:help;
  border:1px solid #f0c040}
.tie-panel{background:#fffbeb;border-bottom:2px solid #f0c040;padding:.75rem 2rem;
  font-size:.78rem;display:flex;flex-direction:column;gap:.35rem}
.tie-panel-title{font-family:var(--head);font-size:.78rem;font-weight:700;
  color:#7a5700;text-transform:uppercase;letter-spacing:.04em;margin-bottom:.1rem}
.tie-row{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;padding:.15rem 0;
  border-bottom:1px solid #f5e090}
.tie-row:last-child{border-bottom:none}
.tie-sel{color:#444;white-space:nowrap}
.tie-chip{background:#fff;border:1px solid #d4b800;color:#5a3e00;border-radius:10px;
  padding:.1rem .45rem;font-size:.72rem;white-space:nowrap;cursor:pointer}
.tie-chip:hover{background:#fff8d0}
.legenda{padding:.55rem 1rem;border-top:1px solid var(--border);font-size:.69rem;
  color:var(--muted);display:flex;gap:1.25rem;flex-wrap:wrap}
.grand-total{font-family:var(--mono);font-size:1.05rem;font-weight:600;color:var(--blue)}

/* MANUAL RELOAD BAR */
.manual-reload-bar{background:#fff8e1;border-bottom:2px solid #ffc107;
  padding:.5rem 2rem;display:flex;align-items:center;gap:.75rem;flex-wrap:wrap;font-size:.82rem}

/* MANUAL ENTRY */
.manual-bar{padding:.55rem 1rem;border-top:1px solid var(--border);background:#fafbfd;
  display:flex;align-items:center;gap:.75rem;flex-wrap:wrap}
.btn-add-manual{font-family:var(--head);font-size:.78rem;font-weight:700;letter-spacing:.04em;
  text-transform:uppercase;background:transparent;border:1.5px dashed var(--blue);
  color:var(--blue);padding:.3rem .85rem;border-radius:6px;cursor:pointer;transition:all .15s}
.btn-add-manual:hover{background:var(--blue);color:#fff}
.manual-form-box{padding:.85rem 1.1rem;background:#f5f8fd;border-top:1px solid var(--border)}
.mfg{display:grid;grid-template-columns:2fr 1fr 1fr;gap:.6rem;margin-bottom:.55rem}
.mfg2{display:grid;grid-template-columns:2fr 1fr;gap:.6rem;margin-bottom:.65rem}
.fg-sm{display:flex;flex-direction:column;gap:.22rem}
.fg-sm label{font-size:.67rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.fg-sm input,.fg-sm select{font-family:var(--body);font-size:.82rem;border:1.5px solid var(--border);
  border-radius:5px;padding:.3rem .55rem;background:#fff;color:var(--text);outline:none;
  transition:border-color .15s}
.fg-sm input:focus,.fg-sm select:focus{border-color:var(--blue)}
.btn-mok{font-family:var(--head);font-size:.8rem;font-weight:700;letter-spacing:.04em;
  text-transform:uppercase;background:var(--green);color:#fff;border:none;
  padding:.38rem .9rem;border-radius:5px;cursor:pointer;transition:opacity .15s}
.btn-mok:hover{opacity:.85}
.btn-mcancel{font-family:var(--head);font-size:.78rem;font-weight:600;background:transparent;
  border:1px solid var(--border);color:var(--muted);padding:.38rem .8rem;border-radius:5px;
  cursor:pointer;margin-left:.4rem}
.manual-badge{font-size:.6rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase;
  background:#fff3cd;color:#856404;border:1px solid #ffc107;padding:.07rem .35rem;
  border-radius:3px;margin-left:.35rem;vertical-align:middle}
.btn-import-csv{font-family:var(--head);font-size:.78rem;font-weight:700;letter-spacing:.04em;
  text-transform:uppercase;background:transparent;border:1.5px dashed #198754;
  color:#198754;padding:.3rem .85rem;border-radius:6px;cursor:pointer;transition:all .15s}
.btn-import-csv:hover{background:#198754;color:#fff}

/* CSV IMPORT MODAL */
.csv-overlay{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:9000;
  display:flex;align-items:center;justify-content:center;padding:1rem}
.csv-modal{background:#fff;border-radius:10px;box-shadow:0 8px 32px rgba(0,0,0,.22);
  width:min(700px,96vw);max-height:90vh;display:flex;flex-direction:column;overflow:hidden}
.csv-modal-head{padding:.85rem 1.2rem;border-bottom:1px solid var(--border);
  display:flex;align-items:center;justify-content:space-between;flex-shrink:0}
.csv-modal-head h3{margin:0;font-size:1rem;font-family:var(--head);color:var(--text)}
.csv-modal-close{background:none;border:none;font-size:1.3rem;cursor:pointer;
  color:var(--muted);line-height:1;padding:.1rem .3rem}
.csv-modal-close:hover{color:var(--red)}
.csv-modal-body{overflow-y:auto;padding:1rem 1.2rem;flex:1}
.csv-section{margin-bottom:1.1rem}
.csv-section h4{font-family:var(--head);font-size:.78rem;font-weight:700;letter-spacing:.06em;
  text-transform:uppercase;color:var(--muted);margin:0 0 .4rem}
.csv-col-table{width:100%;border-collapse:collapse;font-size:.8rem}
.csv-col-table th{background:#f0f4fa;font-family:var(--head);font-size:.67rem;font-weight:700;
  letter-spacing:.05em;text-transform:uppercase;padding:.3rem .6rem;text-align:left;
  border:1px solid var(--border)}
.csv-col-table td{padding:.28rem .6rem;border:1px solid var(--border);vertical-align:top}
.csv-col-table td.required{color:var(--red);font-weight:700}
.csv-col-table td.optional{color:var(--muted)}
.csv-col-table code{font-family:monospace;font-size:.77rem;background:#f5f5f5;
  padding:.05rem .25rem;border-radius:3px}
.csv-drop-zone{border:2px dashed #198754;border-radius:8px;padding:1.5rem 1rem;
  text-align:center;cursor:pointer;transition:all .2s;background:#f8fff9}
.csv-drop-zone:hover,.csv-drop-zone.drag-over{background:#e8f5ea;border-color:#0d6e40}
.csv-drop-zone input[type=file]{display:none}
.csv-drop-label{font-size:.88rem;color:#198754;font-weight:600}
.csv-drop-sub{font-size:.74rem;color:var(--muted);margin-top:.25rem}
.csv-file-chosen{font-size:.78rem;color:var(--text);margin-top:.5rem;font-weight:600}
.csv-action-row{display:flex;gap:.6rem;margin-top:.9rem;align-items:center}
.btn-csv-upload{font-family:var(--head);font-size:.82rem;font-weight:700;letter-spacing:.04em;
  text-transform:uppercase;background:#198754;color:#fff;border:none;
  padding:.42rem 1.1rem;border-radius:5px;cursor:pointer;transition:opacity .15s}
.btn-csv-upload:hover{opacity:.85}
.btn-csv-upload:disabled{opacity:.45;cursor:default}
.btn-csv-template{font-family:var(--head);font-size:.78rem;font-weight:600;
  background:transparent;border:1px solid #198754;color:#198754;
  padding:.38rem .8rem;border-radius:5px;cursor:pointer;transition:all .15s}
.btn-csv-template:hover{background:#198754;color:#fff}
.csv-result-box{margin-top:.9rem}
.csv-err-list{list-style:none;padding:0;margin:0}
.csv-err-list li{font-size:.78rem;padding:.28rem .5rem;border-left:3px solid var(--red);
  background:#fff5f5;margin-bottom:.3rem;border-radius:0 4px 4px 0}
.csv-err-list li strong{color:var(--red)}
.csv-ok-bar{background:#e8f5ea;border:1px solid #a3d9a5;border-radius:6px;
  padding:.5rem .9rem;font-size:.84rem;color:#1a5c35;font-weight:600}
.csv-warn-bar{background:#fff8e1;border:1px solid #ffe082;border-radius:6px;
  padding:.5rem .9rem;font-size:.8rem;color:#7a5c00;margin-top:.4rem}

/* GLOBAL FILTERS PANEL */
.gfilter-panel{background:#fff;border-bottom:2px solid var(--border);padding:.6rem 2rem;display:flex;flex-direction:column;gap:.5rem}
.gfilter-section{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}
.gfilter-label{font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);white-space:nowrap;min-width:180px}
.gfilter-input{font-family:var(--body);font-size:.79rem;border:1.5px solid var(--border);border-radius:5px;padding:.28rem .55rem;background:var(--bg);color:var(--text);outline:none;transition:border-color .15s}
.gfilter-input:focus{border-color:var(--blue)}
.btn-gfilter-add{font-family:var(--head);font-size:.74rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase;background:transparent;border:1.5px solid var(--red);color:var(--red);padding:.22rem .65rem;border-radius:5px;cursor:pointer;transition:all .15s}
.btn-gfilter-add:hover{background:var(--red);color:#fff}
.unavail-chip{display:flex;align-items:center;gap:.25rem;font-size:.71rem;padding:.15rem .45rem;border-radius:10px;border:1.5px solid #e57373;background:#fdf0f0;color:var(--red);white-space:nowrap;font-weight:600}
.unavail-chip button{background:none;border:none;cursor:pointer;color:var(--red);font-size:.78rem;padding:0;line-height:1}
.unavail-chip button:hover{color:#7b1a1a}

@media(max-width:680px){
  .form-grid{grid-template-columns:1fr}
  .form-group.span2{grid-column:1}
  .hbar,.cbar,.staff-panel,.totbar,.main{padding-left:1rem;padding-right:1rem}
  .hmeta{display:none}
  .gfilter-panel{padding-left:1rem;padding-right:1rem}
  .gfilter-label{min-width:unset}
}
</style>
</head>
<body>

<!-- LOADING OVERLAY -->
<div class="loading-overlay hidden" id="loading">
  <div class="spinner"></div>
  <p id="loading-msg">Caricamento dati FIDAL…</p>
  <div class="loading-bar-track" id="loading-bar-track" style="display:none">
    <div class="loading-bar-fill indeterminate" id="loading-bar-fill"></div>
  </div>
  <p id="loading-sub" style="font-size:.72rem;color:rgba(255,255,255,.55);font-weight:400;margin-top:-.5rem"></p>
  <div id="opt-log" style="display:none;font-size:.65rem;color:rgba(255,255,255,.4);
    max-height:72px;overflow-y:auto;text-align:left;width:300px;line-height:1.7;
    border-top:1px solid rgba(255,255,255,.12);padding-top:.4rem;margin-top:-.2rem"></div>
</div>

<!-- ══════════════ SCREEN 1: FORM ══════════════ -->
<div class="screen active" id="scr-form">
  <div class="hbar">
    <div class="hbadge">F</div>
    <div>
      <div class="htitle">FIDAL CdS Tool</div>
      <div class="hsub">Scheda Campionato di Società — Fase Provinciale</div>
    </div>
  </div>

  <div class="form-wrap">
    <div class="form-card">
      <h2>Parametri ricerca graduatorie</h2>
      <p class="sub">Inserisci i parametri per caricare i risultati della società dalla banca dati FIDAL.</p>

      <div class="form-grid">
        <div class="form-group">
          <label>Anno</label>
          <select id="f-anno">
            <option value="2026">2026</option>
            <option value="2025">2025</option>
            <option value="2024">2024</option>
          </select>
        </div>

        <div class="form-group">
          <label>Tipo attività</label>
          <select id="f-tipo">
            <option value="P">Outdoor (Pista/Campo)</option>
            <option value="I">Indoor (Pista coperta)</option>
          </select>
        </div>

        <div class="form-group">
          <label>Sesso</label>
          <select id="f-sesso" onchange="updateCatOptions()">
            <option value="F">Femminile</option>
            <option value="M">Maschile</option>
          </select>
        </div>

        <div class="form-group">
          <label>Categoria</label>
          <select id="f-cat"><!-- popolato da updateCatOptions() --></select>
        </div>

        <div class="form-group">
          <label>Regione</label>
          <select id="f-reg">
            <option value="LOM">Lombardia</option>
            <option value="PIE">Piemonte</option>
            <option value="VEN">Veneto</option>
            <option value="EMR">Emilia-Romagna</option>
            <option value="TOS">Toscana</option>
            <option value="LAZ">Lazio</option>
            <option value="CAM">Campania</option>
            <option value="SIC">Sicilia</option>
            <option value="SAR">Sardegna</option>
            <option value="FVG">Friuli VG</option>
            <option value="LIG">Liguria</option>
            <option value="MAR">Marche</option>
            <option value="UMB">Umbria</option>
            <option value="ABR">Abruzzo</option>
            <option value="BAS">Basilicata</option>
            <option value="CAL">Calabria</option>
            <option value="MOL">Molise</option>
            <option value="PUG">Puglia</option>
            <option value="TAA">Trentino AA</option>
            <option value="VDA">Valle d'Aosta</option>
          </select>
        </div>

        <div class="form-group">
          <label>Nazionalità</label>
          <select id="f-naz">
            <option value="0" selected>Tutti</option>
            <option value="1">Italiani e stranieri</option>
            <option value="2">Solo italiani</option>
          </select>
        </div>

        <div class="form-group">
          <label>Vento</label>
          <select id="f-vento">
            <option value="2" selected>Tutti</option>
            <option value="0">Non ventosi</option>
            <option value="1">Con vento</option>
          </select>
        </div>

        <div class="form-group">
          <label>Limite risultati</label>
          <select id="f-limite">
            <option value="100">100</option>
            <option value="50">50</option>
            <option value="30">30</option>
            <option value="20">20</option>
          </select>
        </div>

        <div class="form-group">
          <label>Società (cerca per nome)</label>
          <input id="f-societa-name" type="text" list="societa-datalist"
            placeholder="Cerca per nome…" autocomplete="off"
            oninput="onSocietaNomeInput()">
          <datalist id="societa-datalist"></datalist>
          <span id="societa-status" style="font-size:.67rem;color:var(--muted);
            margin-top:.2rem;display:block"></span>
        </div>
        <div class="form-group">
          <label>Codice Società FIDAL</label>
          <input id="f-societa" type="text" placeholder="es. BS318"
            style="text-transform:uppercase"
            oninput="onSocietaCodiceInput()">
        </div>
      </div>

      <div class="url-preview" id="url-preview">—</div>
      <div class="error-msg" id="form-error" style="display:none"></div>

      <!-- Indicatore connessione FIDAL -->
      <div class="fidal-status">
        <span class="fidal-led idle" id="fidal-led"></span>
        <span id="fidal-status-txt">Verifica connessione FIDAL…</span>
        <button id="fidal-retry-btn" onclick="checkFidalConnection()"
          style="display:none;font-size:.7rem;padding:.1rem .45rem;margin-left:.3rem;
                 border:1px solid var(--muted);border-radius:4px;background:transparent;
                 color:var(--muted);cursor:pointer">↺ Riprova</button>
      </div>

      <button class="btn-primary" id="btn-fetch-fidal" onclick="fetchData()" disabled>⚡ Carica Graduatorie FIDAL</button>
      <button class="btn-secondary" id="btn-fetch-proj" onclick="fetchProiezione()" disabled>📊 Proiezione regionale (da cache)</button>
      <button class="btn-build" id="btn-build-proj" onclick="startBuildProiezione()" disabled>⚙ Costruisci / Aggiorna DB Regionale (analizza tutte le società)</button>
      <button class="btn-csv-form" onclick="openCsvModal(true)">📂 Importa risultati da CSV (modalità manuale — senza caricare graduatorie FIDAL)</button>
      <!-- Area progress build -->
      <div class="build-area" id="build-area" style="display:none">
        <h4>
          <span id="build-area-title">Costruzione database regionale</span>
          <button class="btn-mcancel" style="font-size:.7rem" onclick="cancelBuild()">✕ Annulla</button>
        </h4>
        <div class="build-progress-track">
          <div class="build-progress-fill" id="build-fill"></div>
        </div>
        <div class="build-status" id="build-status">In attesa...</div>
        <div class="build-log" id="build-log"></div>
      </div>
    </div>
  </div>
</div>

<!-- ══════════════ SCREEN 1b: CLASSIFICA REGIONALE ══════════════ -->
<div class="screen" id="scr-classifica">
  <div class="hbar">
    <div class="hbadge">AC</div>
    <div>
      <div class="htitle" id="clas-screen-title">Proiezione Regionale</div>
      <div class="hsub" id="clas-screen-sub">—</div>
    </div>
    <div class="hmeta">
      <span class="tag" id="clas-screen-ts"></span>
    </div>
    <button class="btn-refresh-cache" onclick="fetchProiezione(true)" style="margin-right:.5rem">🔄 Aggiorna dati</button>
    <button class="btn-pdf" onclick="downloadClassificaCSV()" style="margin-right:.4rem">⬇ CSV</button>
    <button class="btn-pdf" onclick="printClassificaPDF()" style="margin-right:.5rem">⬇ Stampa / PDF</button>
    <button class="btn-back" onclick="goBack()">← Nuova ricerca</button>
  </div>
  <div style="padding:1.5rem 2rem">
    <div id="clas-screen-content"><div style="color:var(--muted);font-size:.85rem">Caricamento…</div></div>
  </div>
</div>

<!-- ══════════════ SCREEN 2: TOOL ══════════════ -->
<div class="screen" id="scr-tool">
  <!-- Header -->
  <div class="hbar">
    <div class="hbadge">AC</div>
    <div>
      <div class="htitle" id="tool-title">ATL. CHIARI 1964 LIB.</div>
      <div class="hsub" id="tool-sub">Scheda CdS · Outdoor 2026</div>
    </div>
    <div class="hmeta">
      <span class="tag" id="tag-cat">—</span>
      <span class="tag acc" id="tag-tot">Tot. — pt</span>
    </div>
    <button class="btn-back" id="btn-to-classifica" onclick="goToClassifica()"
      style="display:none">🏆 Classifica</button>
    <button class="btn-back" onclick="goBack()">← Nuova ricerca</button>
  </div>

  <!-- Proiezione banner -->
  <div class="proiezione-bar" id="proiezione-bar" style="display:none">
    <span>📊 <strong>Proiezione Regionale</strong> — punteggio teorico massimo (top atleti per disciplina, max 1 doppiatura).</span>
    <span id="proiezione-ts" style="font-size:.75rem;opacity:.8"></span>
    <span style="margin-left:auto"></span>
    <button class="btn-refresh-cache" onclick="fetchProiezione(true)">🔄 Aggiorna dati</button>
    <button class="btn-refresh-cache" onclick="computeClassifica()">🏆 Classifica</button>
  </div>

  <!-- Classifica regionale -->
  <div class="clas-panel" id="clas-panel" style="display:none">
    <h3 id="clas-title">🏆 Classifica Regionale</h3>
    <div id="clas-content"></div>
  </div>

  <!-- Constraints -->
  <div class="cbar">
    <div class="cbox" id="c-n">   <span>📋</span><span id="c-n-t">0/13 risultati</span></div>
    <div class="cbox" id="c-ev">  <span>📊</span><span id="c-ev-t">0/10 gare</span></div>
    <div class="cbox" id="c-la">  <span>⭕</span><span id="c-la-t">0/2 lanci</span></div>
    <div class="cbox" id="c-sa">  <span>↑</span><span id="c-sa-t">0/2 salti</span></div>
    <div class="cbox" id="c-at">  <span>👤</span><span id="c-at-t">Vincoli OK</span></div>
  </div>

  <!-- Global Filters Panel -->
  <div class="gfilter-panel" id="gfilter-panel">
    <div class="gfilter-section">
      <span class="gfilter-label">🚫 Atleti non disponibili:</span>
      <input id="unavail-input" class="gfilter-input" list="unavail-list"
        placeholder="Cerca atleta…" style="min-width:160px"
        onkeydown="if(event.key==='Enter')addUnavailFromInput()">
      <datalist id="unavail-list"></datalist>
      <button class="btn-gfilter-add" onclick="addUnavailFromInput()">Escludi</button>
      <div id="unavail-chips" style="display:flex;gap:.3rem;flex-wrap:wrap"></div>
    </div>
    <div class="gfilter-section">
      <span class="gfilter-label">📅 Solo prestazioni dal:</span>
      <input id="date-filter-input" class="gfilter-input" type="date"
        style="min-width:145px" onchange="onDateFilterChange()">
      <button class="btn-mcancel" id="date-filter-clear"
        style="font-size:.72rem;padding:.22rem .65rem;display:none"
        onclick="clearDateFilter()">✕ Azzera</button>
      <span id="date-filter-count" style="font-size:.72rem;color:var(--muted)"></span>
    </div>
  </div>

  <!-- Saved manual entries reload bar -->
  <div class="manual-reload-bar" id="manual-reload-bar" style="display:none">
    <span>💾 <strong id="reload-count">0</strong> risultati manuali salvati per questa categoria — vuoi ricaricarli?</span>
    <button class="btn-mok" style="font-size:.74rem;padding:.22rem .65rem" onclick="reloadManualEntries()">⟳ Ricarica</button>
    <button class="btn-mcancel" style="font-size:.74rem" onclick="dismissReloadBar()">✕ Ignora</button>
  </div>

  <!-- Event filter panel -->
  <div class="ev-filter-panel" id="ev-filter-panel" style="display:none">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:.5rem;flex-wrap:wrap">
      <h3 style="margin:0">🏅 Gare nel programma CdS — clicca per escludere</h3>
      <div style="display:flex;gap:.4rem">
        <button class="btn-mok" id="btn-preset-cds" style="font-size:.72rem;padding:.22rem .65rem;display:none" onclick="applyPresetCds()">⚡ Preset CdS</button>
        <button class="btn-mcancel" style="font-size:.72rem;padding:.22rem .65rem" onclick="resetEvFilter()">✕ Includi tutto</button>
      </div>
    </div>
    <div class="ev-chips-row" id="ev-chips"></div>
  </div>

  <!-- Staffetta Analysis -->
  <div class="staff-panel" id="staff-panel" style="display:none">
    <h3>⚡ Analisi Staffette</h3>
    <div class="staff-cards" id="staff-cards"></div>
  </div>

  <!-- Auto-options bar -->
  <div class="auto-opts">
    <span style="font-weight:600;color:var(--muted);font-size:.7rem;letter-spacing:.04em;text-transform:uppercase">Auto</span>
    <label title="Carica automaticamente i risultati manuali salvati per questa società all'apertura">
      <input type="checkbox" id="opt-auto-manual" onchange="setAutoOpt('manual',this.checked)">
      Carica manuali
    </label>
    <label title="Applica automaticamente il filtro disciplina CdS all'apertura">
      <input type="checkbox" id="opt-auto-preset" onchange="setAutoOpt('preset',this.checked)">
      Filtro CdS
    </label>
  </div>

  <!-- Totals bar -->
  <div class="totbar">
    <div class="tstat"><span class="val" id="tot-pts">0</span><span class="lbl">Punti</span></div>
    <div class="tstat"><span class="val" id="tot-n">0</span><span class="lbl">Selezionati</span></div>
    <div class="tstat"><span class="val" id="tot-ev">0</span><span class="lbl">Gare</span></div>
    <span class="note-est" id="note-est">Inserisci i punti FIDAL per ogni risultato, poi usa Calcola Ottimale</span>
    <button class="btn-clr" onclick="clearAll()">✕ Svuota</button>
    <button class="btn-pdf" onclick="downloadCSV()">⬇ CSV</button>
    <button class="btn-pdf" onclick="printPDF()">⬇ Stampa / PDF</button>
    <button class="btn-opt" onclick="computeOptimal()">⚡ Calcola Ottimale</button>
  </div>
  <div id="calcola-err" style="display:none"></div>

  <div class="main">

    <!-- PROSPETTO -->
    <div class="card">
      <div class="card-head">
        <h2 id="pros-title">Prospetto Scheda</h2>
        <span class="badge-n" id="sel-n">0</span>
      </div>
      <!-- Avviso pareggi punteggio -->
      <div class="tie-panel" id="tie-panel" style="display:none"></div>
      <div style="overflow-x:auto">
        <table class="tbl">
          <thead><tr>
            <th>#</th><th>Tipo</th><th>Disciplina</th><th>Atleta/e</th><th>Anno</th>
            <th>Prest.</th><th>Piazz.</th><th>Città</th><th>Data</th>
            <th style="text-align:right">Punti FIDAL</th><th></th>
          </tr></thead>
          <tbody id="pros-body">
            <tr><td colspan="9" style="padding:2rem;text-align:center;color:var(--muted)">
              Clicca <strong>⚡ Calcola Ottimale</strong> o seleziona manualmente dalla tabella sotto.
            </td></tr>
          </tbody>
        </table>
      </div>
      <div style="padding:.45rem 1rem .65rem;display:flex;justify-content:flex-end;
                  gap:.75rem;align-items:center;border-top:1px solid var(--border)">
        <span style="font-size:.75rem;color:var(--muted)">Totale scheda:</span>
        <span class="grand-total" id="grand-total">0</span>
      </div>
    </div>

    <!-- TUTTI I RISULTATI -->
    <div class="card">
      <div class="card-head">
        <h2>Tutti i risultati disponibili</h2>
        <span class="badge-n" id="all-n">—</span>
      </div>

      <!-- Athlete tracker -->
      <div class="atl-tracker" id="atl-tracker"></div>

      <!-- Filters -->
      <div class="frow">
        <select id="f-type" onchange="renderAll()">
          <option value="">Tutti i tipi</option>
          <option value="corsa">Corsa</option>
          <option value="ostacoli">Ostacoli</option>
          <option value="salto">Salti</option>
          <option value="lancio">Lanci</option>
          <option value="staffetta">Staffetta</option>
        </select>
        <input type="text" id="f-name" placeholder="Cerca atleta…" oninput="renderAll()">
        <select id="f-ev-filter" onchange="renderAll()">
          <option value="">Tutte le discipline</option>
        </select>
        <span style="font-size:.72rem;color:var(--muted);margin-left:auto">
          🟢 Selezionato &nbsp;|&nbsp; 🟡 Parzialmente disponibile &nbsp;|&nbsp; 🔴 Bloccato
        </span>
      </div>

      <div style="overflow-x:auto">
        <table class="tbl">
          <thead><tr>
            <th></th>
            <th onclick="sortAll(0)">Tipo ⇅</th>
            <th onclick="sortAll(1)">Disciplina ⇅</th>
            <th onclick="sortAll(2)">Atleta ⇅</th>
            <th>Anno</th>
            <th onclick="sortAll(3)">Prest. ⇅</th>
            <th>Vento</th>
            <th onclick="sortAll(4)">Piazz. ⇅</th>
            <th>Città</th>
            <th>Data</th>
            <th onclick="sortAll(5)" style="text-align:right">Pt FIDAL ⇅</th>
            <th></th>
          </tr></thead>
          <tbody id="all-body"></tbody>
        </table>
      </div>
      <!-- Manual entry bar -->
      <div class="manual-bar">
        <button class="btn-add-manual" onclick="toggleManualForm()">➕ Aggiungi risultato manuale</button>
        <button class="btn-import-csv" onclick="openCsvModal()">📂 Importa da CSV</button>
        <span style="font-size:.72rem;color:var(--muted)">per staffette non presenti o gare mancanti</span>
      </div>
      <div id="manual-form" style="display:none">
        <div class="manual-form-box">
          <div class="mfg">
            <div class="fg-sm">
              <label>Gara *</label>
              <input id="m-ev" list="m-ev-list" placeholder="es. Staffetta 4×100" oninput="onManualEvInput()">
              <datalist id="m-ev-list"></datalist>
            </div>
            <div class="fg-sm">
              <label>Tipo</label>
              <select id="m-tipo">
                <option value="corsa">Corsa</option>
                <option value="ostacoli">Ostacoli</option>
                <option value="salto">Salto</option>
                <option value="lancio">Lancio</option>
                <option value="staffetta">Staffetta</option>
              </select>
            </div>
            <div class="fg-sm">
              <label>Prestazione *</label>
              <input id="m-perf" placeholder="es. 48.50 o 1:52.30">
            </div>
          </div>
          <div class="mfg2">
            <div class="fg-sm">
              <label>Atleta/e * <span style="font-weight:400;text-transform:none">(staffetta: nomi separati da virgola)</span></label>
              <input id="m-athl" placeholder="es. ROSSI L., BIANCHI M., VERDI G., NERI A.">
            </div>
            <div class="fg-sm">
              <label>Punti FIDAL</label>
              <input id="m-pts" type="number" min="0" placeholder="(lascia vuoto per inserire dopo)">
            </div>
          </div>
          <div class="mfg2">
            <div class="fg-sm">
              <label>Città</label>
              <input id="m-citta" placeholder="es. Brescia">
            </div>
            <div class="fg-sm">
              <label>Data</label>
              <input id="m-data" placeholder="es. 15/05/2026">
            </div>
          </div>
          <button class="btn-mok" onclick="submitManual()">✔ Aggiungi</button>
          <button class="btn-mcancel" onclick="toggleManualForm()">Annulla</button>
          <span id="manual-err" style="color:var(--red);font-size:.8rem;margin-left:.75rem"></span>
        </div>
      </div>

      <div class="legenda">
        <span>⚠ Max 2 risultati per gara (staffetta esclusa)</span>
        <span>⚠ Ogni atleta max 2 volte (staffetta conta come 1)</span>
        <span>⚠ Obbligatori: ≥2 lanci diversi + ≥2 salti diversi · min 10 gare</span>
      </div>
    </div>

  </div><!-- /main -->

</div><!-- /scr-tool -->

<!-- CSV Import Modal — fuori da tutti gli .screen, visibile da qualsiasi schermata -->
<div class="csv-overlay" id="csv-overlay" style="display:none" onclick="if(event.target===this)closeCsvModal()">
  <div class="csv-modal" role="dialog" aria-labelledby="csv-modal-title">
    <div class="csv-modal-head">
      <h3 id="csv-modal-title">Importa risultati da file CSV</h3>
      <button class="csv-modal-close" onclick="closeCsvModal()" title="Chiudi">&#x2715;</button>
    </div>
    <div class="csv-modal-body">

      <!-- nota contestuale: visibile solo quando aperto dal form screen -->
      <div class="csv-form-note" id="csv-form-note" style="display:none">
        <strong>Modalità manuale:</strong> i parametri compilati nel form (categoria, società, anno…) verranno usati per aprire la schermata di lavoro.
        Dopo l&apos;importazione si aprirà automaticamente il tool con i soli risultati caricati da file.
      </div>

      <div class="csv-section">
        <h4>Come funziona</h4>
        <p style="font-size:.82rem;margin:.3rem 0 .6rem">Prepara un file <strong>.csv</strong> con una riga di intestazione e una riga per ogni risultato da importare.
        I record validi vengono aggiunti come inserimenti manuali e salvati in modo persistente.
        Le righe con errori vengono segnalate ma <em>non</em> bloccano l&apos;importazione delle righe valide.</p>
      </div>

      <div class="csv-section">
        <h4>Colonne richieste e valori ammessi</h4>
        <table class="csv-col-table">
          <thead><tr><th>Colonna</th><th>Obbl.</th><th>Valori ammessi / formato</th><th>Esempio</th></tr></thead>
          <tbody>
            <tr><td><code>categoria</code></td><td class="required">SI</td><td>CF &bull; CM &bull; RF &bull; RM</td><td>CF</td></tr>
            <tr><td><code>gara</code></td><td class="required">SI</td><td>Nome disciplina dalla lista per la categoria (case-insensitive) &mdash; <button type="button" onclick="toggleDisciplineList()" style="background:none;border:none;color:var(--blue);cursor:pointer;font-size:.78rem;padding:0;text-decoration:underline" id="csv-disc-toggle">mostra lista ▾</button></td><td>300 piani</td></tr>
            <tr><td><code>tipo</code></td><td class="required">SI</td><td>corsa &bull; ostacoli &bull; salto &bull; lancio &bull; staffetta</td><td>corsa</td></tr>
            <tr><td><code>prestazione</code></td><td class="required">SI</td><td>Formato libero: secondi, m:ss.cc, metri</td><td>42.10 &nbsp;/&nbsp; 1:52.30 &nbsp;/&nbsp; 13.45</td></tr>
            <tr><td><code>atleta</code></td><td class="required">SI</td><td>Nome atleta. Per staffetta: nomi separati da <code>/</code> o <code>,</code></td><td>ROSSI L. / BIANCHI M. / VERDI G. / NERI A.</td></tr>
            <tr><td><code>punti</code></td><td class="optional">no</td><td>Intero &ge; 0. Lasciare vuoto se sconosciuto</td><td>638</td></tr>
            <tr><td><code>vento</code></td><td class="optional">no</td><td>es. +1.2, -0.5, 0.0</td><td>+0.8</td></tr>
            <tr><td><code>piazzamento</code></td><td class="optional">no</td><td>Numero intero</td><td>2</td></tr>
            <tr><td><code>citta</code></td><td class="optional">no</td><td>Testo libero</td><td>Brescia</td></tr>
            <tr><td><code>data</code></td><td class="optional">no</td><td>gg/mm/aaaa oppure gg/mm</td><td>18/04/2026</td></tr>
          </tbody>
        </table>
        <p style="font-size:.74rem;color:var(--muted);margin:.45rem 0 0">
          Le intestazioni delle colonne sono <strong>case-insensitive</strong> e gli spazi vengono ignorati.
          Il separatore deve essere la virgola (<code>,</code>). Valori con virgola devono essere tra doppi apici.
          Encoding supportati: UTF-8, UTF-8 con BOM, Latin-1.
        </p>

        <!-- Lista discipline collassabile -->
        <div id="csv-discipline-list" style="display:none;margin-top:.7rem">
          <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-bottom:.4rem">Discipline valide per categoria</div>
          <div id="csv-disc-content" style="font-size:.78rem;display:grid;grid-template-columns:1fr 1fr;gap:.5rem 1.5rem">
            <div style="color:var(--muted);font-style:italic">Caricamento…</div>
          </div>
        </div>
      </div>

      <div class="csv-section">
        <h4>Carica file</h4>
        <div class="csv-drop-zone" id="csv-drop-zone"
             onclick="document.getElementById('csv-file-input').click()"
             ondragover="event.preventDefault();this.classList.add('drag-over')"
             ondragleave="this.classList.remove('drag-over')"
             ondrop="csvHandleDrop(event)">
          <input type="file" id="csv-file-input" accept=".csv,text/csv" onchange="csvFileChosen(this.files[0])">
          <div class="csv-drop-label">Clicca oppure trascina qui il file CSV</div>
          <div class="csv-drop-sub">Solo file .csv &mdash; max 2 MB</div>
          <div class="csv-file-chosen" id="csv-file-chosen" style="display:none"></div>
        </div>
        <div class="csv-action-row">
          <button class="btn-csv-upload" id="btn-csv-upload" onclick="csvUpload()" disabled>Importa</button>
          <button class="btn-csv-template" onclick="csvDownloadTemplate()">Scarica template CSV</button>
          <span id="csv-upload-spinner" style="display:none;font-size:.8rem;color:var(--muted)">Caricamento...</span>
        </div>
        <div class="csv-result-box" id="csv-result-box" style="display:none"></div>
      </div>

    </div><!-- /csv-modal-body -->
  </div>
</div><!-- /csv-overlay -->

<script>
// ── COSTANTI ────────────────────────────────────────────
const LANCIO_EVS = new Set(['peso','martello','giavellotto','disco','lancio','vortex','palla']);
const SALTO_EVS  = new Set(['lungo','triplo','alto','asta','salto']);
const TYPE_LBL   = {corsa:'Corsa',ostacoli:'Ostacoli',salto:'Salto',lancio:'Lancio',staffetta:'Staffetta'};

// ── PROGRAMMI TECNICI CdS (per preset filtro gare) ──────
// Helper: vero se l'evento è un ostacolo (FIDAL usa sia "ostacoli" che "hs"/"Hs")
const _isOstac = e => e.includes('ostac') || e.includes(' hs') || e.includes('hs ') || e.startsWith('hs');

const CDS_PROGRAMS = {
  // Ragazzi/Ragazze: 60hs, 60, 1000, Marcia 2km, Alto, Lungo, Peso 2kg, Vortex, 4x100
  RM: ev => {
    const e = ev.toLowerCase();
    return (/(?<!\d)60(?!\d)/.test(e) && (e.includes('piani') || _isOstac(e))) ||
           (/(?<!\d)1000(?!\d)/.test(e) && !e.includes('3x') && !e.includes('3 x')) ||
           e.includes('marcia') || e.includes('in alto') || e.includes('in lungo') ||
           (e.includes('peso') && e.includes('2')) ||
           e.includes('vortex') ||
           (e.includes('staffetta') && /4\s*[xX]\s*100(?!0)/.test(e));
  },
  // Cadetti: 100hs, 80, 300hs, 300, 1000, 2000, 1200 siepi, Asta, Alto, Lungo, Triplo,
  //          Peso 4kg, Martello 4kg, Disco 1.5kg, Giavellotto 600g, 4x100, Marcia 5km
  CM: ev => {
    const e = ev.toLowerCase();
    return (e.includes('80') && e.includes('piani')) ||
           (/(?<!\d)100(?!\d)/.test(e) && _isOstac(e)) ||
           (e.includes('300') && (_isOstac(e) || e.includes('piani'))) ||
           (/(?<!\d)1000(?!\d)/.test(e) && !e.includes('3x') && !e.includes('3 x')) ||
           e.includes('2000') || e.includes('1200') ||
           e.includes('asta') || e.includes('in alto') || e.includes('in lungo') ||
           e.includes('triplo') ||
           (e.includes('peso') && e.includes('4')) ||
           e.includes('martello') || e.includes('disco') || e.includes('giavellott') ||
           (e.includes('staffetta') && /4\s*[xX]\s*100(?!0)/.test(e)) ||
           e.includes('marcia');
  },
};
CDS_PROGRAMS.RF = CDS_PROGRAMS.RM; // stesso programma tecnico dei Ragazzi
// Cadette: 80hs, 80, 300hs, 300, 1000, 2000, 1200 siepi, Asta, Alto, Lungo, Triplo,
//          Peso 3kg, Martello 3kg, Disco 1kg, Giavellotto 400g, Staffetta 4x100, Marcia 3km
CDS_PROGRAMS.CF = ev => {
  const e = ev.toLowerCase();
  return (e.includes('80') && (e.includes('piani') || _isOstac(e))) ||
         (e.includes('300') && (_isOstac(e) || e.includes('piani'))) ||
         (/(?<!\d)1000(?!\d)/.test(e) && !e.includes('3x') && !e.includes('3 x')) ||
         e.includes('2000') || e.includes('1200') ||
         e.includes('asta') || e.includes('in alto') || e.includes('in lungo') ||
         e.includes('triplo') ||
         e.includes('peso') || e.includes('martello') || e.includes('disco') ||
         e.includes('giavellott') ||
         (e.includes('staffetta') && /4\s*[xX]\s*100(?!0)/.test(e)) ||
         e.includes('marcia');
};

// ── VINCOLI PER CATEGORIA ────────────────────────────────
// nSel: risultati totali | minEv: gare minime distinte
// minLanci/minSalti: discipline obbligatorie | maxAthlInd: max gare individuali per atleta
const CONSTRAINTS = {
  default: { nSel:13, minEv:10, minLanci:2, minSalti:2, maxAthlInd:2 },
  RM:      { nSel:8,  minEv:6,  minLanci:1, minSalti:1, maxAthlInd:1 },
  RF:      { nSel:8,  minEv:6,  minLanci:1, minSalti:1, maxAthlInd:1 },
};
function getC(){ return CONSTRAINTS[currentCategoria] || CONSTRAINTS.default; }

// ── STATO ───────────────────────────────────────────────
let ALL = [], selectedIds = new Set(), userPts = {}, staffAnalysis = [], excludedEvs = new Set(), topCombinations = [];
let _societiesMeta = {}; // pre-calcolato dal build, caricato con la proiezione
let _classificaRanked = null; // ultimo ranked salvato da _renderClassifica, usato per export
let _tabelleCache = {};   // cache tabelle punteggi: categoria → {gara → {perf → pts}}

async function _getTabellaCategoria(cat) {
  if (_tabelleCache[cat]) return _tabelleCache[cat];
  try {
    const r = await fetch(`/api/tabelle?categoria=${cat}`);
    const j = await r.json();
    if (j.ok) _tabelleCache[cat] = j.tabelle[cat] || {};
  } catch(e) { _tabelleCache[cat] = {}; }
  return _tabelleCache[cat] || {};
}
let currentCategoria = '', currentAnno = 2026, savedManualEntries = [];
let unavailableAthletes = new Set(), minDateFilter = null;
let _fidalConnected = false, _fidalCheckTimer = null;
let _currentProiezioneP = null; // parametri dell'ultima proiezione caricata
let currentSocieta = '';        // codice società corrente (stringa vuota in proiezione)
// Impostazioni persistenti (localStorage)
let autoLoadManual = localStorage.getItem('cds_autoLoadManual') !== 'false';
let autoPresetCds  = localStorage.getItem('cds_autoPresetCds')  !== 'false';
let societaList = [];
let isProiezione = false;

function athleteDisplay(r, short=false){
  if (r.isStaffetta) return (r.staffAthl||[r.athlete]).join(' / ');
  if (r.athlete_url && !short)
    return `<a class="athl-link" href="${r.athlete_url}" target="_blank" rel="noopener">${r.athlete}</a>`;
  return r.athlete;
}
let sortCol = -1, sortAsc = true;

function isLancio(ev){
  // Keyword ha priorità (override per eventi FIDAL classificati erroneamente)
  if ([...LANCIO_EVS].some(k=>ev.toLowerCase().includes(k))) return true;
  const r=activeAll().find(x=>x.ev===ev);
  return !!(r && r.type==='lancio');
}
function isSalto(ev){
  if ([...SALTO_EVS].some(k=>ev.toLowerCase().includes(k))) return true;
  const r=activeAll().find(x=>x.ev===ev);
  return !!(r && r.type==='salto');
}
function pts(r){ return userPts[r.id] !== undefined ? userPts[r.id] : r.pts; }
function activeAll(){
  return ALL.filter(r=>{
    if (excludedEvs.has(r.ev)) return false;
    const athls = r.isStaffetta ? (r.staffAthl||[r.athlete]) : [r.athlete];
    if (athls.some(a=>unavailableAthletes.has(a))) return false;
    if (minDateFilter && r.data){
      const d = parseResultDate(r.data);
      if (d && d < minDateFilter) return false;
    }
    // In proiezione: escludi risultati senza punteggio tabella
    if (isProiezione && !r.pts_ok && userPts[r.id]===undefined) return false;
    return true;
  });
}

// ── CATEGORIA PICKLIST ──────────────────────────────────
// Limitato alle categorie con tabelle punteggi FIDAL disponibili
const CATS={
  F:[{v:'CF',l:'Cadette (CF)'},{v:'RF',l:'Ragazze (RF)'}],
  M:[{v:'RM',l:'Ragazzi (RM)'},{v:'CM',l:'Cadetti (CM)'}],
};
function updateCatOptions(){
  const sesso=document.getElementById('f-sesso').value;
  const sel=document.getElementById('f-cat');
  const prev=sel.value;
  sel.innerHTML=CATS[sesso].map(c=>`<option value="${c.v}">${c.l}</option>`).join('');
  if ([...sel.options].some(o=>o.value===prev)) sel.value=prev;
  updateUrlPreview();
}

// ── URL PREVIEW ─────────────────────────────────────────
function updateUrlPreview(){
  const p = getFormParams();
  const q = new URLSearchParams({...p, gara:'0', tipologia_estrazione:'2', submit:'Invia'});
  document.getElementById('url-preview').textContent =
    'https://www.fidal.it/graduatorie.php?' + q.toString();
}
['f-anno','f-tipo','f-sesso','f-cat','f-reg','f-naz','f-vento','f-limite','f-societa']
  .forEach(id => document.getElementById(id).addEventListener('change', updateUrlPreview));
document.getElementById('f-societa').addEventListener('input', updateUrlPreview);
// Ricarica lista società quando cambia la regione
document.getElementById('f-reg').addEventListener('change', () => {
  document.getElementById('f-societa-name').value = '';
  loadSocieta(document.getElementById('f-reg').value);
});
updateCatOptions(); // popola la picklist categorie al caricamento
loadSocieta(document.getElementById('f-reg').value); // carica società per la regione default
setTimeout(checkFidalConnection, 0); // verifica connessione FIDAL dopo il parsing dello script

// ── RICERCA SOCIETÀ ──────────────────────────────────────
async function loadSocieta(regione){
  const statusEl = document.getElementById('societa-status');
  const dl       = document.getElementById('societa-datalist');
  societaList = [];
  dl.innerHTML = '';
  statusEl.textContent = 'Caricamento…';
  try {
    const resp = await fetch(`/api/societa?regione=${encodeURIComponent(regione)}`);
    const json = await resp.json();
    if (!json.ok) throw new Error(json.error);
    societaList = json.data;
    dl.innerHTML = societaList.map(s =>
      `<option value="${s.nome.replace(/"/g,'&quot;')}">`
    ).join('');
    statusEl.textContent = `${societaList.length} società`;
  } catch(e) {
    statusEl.textContent = `⚠ ${e.message}`;
  }
}

function onSocietaNomeInput(){
  const nome  = document.getElementById('f-societa-name').value.trim();
  const found = societaList.find(s => s.nome === nome);
  if (found){
    document.getElementById('f-societa').value = found.cod;
    updateUrlPreview();
  }
}

function onSocietaCodiceInput(){
  document.getElementById('f-societa-name').value = '';
  updateUrlPreview();
}

function getFormParams(){
  return {
    anno: document.getElementById('f-anno').value,
    tipo_attivita: document.getElementById('f-tipo').value,
    sesso: document.getElementById('f-sesso').value,
    categoria: document.getElementById('f-cat').value,
    regione: document.getElementById('f-reg').value,
    nazionalita: document.getElementById('f-naz').value,
    vento: document.getElementById('f-vento').value,
    limite: document.getElementById('f-limite').value,
    societa: document.getElementById('f-societa').value.toUpperCase().trim(),
  };
}

// ── FIDAL CONNECTION STATUS ──────────────────────────────
function _setFidalStatus(state, msg){
  const led = document.getElementById('fidal-led');
  const txt = document.getElementById('fidal-status-txt');
  const retry = document.getElementById('fidal-retry-btn');
  if (!led) return;
  led.className = 'fidal-led ' + state;
  txt.textContent = msg;
  const connected = state === 'ok';
  _fidalConnected = connected;
  retry.style.display = state === 'error' ? '' : 'none';
  ['btn-fetch-fidal','btn-fetch-proj','btn-build-proj'].forEach(id => {
    const btn = document.getElementById(id);
    if (btn) btn.disabled = !connected;
  });
}

async function checkFidalConnection(){
  try {
    _setFidalStatus('checking', 'Verifica connessione FIDAL…');
    const resp = await fetch('/api/fidal_status', {cache:'no-store'});
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    if (data.ok){
      _setFidalStatus('ok', `Connessione FIDAL attiva · ${data.latency_ms} ms`);
    } else {
      _setFidalStatus('error', `FIDAL non raggiungibile — ${(data.error||'?').slice(0,60)}`);
    }
  } catch(e){
    try { _setFidalStatus('error', `Errore — ${(e.message||'?').slice(0,60)}`); } catch(_){}
  }
  clearTimeout(_fidalCheckTimer);
  _fidalCheckTimer = setTimeout(checkFidalConnection, 60000);
}

// ── FETCH DATA ───────────────────────────────────────────
async function fetchData(){
  const errEl = document.getElementById('form-error');
  errEl.style.display='none';
  const p = getFormParams();
  if (!p.societa){ errEl.style.display='block'; errEl.textContent='Inserisci il codice società.'; return; }

  document.getElementById('loading').classList.remove('hidden');
  try {
    const resp = await fetch('/api/fetch?' + new URLSearchParams(p));
    const json = await resp.json();
    if (!json.ok) throw new Error(json.error);

    ALL = json.data;
    selectedIds.clear(); userPts = {}; staffAnalysis = []; excludedEvs = new Set();
    unavailableAthletes = new Set(); minDateFilter = null; isProiezione = false;
    document.getElementById('staff-panel').style.display='none';

    // Segna miglior prestazione per disciplina
    computeBests();

    // Risolvi nomi staffette
    ALL.filter(r=>r.isStaffetta).forEach(r=>{
      r.staffAthl = resolveStaffettaAthletes(r.rawStaff);
    });

    // Popola UI
    setupToolScreen(p);
    show('scr-tool');
    await _applyAutoOpts();
  } catch(e){
    errEl.style.display='block'; errEl.textContent='Errore: ' + e.message;
    // Aggiorna il LED: potrebbe essere un problema di connessione FIDAL
    _setFidalStatus('error', `Errore comunicazione FIDAL — ${e.message.slice(0,80)}`);
    document.getElementById('fidal-retry-btn').style.display='';
  } finally {
    document.getElementById('loading').classList.add('hidden');
  }
}

function _setLoadingMsg(msg){ const el=document.getElementById('loading-msg'); if(el) el.textContent=msg; }

// ── BUILD DB REGIONALE (SSE) ─────────────────────────────
let _buildES = null, _buildCompete = 0;

function startBuildProiezione(){
  const p = getFormParams();
  const area = document.getElementById('build-area');
  const fill = document.getElementById('build-fill');
  const status = document.getElementById('build-status');
  const log = document.getElementById('build-log');

  area.style.display = '';
  fill.style.width = '0%';
  status.textContent = 'Connessione…';
  log.innerHTML = '';

  if (_buildES) _buildES.close();

  const params = new URLSearchParams({
    anno: p.anno, tipo_attivita: p.tipo_attivita, sesso: p.sesso,
    categoria: p.categoria, regione: p.regione,
    nazionalita: p.nazionalita, vento: p.vento,
  });

  _buildES = new EventSource('/api/proiezione/build?' + params);

  _buildES.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch(_){ return; }

    if (msg.type === 'status'){
      status.textContent = msg.msg;
    }
    else if (msg.type === 'total'){
      status.textContent = `0/${msg.n} società analizzate · 0 con ${p.categoria}`;
      _buildCompete = 0;
    }
    else if (msg.type === 'found' || msg.type === 'skip' || msg.type === 'unchanged'){
      const pct = Math.round(msg.done / msg.total * 100);
      fill.style.width = pct + '%';
      if (msg.type === 'found' && msg.can_compete) _buildCompete++;
      const unch = msg.unchanged || 0;
      status.textContent = `${msg.done}/${msg.total} analizzate · ${msg.found} aggiornate · ${unch} invariate · 🏆 ${_buildCompete} competitive`;
      if (msg.type === 'found'){
        const badge = msg.can_compete ? '🏆' : '⚠';
        const hint  = msg.can_compete
          ? `${msg.n_ev} gare · ${msg.n_la} lanci · ${msg.n_sa} salti`
          : `solo ${msg.n_ev} gare (min 10), ${msg.n_la} lanci, ${msg.n_sa} salti`;
        const optStr = (msg.can_compete && msg.optimal_score > 0) ? ` · ottimale Σ ${msg.optimal_score}` : '';
        log.innerHTML += `<div>${badge} ${msg.soc} — ${msg.n_athl} atlet${p.sesso==='F'?'e':'i'} · ${hint} · Σ ${msg.total_pts}pt${optStr}</div>`;
        log.scrollTop = log.scrollHeight;
      } else if (msg.type === 'unchanged'){
        log.innerHTML += `<div style="color:var(--muted)">= ${msg.soc} — invariata (${msg.num_gare} gare · Σ ${msg.total_pts}pt)</div>`;
        log.scrollTop = log.scrollHeight;
      }
    }
    else if (msg.type === 'done'){
      _buildES.close(); _buildES = null;
      fill.style.width = '100%';
      const d = new Date(msg.updated_at);
      const fmt = d.toLocaleString('it-IT',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
      const unch = msg.unchanged_societies || 0;
      status.textContent = `✅ Completato — ${msg.found_societies} aggiornate · ${unch} invariate (🏆 ${_buildCompete} competitive) · ${msg.n_results} prestazioni · ${fmt}`;
      const logFile = msg.log_path ? `<br><span style="font-size:.8em;color:var(--muted)">📄 Log: ${msg.log_path}</span>` : '';
      log.innerHTML += `<div style="font-weight:600;color:var(--green)">Dati salvati in cache. Avvio proiezione...${logFile}</div>`;
      log.scrollTop = log.scrollHeight;
      setTimeout(() => {
        area.style.display = 'none';
        fetchProiezione(false);
      }, 2500);
    }
    else if (msg.type === 'error'){
      _buildES.close(); _buildES = null;
      status.textContent = `⚠ ${msg.msg}`;
      fill.style.width = '0%';
    }
  };

  _buildES.onerror = () => {
    if (_buildES){ _buildES.close(); _buildES = null; }
    status.textContent = '⚠ Connessione interrotta — riprova';
  };
}

function cancelBuild(){
  if (_buildES){ _buildES.close(); _buildES = null; }
  document.getElementById('build-area').style.display = 'none';
}

function _proiezioneParams(p, force){
  return new URLSearchParams({
    anno: p.anno, tipo_attivita: p.tipo_attivita, sesso: p.sesso,
    categoria: p.categoria, regione: p.regione,
    nazionalita: p.nazionalita, vento: p.vento,
    force: force ? '1' : '0',
  });
}

function _applyProiezioneData(json, p){
  ALL = json.data;
  _societiesMeta = json.societies_meta || {};
  _currentProiezioneP = p;
  selectedIds.clear(); userPts = {}; staffAnalysis = []; excludedEvs = new Set();
  unavailableAthletes = new Set(); minDateFilter = null; isProiezione = true;
  document.getElementById('staff-panel').style.display='none';
  computeBests();
  ALL.filter(r=>r.isStaffetta).forEach(r=>{ r.staffAthl = resolveStaffettaAthletes(r.rawStaff); });
  _pruneForProiezione(10); // top-10 per evento: include atleti fino al 10° posto regionale
}

function _setProiezioneBannerTs(json){
  if (!json.updated_at) return;
  const d = new Date(json.updated_at);
  const fmt = d.toLocaleString('it-IT',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
  document.getElementById('proiezione-ts').textContent =
    json.from_cache ? `📦 cache · ${fmt}` : `🌐 aggiornato · ${fmt}`;
}

async function fetchProiezione(forceRefresh=false){
  // Refresh da scr-classifica già attiva
  if (forceRefresh && document.getElementById('scr-classifica').classList.contains('active')){
    _bgRefreshProiezione(); return;
  }

  const errEl = document.getElementById('form-error');
  errEl.style.display='none';
  const p = getFormParams();

  _setLoadingMsg('Caricamento proiezione regionale…');
  document.getElementById('loading').classList.remove('hidden');
  try {
    const resp = await fetch('/api/proiezione?' + _proiezioneParams(p, forceRefresh));
    const json = await resp.json();
    if (!json.ok) throw new Error(json.error);

    _setLoadingMsg('Elaborazione classifica…');
    _applyProiezioneData(json, p);

    // Intestazione schermata classifica
    const catLbl = `CdS ${p.categoria} · ${p.tipo_attivita==='P'?'Outdoor':'Indoor'} ${p.anno} · ${p.regione}`;
    document.getElementById('clas-screen-title').textContent = `Proiezione Regionale — ${p.regione}`;
    document.getElementById('clas-screen-sub').textContent = catLbl;
    if (json.updated_at){
      const d=new Date(json.updated_at);
      const fmt=d.toLocaleString('it-IT',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
      document.getElementById('clas-screen-ts').textContent = json.from_cache ? `📦 ${fmt}` : `🌐 ${fmt}`;
    }

    show('scr-classifica');
    await computeClassifica();
  } catch(e){
    errEl.style.display='block'; errEl.textContent='Errore: ' + e.message;
  } finally {
    document.getElementById('loading').classList.add('hidden');
    _setLoadingMsg('Caricamento dati FIDAL…');
  }
}

async function _bgRefreshProiezione(){
  const ts  = document.getElementById('clas-screen-ts');
  const p   = getFormParams();
  if (ts) ts.textContent = '<span class="bspin">⟳</span> Aggiornamento…';

  try {
    const resp = await fetch('/api/proiezione?' + _proiezioneParams(p, true));
    const json = await resp.json();
    if (!json.ok) throw new Error(json.error);
    _applyProiezioneData(json, p);
    if (json.updated_at){
      const d=new Date(json.updated_at);
      const fmt=d.toLocaleString('it-IT',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
      if (ts) ts.textContent = `🌐 ${fmt}`;
    }
    await computeClassifica();
  } catch(e){
    if (ts) ts.textContent = `⚠ ${e.message}`;
  }
}

function setupToolScreen(p){
  currentCategoria = p.categoria;
  currentSocieta   = p.societa || '';
  currentAnno = +p.anno || new Date().getFullYear();
  document.getElementById('manual-reload-bar').style.display='none';
  savedManualEntries=[];
  checkSavedManualEntries(p.categoria, p.societa || '');

  // Reset filtri globali
  document.getElementById('date-filter-input').value='';
  document.getElementById('date-filter-clear').style.display='none';
  document.getElementById('date-filter-count').textContent='';
  renderUnavailPanel();

  const cat = document.getElementById('f-cat');
  const catLabel = cat.options[cat.selectedIndex].text;
  const sesso = p.sesso==='F'?'Femminile':'Maschile';
  const proBar = document.getElementById('proiezione-bar');
  const btnClas = document.getElementById('btn-to-classifica');
  if (isProiezione){
    proBar.style.display='';
    if (btnClas) btnClas.style.display='none';
    document.getElementById('tool-title').textContent = `Proiezione Regionale — ${p.regione}`;
  } else {
    proBar.style.display='none';
    // Mostra "Classifica" solo se c'è una proiezione disponibile in memoria
    if (btnClas) btnClas.style.display = _currentProiezioneP ? '' : 'none';
    document.getElementById('tool-title').textContent = 'Graduatorie — ' + (p.societa_nome || ('Soc. ' + p.societa));
  }
  document.getElementById('tool-sub').textContent =
    `CdS ${catLabel} · ${p.tipo_attivita==='P'?'Outdoor':'Indoor'} ${p.anno} · ${p.regione}`;
  document.getElementById('tag-cat').textContent = catLabel;

  // Popola filtro discipline
  const evSel = document.getElementById('f-ev-filter');
  evSel.innerHTML = '<option value="">Tutte le discipline</option>';
  [...new Set(ALL.map(r=>r.ev))].forEach(e=>{
    const o=document.createElement('option'); o.value=e; o.textContent=e; evSel.appendChild(o);
  });

  // Staffetta panel
  const staffette = ALL.filter(r=>r.isStaffetta);
  document.getElementById('staff-panel').style.display = staffette.length ? '' : 'none';

  // Sincronizza i checkbox delle opzioni automatiche
  const cbManual = document.getElementById('opt-auto-manual');
  const cbPreset = document.getElementById('opt-auto-preset');
  if (cbManual) cbManual.checked = autoLoadManual;
  if (cbPreset) cbPreset.checked = autoPresetCds;

  buildEvFilterPanel();
  updateConstraints(); renderAll(); renderAthleteTracker();
}

function setAutoOpt(which, val){
  if (which === 'manual'){ autoLoadManual = val; localStorage.setItem('cds_autoLoadManual', val); }
  if (which === 'preset') { autoPresetCds  = val; localStorage.setItem('cds_autoPresetCds',  val); }
}

async function _applyAutoOpts(){
  // Applica preset CdS se abilitato (sincrono, prima di caricare i manuali)
  if (autoPresetCds && CDS_PROGRAMS[currentCategoria]) applyPresetCds();
  // Carica manuali in silenzio se abilitato
  if (autoLoadManual) await _ensureManualEntries();
}

async function _triggerSocReoptimize(soc_cod, categoria){
  // Ricalcola l'ottimale nel JSON della proiezione cached dopo un cambio di manuali.
  // Operazione in background: non blocca la UI, mostra solo una notifica breve.
  if (!soc_cod || !categoria || !_currentProiezioneP) return;
  const noteEl = document.getElementById('note-est');
  const prev = noteEl ? noteEl.textContent : '';
  if (noteEl) setNoteEst('⟳ Aggiornamento ottimale nella proiezione regionale…');
  try {
    const resp = await fetch('/api/reoptimize_soc', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        soc_cod,
        categoria,
        anno:          _currentProiezioneP.anno,
        tipo_attivita: _currentProiezioneP.tipo_attivita,
        sesso:         _currentProiezioneP.sesso,
        regione:       _currentProiezioneP.regione,
      }),
    });
    const data = await resp.json();
    if (data.ok && _societiesMeta[soc_cod]) {
      // Aggiorna la meta in memoria così la classifica riflette il nuovo punteggio
      if (!_societiesMeta[soc_cod].optimal) _societiesMeta[soc_cod].optimal = {};
      _societiesMeta[soc_cod].optimal.score = data.score;
      _societiesMeta[soc_cod].manual_count  = data.manual_count;
    }
    if (noteEl) setNoteEst(data.ok
      ? `✓ Proiezione aggiornata — nuovo ottimale: ${(data.score||0).toLocaleString('it')} pt`
      : `⚠ Aggiornamento proiezione non riuscito: ${data.error||'?'}`
    );
  } catch(e){
    if (noteEl) setNoteEst('⚠ Aggiornamento proiezione non riuscito');
  }
  // Ripristina il messaggio precedente dopo 4s
  setTimeout(()=>{ if (noteEl) setNoteEst(''); }, 4000);
}

// ── MIGLIORI PRESTAZIONI ─────────────────────────────────
function parsePerf(perf){
  perf = perf.trim();
  if (perf.includes(':')){
    const p = perf.split(':').map(Number);
    return p.length===2 ? p[0]*60+p[1] : p[0]*3600+p[1]*60+p[2];
  }
  return parseFloat(perf.replace(',','.'));
}

function computeBests(){
  ALL.forEach(r=>r.isBest=false);
  const byEvent = {};
  activeAll().forEach(r=>{ if(!byEvent[r.ev]) byEvent[r.ev]=[]; byEvent[r.ev].push(r); });
  for (const ers of Object.values(byEvent)){
    if (ers[0].isStaffetta){ ers.forEach(r=>r.isBest=true); continue; }
    const isTime = !isLancio(ers[0].ev) && !isSalto(ers[0].ev);
    const parsed = ers.map(r=>({r, p:parsePerf(r.perf)})).filter(x=>!isNaN(x.p)&&x.p>0);
    if (!parsed.length) continue;
    const bestVal = isTime ? Math.min(...parsed.map(x=>x.p)) : Math.max(...parsed.map(x=>x.p));
    parsed.filter(x=>x.p===bestVal).forEach(x=>x.r.isBest=true);
  }
}

// ── NAME RESOLUTION (staffette) ─────────────────────────
function resolveStaffettaAthletes(rawStr){
  // FIDAL usa sia "COGNOME N. / COGNOME N." che "cognome-cognome-cognome"
  const byCS = rawStr.split(/[,\/]/).map(s=>s.trim()).filter(Boolean);
  const parts = byCS.length > 1 ? byCS : rawStr.split('-').map(s=>s.trim()).filter(Boolean);
  const indiv = activeAll().filter(r=>!r.isStaffetta);
  return parts.map(part=>{
    const cleaned = part.replace(/\s+[A-Z]{2}\s*$/, '').trim();
    // Pattern "COGNOME I." o "COGNOME I"
    const m = cleaned.match(/^([A-Z][A-Z'\-]+(?:\s+[A-Z][A-Z'\-]+)*)\s+([A-Z])\.?$/i);
    if (m) {
      const [,sur,ini] = m;
      const found = indiv.find(r=>{
        const w=r.athlete.split(/\s+/);
        return w[0].toUpperCase()===sur.toUpperCase() && w.length>1 && w[1][0].toUpperCase()===ini.toUpperCase();
      });
      if (found) return found.athlete;
      return `${sur.toUpperCase()} ${ini.toUpperCase()}.`;
    }
    // Fallback: solo cognome (formato FIDAL dash)
    const surOnly = cleaned.toUpperCase().trim();
    if (!/\s/.test(surOnly) && surOnly.length > 1) {
      const found = indiv.find(r => r.athlete.split(/\s+/)[0].toUpperCase() === surOnly);
      if (found) return found.athlete;
    }
    return cleaned || null;
  }).filter(Boolean);
}

// ── VALIDATION ───────────────────────────────────────────
function validate(){
  const C = getC();
  const sel = ALL.filter(r=>selectedIds.has(r.id));
  const evCount={}, atlCount={}, atlIndCount={}, lancioSet=new Set(), saltoSet=new Set();
  sel.forEach(r=>{
    if (!r.isStaffetta) evCount[r.ev]=(evCount[r.ev]||0)+1;
    const athls = r.isStaffetta ? r.staffAthl : [r.athlete];
    athls.forEach(a=>atlCount[a]=(atlCount[a]||0)+1);
    if (!r.isStaffetta) athls.forEach(a=>atlIndCount[a]=(atlIndCount[a]||0)+1);
    if (r.type==='lancio'||isLancio(r.ev)) lancioSet.add(r.ev);
    if (r.type==='salto' ||isSalto(r.ev))  saltoSet.add(r.ev);
  });
  const nEv = Object.keys(evCount).length + (sel.some(r=>r.isStaffetta)?1:0);
  return {
    nSel:sel.length, nEv, nLanci:lancioSet.size, nSalti:saltoSet.size,
    evOk: Object.values(evCount).every(v=>v<=2),
    atlOk: Object.values(atlCount).every(v=>v<=2) &&
           Object.values(atlIndCount).every(v=>v<=C.maxAthlInd),
    sel, evCount, atlCount, atlIndCount
  };
}

// ── STATUS PER RIGA (per colori nella tabella) ───────────
function rowStatus(r){
  const C = getC();
  const v = validate();
  const inSel = selectedIds.has(r.id);
  if (inSel) return 'sel';

  const athls = r.isStaffetta ? (r.staffAthl||[]) : [r.athlete];
  const atlBlocked = r.isStaffetta
    ? athls.some(a=>(v.atlCount[a]||0)>=2)
    : athls.some(a=>(v.atlIndCount[a]||0)>=C.maxAthlInd || (v.atlCount[a]||0)>=2);
  const evBlocked  = !r.isStaffetta && (v.evCount[r.ev]||0)>=2;
  const atLimit    = v.nSel >= C.nSel;

  if (atlBlocked || evBlocked || atLimit) return 'block';
  // warn se atleta è all'ultimo slot individuale disponibile
  const warnInd = !r.isStaffetta && athls.some(a=>(v.atlIndCount[a]||0)===C.maxAthlInd-1);
  const warnTot = athls.some(a=>(v.atlCount[a]||0)===1 && !warnInd);
  if (warnInd || warnTot) return 'warn';
  return 'free';
}

function statusIcon(s){
  return {sel:'✅',free:'➕',warn:'🟡',block:'🔴'}[s]||'';
}
function statusTitle(s,r){
  const C = getC();
  if (s==='sel') return 'Clicca per rimuovere';
  if (s==='block'){
    const v=validate();
    const athls=r.isStaffetta?(r.staffAthl||[]):[r.athlete];
    if (!r.isStaffetta && athls.some(a=>(v.atlIndCount[a]||0)>=C.maxAthlInd))
      return `🔴 Atleta già in ${C.maxAthlInd} gara${C.maxAthlInd>1?'':'individuale'}`;
    if (athls.some(a=>(v.atlCount[a]||0)>=2)) return '🔴 Atleta già selezionata 2 volte';
    if (!r.isStaffetta&&(v.evCount[r.ev]||0)>=2) return '🔴 Gara già con 2 risultati';
    if (v.nSel>=C.nSel) return `🔴 Già ${C.nSel} risultati selezionati`;
    return '🔴 Non aggiungibile';
  }
  if (s==='warn'){
    const v=validate();
    const athls=r.isStaffetta?(r.staffAthl||[]):[r.athlete];
    const used=athls.find(a=>(v.atlCount[a]||0)===1);
    return `🟡 ${used}: ultima occorrenza disponibile`;
  }
  return 'Clicca per aggiungere';
}

// ── TOGGLE SELECT ────────────────────────────────────────
function toggleSelect(id){
  const r = ALL.find(x=>x.id===id);
  if (!r) return;

  if (selectedIds.has(id)){
    selectedIds.delete(id);
  } else {
    const C = getC();
    const v = validate();
    const athls = r.isStaffetta ? (r.staffAthl||[]) : [r.athlete];
    if (v.nSel>=C.nSel){ alert(`Hai già ${C.nSel} risultati. Rimuovine uno prima.`); return; }
    if (!r.isStaffetta && athls.some(a=>(v.atlIndCount[a]||0)>=C.maxAthlInd)){
      alert(`⚠ Atleta già in ${C.maxAthlInd} gara${C.maxAthlInd>1?' individuale':''}!`); return;
    }
    if (athls.some(a=>(v.atlCount[a]||0)>=2)){ alert('⚠ Atleta già presente 2 volte!'); return; }
    if (!r.isStaffetta && (v.evCount[r.ev]||0)>=2){ alert('⚠ Gara già con 2 risultati!'); return; }
    selectedIds.add(id);
  }
  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
}

function clearAll(){ selectedIds.clear(); staffAnalysis=[]; topCombinations=[];
  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
  document.getElementById('staff-cards').innerHTML='';
  document.getElementById('staff-panel').style.display='none'; }

function goBack(){ show('scr-form'); }

async function goToClassifica(){
  if (!_currentProiezioneP) return;
  // Ricarica proiezione dalla cache (veloce, no fetch FIDAL) e mostra la classifica
  document.getElementById('loading').classList.remove('hidden');
  _setLoadingMsg('Caricamento proiezione…');
  try {
    const resp = await fetch('/api/proiezione?' + _proiezioneParams(_currentProiezioneP, false));
    const json = await resp.json();
    if (!json.ok) throw new Error(json.error||'Errore proiezione');
    _applyProiezioneData(json, _currentProiezioneP);
    setupToolScreen(_currentProiezioneP);
    show('scr-tool');
    buildEvFilterPanel();
    renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
    // Apre direttamente la classifica
    computeClassifica();
    setTimeout(()=>{
      const el = document.getElementById('clas-panel');
      if (el) el.scrollIntoView({behavior:'smooth', block:'start'});
    }, 150);
  } catch(e){
    alert('Errore: ' + e.message);
  } finally {
    document.getElementById('loading').classList.add('hidden');
  }
}
function show(id){ document.querySelectorAll('.screen').forEach(s=>s.classList.remove('active'));
  document.getElementById(id).classList.add('active'); }

// ── RENDER CONSTRAINTS ────────────────────────────────────
function updateConstraints(){
  const C=getC();
  const v=validate();
  const nDbl=Math.max(0,C.nSel-v.nEv);
  document.getElementById('c-n-t').textContent=`${v.nSel}/${C.nSel} risultati`;
  document.getElementById('c-n').className='cbox '+(v.nSel===C.nSel?'ok':v.nSel>0?'warn':'err');
  document.getElementById('c-ev-t').textContent=`${v.nEv}/${C.minEv} gare (${nDbl} doppiate)`;
  document.getElementById('c-ev').className='cbox '+(v.nEv>=C.minEv?'ok':v.nEv>=Math.ceil(C.minEv*.7)?'warn':'err');
  document.getElementById('c-la-t').textContent=`${v.nLanci}/${C.minLanci} lanci`;
  document.getElementById('c-la').className='cbox '+(v.nLanci>=C.minLanci?'ok':v.nLanci>0?'warn':'err');
  document.getElementById('c-sa-t').textContent=`${v.nSalti}/${C.minSalti} salti`;
  document.getElementById('c-sa').className='cbox '+(v.nSalti>=C.minSalti?'ok':v.nSalti>0?'warn':'err');
  document.getElementById('c-at-t').textContent=v.evOk&&v.atlOk?'Vincoli OK':'⚠ Violazione!';
  document.getElementById('c-at').className='cbox '+(v.evOk&&v.atlOk?'ok':'err');

  const total=v.sel.reduce((s,r)=>s+pts(r),0);
  document.getElementById('tot-pts').textContent=total.toLocaleString('it');
  document.getElementById('tot-n').textContent=v.nSel;
  document.getElementById('tot-ev').textContent=v.nEv;
  document.getElementById('tag-tot').textContent=`Tot. ${total.toLocaleString('it')} pt`;
  document.getElementById('grand-total').textContent=total.toLocaleString('it');
  document.getElementById('sel-n').textContent=v.nSel;

  // Auto-clear stale optimizer error when selection is fully valid
  const allOk = v.nSel===C.nSel && v.nEv>=C.minEv && v.nLanci>=C.minLanci && v.nSalti>=C.minSalti && v.evOk && v.atlOk;
  if (allOk) setNoteEst('');
}

// ── RENDER ATHLETE TRACKER ────────────────────────────────
function renderAthleteTracker(){
  const v=validate();
  const tracker=document.getElementById('atl-tracker');
  if (!Object.keys(v.atlCount).length){ tracker.innerHTML=''; return; }
  const C=getC();
  tracker.innerHTML = Object.entries(v.atlCount).sort((a,b)=>b[1]-a[1]).map(([a,c])=>{
    const indC=v.atlIndCount[a]||0;
    const full=c>=2||indC>=C.maxAthlInd;
    const cls = full?'full':c===1?'half':'free';
    // Mostra il conteggio totale (che determina il colore) + dettaglio individuale
    const label = c>=2 ? `${c}/2` : `${c}/2 · ${indC} ind`;
    return `<span class="atl-chip ${cls}">${a} <span class="atl-cnt">${label}</span></span>`;
  }).join('');
}

// ── RENDER PROSPETTO ──────────────────────────────────────
function renderProspetto(){
  const C=getC();
  const sel=ALL.filter(r=>selectedIds.has(r.id)).sort((a,b)=>pts(b)-pts(a));
  const ec={};
  sel.forEach(r=>ec[r.ev]=(ec[r.ev]||0)+1);

  // Aggiorna titolo dinamico
  document.getElementById('pros-title').textContent=`Prospetto Scheda — ${C.nSel} risultati`;

  // Calcola pareggi: risultati NON selezionati con stesso punteggio nella stessa gara
  const tieMap={};
  sel.filter(r=>!r.isStaffetta).forEach(r=>{
    const p=pts(r);
    if (!p) return;
    const alts=activeAll().filter(x=>x.ev===r.ev&&!x.isStaffetta&&!selectedIds.has(x.id)&&pts(x)===p);
    if (alts.length) tieMap[r.id]=alts;
  });
  const tieIds=Object.keys(tieMap);
  const tiePanel=document.getElementById('tie-panel');
  if (tieIds.length){
    tiePanel.style.display='';
    tiePanel.innerHTML=`<div class="tie-panel-title">⚠ ${tieIds.length} risultat${tieIds.length===1?'o':'i'} con alternative a pari punteggio (scelti arbitrariamente) — clicca un'alternativa per sostituire:</div>`+
      tieIds.map(id=>{
        const r=ALL.find(x=>x.id===+id);
        const alts=tieMap[id];
        const altChips=alts.map(a=>`<span class="tie-chip" title="Clicca per sostituire" onclick="swapResult(${r.id},${a.id})">${a.athlete} <span style="color:var(--muted)">${a.perf}</span></span>`).join('');
        return `<div class="tie-row"><span class="tie-sel">📌 <strong>${r.ev}</strong> — ${r.athlete} (${r.perf})</span><span style="color:var(--muted);margin:0 .3rem">→ anche:</span>${altChips}</div>`;
      }).join('');
  } else {
    tiePanel.style.display='none';
  }

  const tbody=document.getElementById('pros-body');
  if (!sel.length){
    tbody.innerHTML='<tr><td colspan="11" style="padding:2rem;text-align:center;color:var(--muted)">Nessun risultato selezionato.</td></tr>';
    updateConstraints();
    return;
  }
  tbody.innerHTML=sel.map((r,i)=>{
    const p=pts(r);
    const pVal=userPts[r.id]!==undefined?userPts[r.id]:(r.pts_ok?r.pts:'');
    const dbl=ec[r.ev]===2?'<span class="dbl-badge">×2</span>':'';
    const best=r.isBest?'<span class="best-mark" title="Miglior prestazione nella disciplina">*</span>':'';
    const tieBadge=tieMap[r.id]?`<span class="tie-badge" title="${tieMap[r.id].length} alternativa/e con uguale punteggio">≡${tieMap[r.id].length}</span>`:'';
    return `<tr class="selected-row">
      <td style="color:var(--muted);font-family:var(--mono);font-size:.72rem">${i+1}</td>
      <td><span class="etype ${r.type}">${TYPE_LBL[r.type]}</span></td>
      <td style="font-weight:600;white-space:nowrap">${r.ev}${dbl}</td>
      <td style="font-size:.78rem">${athleteDisplay(r)}${tieBadge}</td>
      <td style="font-size:.75rem;color:var(--muted);font-family:var(--mono)">${r.anno||''}</td>
      <td class="perf">${best}${r.perf}${r.wind?` <span style="font-size:.68rem;color:var(--muted)">${r.wind}</span>`:''}</td>
      <td style="font-size:.75rem;color:var(--muted)">${r.piazz||''}</td>
      <td style="font-size:.75rem;color:var(--muted);white-space:nowrap">${r.citta||''}</td>
      <td style="font-size:.75rem;color:var(--muted);white-space:nowrap;font-family:var(--mono)">${r.data||''}</td>
      <td style="text-align:right">
        <input class="pts-inp" type="number" min="0" value="${pVal}" placeholder="—"
          title="Inserisci punti FIDAL"
          onchange="userPts[${r.id}]=this.value!==''?+this.value:undefined;updateConstraints();renderProspetto();"
          onclick="event.stopPropagation()">
      </td>
      <td><button class="del-btn" title="Rimuovi" onclick="toggleSelect(${r.id})">✕</button></td>
    </tr>`;
  }).join('');
  updateConstraints();
}

function swapResult(oldId, newId){
  selectedIds.delete(oldId);
  selectedIds.add(newId);
  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
}

// ── RENDER ALL RESULTS ────────────────────────────────────
let sortedAll = [];
function sortAll(col){
  if (sortCol===col) sortAsc=!sortAsc; else {sortCol=col;sortAsc=true;}
  renderAll();
}

function renderAll(){
  const typeF=document.getElementById('f-type').value;
  const nameF=document.getElementById('f-name').value.toLowerCase();
  const evF=document.getElementById('f-ev-filter').value;

  let filtered=activeAll().filter(r=>
    (!typeF||r.type===typeF)&&
    (!nameF||r.athlete.toLowerCase().includes(nameF))&&
    (!evF||r.ev===evF)
  );

  if (sortCol>=0){
    const keys=[r=>r.type,r=>r.ev,r=>r.athlete,r=>r.perf,r=>r.piazz,r=>pts(r)];
    const fn=keys[sortCol]||((r)=>r.id);
    filtered.sort((a,b)=>{
      const va=fn(a),vb=fn(b);
      const n=parseFloat(va),m=parseFloat(vb);
      if (!isNaN(n)&&!isNaN(m)) return sortAsc?n-m:m-n;
      return sortAsc?String(va).localeCompare(String(vb),'it'):String(vb).localeCompare(String(va),'it');
    });
  }

  document.getElementById('all-n').textContent=filtered.length;
  const tbody=document.getElementById('all-body');
  tbody.innerHTML=filtered.map(r=>{
    const st=rowStatus(r);
    const inSel=selectedIds.has(r.id);
    const pVal=userPts[r.id]!==undefined?userPts[r.id]:(r.pts_ok?r.pts:'');
    const rowCls=`avail-row row-${st}`;
    const canClick=st!=='block';
    const btnTxt=inSel?'✓ Incluso':'+ Aggiungi';
    const btnCls='add-btn'+(inSel?' sel':'');
    const best=r.isBest?'<span class="best-mark" title="Miglior prestazione nella disciplina">*</span>':'';
    const manualBadge=r.isManual?'<span class="manual-badge">manuale</span>':'';
    const delManual=r.isManual?`<button class="del-btn" title="Rimuovi risultato manuale"
      onclick="event.stopPropagation();removeManual(${r.id})" style="margin-left:.25rem">🗑</button>`:'';
    return `<tr class="${rowCls}" ${canClick?`onclick="toggleSelect(${r.id})" title="${statusTitle(st,r)}"`:''}>
      <td style="text-align:center">${statusIcon(st)}</td>
      <td><span class="etype ${r.type}">${TYPE_LBL[r.type]}</span></td>
      <td style="font-weight:600;white-space:nowrap;font-size:.8rem">${r.ev}${manualBadge}</td>
      <td style="font-size:.78rem" onclick="event.stopPropagation()">${athleteDisplay(r)}</td>
      <td style="color:var(--muted);font-size:.72rem;font-family:var(--mono)">${r.anno||''}</td>
      <td class="perf">${best}${r.perf}</td>
      <td style="color:var(--muted);font-size:.72rem">${r.wind||'—'}</td>
      <td style="color:var(--muted);font-size:.72rem">${r.piazz||''}</td>
      <td style="color:var(--muted);font-size:.72rem;white-space:nowrap">${r.citta||''}</td>
      <td style="color:var(--muted);font-size:.72rem;white-space:nowrap;font-family:var(--mono)">${r.data||''}</td>
      <td style="text-align:right" onclick="event.stopPropagation()">
        <input class="pts-inp" type="number" min="0" value="${pVal}" placeholder="—"
          title="Inserisci punti FIDAL"
          onchange="userPts[${r.id}]=this.value!==''?+this.value:undefined;updateConstraints();renderProspetto();">
      </td>
      <td onclick="event.stopPropagation()">
        <button class="${btnCls}" ${st==='block'?'disabled':''} title="${statusTitle(st,r)}"
          onclick="toggleSelect(${r.id})">${btnTxt}</button>${delManual}
      </td>
    </tr>`;
  }).join('');
}

// ── INSERIMENTO MANUALE ───────────────────────────────────
let manualIdCounter = 100000;

function detectType(evName){
  const n=evName.toLowerCase();
  if (/staffetta|[34]x\d+|\dx\d/.test(n)) return 'staffetta';
  if (/\bhs\b|ostacoli|siepi/.test(n))     return 'ostacoli';
  if (/lungo|triplo|alto|asta|salto/.test(n)) return 'salto';
  if (/peso|martello|giavellotto|disco|lancio/.test(n)) return 'lancio';
  return 'corsa';
}

function onManualEvInput(){
  const ev=document.getElementById('m-ev').value;
  document.getElementById('m-tipo').value=detectType(ev);
}

function toggleManualForm(){
  const f=document.getElementById('manual-form');
  const visible=f.style.display!=='none';
  f.style.display=visible?'none':'';
  if (!visible){
    // Popola datalist con le gare esistenti
    const dl=document.getElementById('m-ev-list');
    dl.innerHTML=[...new Set(ALL.map(r=>r.ev))].sort((a,b)=>a.localeCompare(b,'it'))
      .map(e=>`<option value="${e}">`).join('');
    document.getElementById('m-ev').focus();
    document.getElementById('manual-err').textContent='';
  }
}

function submitManual(){
  const ev=(document.getElementById('m-ev').value||'').trim();
  const tipo=document.getElementById('m-tipo').value;
  const perf=(document.getElementById('m-perf').value||'').trim();
  const athlRaw=(document.getElementById('m-athl').value||'').trim();
  const ptsVal=document.getElementById('m-pts').value;
  const errEl=document.getElementById('manual-err');
  errEl.textContent='';

  if (!ev)     { errEl.textContent='⚠ Inserisci il nome della gara.'; return; }
  if (!perf)   { errEl.textContent='⚠ Inserisci la prestazione.'; return; }
  if (!athlRaw){ errEl.textContent='⚠ Inserisci almeno un/una atleta.'; return; }
  const _cdsCheck = CDS_PROGRAMS[currentCategoria];
  if (_cdsCheck && !_cdsCheck(ev)){
    errEl.style.color='var(--warn,#e67e00)';
    errEl.textContent=`⚠ "${ev}" non è nel programma CdS ${currentCategoria} — verrà salvato ma escluso dall'ottimizzatore.`;
  }

  const isStaff=(tipo==='staffetta');
  const staffAthl=isStaff ? athlRaw.split(/[,;\/]/).map(s=>s.trim()).filter(Boolean) : null;
  const pts_num=ptsVal!==''?+ptsVal:0;
  const pts_ok=ptsVal!=='';

  const citta=(document.getElementById('m-citta').value||'').trim();
  const data=(document.getElementById('m-data').value||'').trim();

  const r={
    id: manualIdCounter++,
    ev, type: tipo,
    athlete: isStaff ? (staffAthl.join(' / ')) : athlRaw,
    athlete_url:'', perf, wind:'', piazz:'', citta, data, anno:'',
    pts: pts_num, pts_ok,
    isStaffetta: isStaff,
    rawStaff: isStaff ? athlRaw : '',
    staffAthl: isStaff ? staffAthl : undefined,
    isManual: true,
  };

  ALL.push(r);

  // Persisti sul server
  const _mp = getFormParams();
  const savePayload={
    categoria:currentCategoria, ev:r.ev, type:r.type, athlete:r.athlete,
    perf:r.perf, wind:r.wind, piazz:r.piazz, citta:r.citta, data:r.data,
    anno:r.anno, pts:r.pts, pts_ok:r.pts_ok,
    isStaffetta:r.isStaffetta, rawStaff:r.rawStaff, staffAthl:r.staffAthl,
    isManual:true,
    soc_cod: _mp.societa || '',
    soc_nome: document.getElementById('f-societa-name')?.value?.trim() || '',
  };
  fetch('/api/manual',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(savePayload)})
    .then(res=>res.json()).then(json=>{
      if(json.ok){
        r.savedId=json.savedId;
        _triggerSocReoptimize(savePayload.soc_cod, savePayload.categoria);
      }
    })
    .catch(()=>{});

  computeBests();
  buildEvFilterPanel();

  // Aggiorna filtro discipline
  const evSel=document.getElementById('f-ev-filter');
  if (![...evSel.options].some(o=>o.value===ev)){
    const o=document.createElement('option'); o.value=ev; o.textContent=ev; evSel.appendChild(o);
  }

  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();

  // Reset form (mantieni aperto per aggiungerne altri)
  document.getElementById('m-ev').value='';
  document.getElementById('m-perf').value='';
  document.getElementById('m-athl').value='';
  document.getElementById('m-pts').value='';
  document.getElementById('m-citta').value='';
  document.getElementById('m-data').value='';
  document.getElementById('m-ev').focus();
}

function removeManual(id){
  const idx=ALL.findIndex(r=>r.id===id&&r.isManual);
  if (idx<0) return;
  const r=ALL[idx];
  if (r.savedId){
    fetch(`/api/manual/${r.savedId}`,{method:'DELETE'})
      .then(()=>_triggerSocReoptimize(currentSocieta, currentCategoria))
      .catch(()=>{});
  }
  ALL.splice(idx,1);
  selectedIds.delete(id);
  computeBests();
  buildEvFilterPanel();
  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
}

// ── CSV IMPORT ───────────────────────────────────────────────────────────────
let _csvFile = null;
let _csvOpenedFromForm = false;  // true = aperto dal form screen (modalità manuale)

function openCsvModal(fromForm){
  _csvOpenedFromForm = !!fromForm;
  // Mostra/nasconde la nota contestuale
  document.getElementById('csv-form-note').style.display = fromForm ? '' : 'none';
  document.getElementById('csv-overlay').style.display='flex';
  document.getElementById('csv-result-box').style.display='none';
  document.getElementById('csv-result-box').innerHTML='';
  document.getElementById('csv-file-input').value='';
  document.getElementById('csv-file-chosen').style.display='none';
  document.getElementById('btn-csv-upload').disabled=true;
}
function closeCsvModal(){
  document.getElementById('csv-overlay').style.display='none';
  _csvFile=null;
  _csvOpenedFromForm=false;
  document.getElementById('csv-file-input').value='';
  document.getElementById('csv-file-chosen').style.display='none';
  document.getElementById('btn-csv-upload').disabled=true;
  document.getElementById('csv-result-box').style.display='none';
  document.getElementById('csv-result-box').innerHTML='';
  document.getElementById('csv-upload-spinner').style.display='none';
}

function csvFileChosen(file){
  if (!file) return;
  _csvFile=file;
  const label=document.getElementById('csv-file-chosen');
  label.textContent=`File selezionato: ${file.name} (${(file.size/1024).toFixed(1)} KB)`;
  label.style.display='block';
  document.getElementById('btn-csv-upload').disabled=false;
  document.getElementById('csv-result-box').style.display='none';
  document.getElementById('csv-result-box').innerHTML='';
}

function csvHandleDrop(e){
  e.preventDefault();
  document.getElementById('csv-drop-zone').classList.remove('drag-over');
  const file=e.dataTransfer?.files?.[0];
  if (file) csvFileChosen(file);
}

function csvDownloadTemplate(){
  const a=document.createElement('a');
  a.href='/api/manual/template_csv';
  a.download='template_importazione_cds.csv';
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
}

let _discListLoaded = false;
function toggleDisciplineList(){
  const panel = document.getElementById('csv-discipline-list');
  const toggle = document.getElementById('csv-disc-toggle');
  const open = panel.style.display === 'none';
  panel.style.display = open ? '' : 'none';
  toggle.textContent = open ? 'nascondi lista ▴' : 'mostra lista ▾';
  if (open && !_discListLoaded){
    _discListLoaded = true;
    fetch('/api/discipline_list').then(r=>r.json()).then(data=>{
      const order = ['CF','CM','RF','RM'];
      const labels = {CF:'Cadette (CF)', CM:'Cadetti (CM)', RF:'Ragazze (RF)', RM:'Ragazzi (RM)'};
      const content = document.getElementById('csv-disc-content');
      content.innerHTML = order.map(cat=>{
        const pills = (data[cat]||[]).map(g=>`<span class="csv-disc-pill">${g}</span>`).join('');
        return `<div><div class="csv-disc-cat">${labels[cat]}</div><div>${pills}</div></div>`;
      }).join('');
    }).catch(()=>{
      document.getElementById('csv-disc-content').innerHTML='<em style="color:var(--muted)">Errore caricamento lista.</em>';
    });
  }
}

function _csvEntryToRow(entry){
  return {
    id: manualIdCounter++,
    ev: entry.ev,
    type: entry.type,
    athlete: entry.athlete,
    athlete_url: '',
    perf: entry.perf,
    wind: entry.wind||'',
    piazz: entry.piazz||'',
    citta: entry.citta||'',
    data: entry.data||'',
    anno: '',
    pts: entry.pts||0,
    pts_ok: entry.pts_ok||false,
    isStaffetta: entry.isStaffetta||false,
    rawStaff: entry.rawStaff||'',
    staffAthl: entry.staffAthl||undefined,
    isManual: true,
    savedId: entry.savedId,
  };
}

async function csvUpload(){
  if (!_csvFile){ return; }
  const btn=document.getElementById('btn-csv-upload');
  const spin=document.getElementById('csv-upload-spinner');
  const box=document.getElementById('csv-result-box');
  btn.disabled=true; spin.style.display='inline'; box.style.display='none'; box.innerHTML='';

  const fd=new FormData();
  fd.append('file', _csvFile);

  let json;
  try {
    const resp=await fetch('/api/manual/import_csv',{method:'POST',body:fd});
    json=await resp.json();
  } catch(err) {
    spin.style.display='none'; btn.disabled=false;
    box.innerHTML=`<div class="csv-warn-bar">Errore di rete: ${err.message}</div>`;
    box.style.display='block';
    return;
  }
  spin.style.display='none';

  if (!json.ok && !json.imported){
    box.innerHTML=`<div class="csv-warn-bar"><strong>Errore:</strong> ${json.error||'Importazione fallita'}</div>`;
    box.style.display='block';
    btn.disabled=false;
    return;
  }

  const imported=json.imported||[];
  const errors=json.errors||[];

  // Feedback testuale
  let html='';
  if (imported.length>0){
    const n=imported.length;
    html+=`<div class="csv-ok-bar">${n} riga${n!==1?'he':''} importata${n!==1?'e':''} con successo.</div>`;
  }
  if (errors.length>0){
    const n=errors.length;
    html+=`<div class="csv-warn-bar" style="margin-top:.5rem"><strong>${n} riga${n!==1?'he':''} con errori (non importat${n!==1?'e':'a'}):</strong>
    <ul class="csv-err-list" style="margin-top:.4rem">`;
    errors.forEach(e=>{
      const ante=e.anteprima?` <em style="color:var(--muted)">(${e.anteprima})</em>`:'';
      html+=`<li><strong>Riga ${e.riga}${ante}:</strong> ${e.errori.join('; ')}</li>`;
    });
    html+=`</ul></div>`;
  }
  box.innerHTML=html; box.style.display='block';

  if (imported.length>0){
    if (_csvOpenedFromForm){
      // ── Flusso da form screen: inizializza tool screen con i soli dati CSV ──
      const p = getFormParams();
      p.societa_nome = document.getElementById('f-societa-name')?.value?.trim() || '';
      // La categoria può venire dal form oppure, se tutte le righe hanno la stessa, da lì
      const cats=[...new Set(imported.map(e=>e.categoria))];
      if (!p.categoria && cats.length===1) p.categoria=cats[0];

      // Reset stato tool
      ALL=[]; selectedIds.clear(); userPts={}; staffAnalysis=[]; excludedEvs=new Set();
      unavailableAthletes=new Set(); minDateFilter=null; isProiezione=false;

      // Popola ALL con i record importati (filtro per categoria del form)
      imported.forEach(entry=>{
        if (!p.categoria || entry.categoria===p.categoria) ALL.push(_csvEntryToRow(entry));
      });

      computeBests();
      ALL.filter(r=>r.isStaffetta).forEach(r=>{ r.staffAthl=r.staffAthl||[]; });
      setupToolScreen(p);
      show('scr-tool');
      await _applyAutoOpts();
      // Chiudi il modale dopo un breve ritardo per far vedere il feedback
      setTimeout(closeCsvModal, 1200);
    } else {
      // ── Flusso da tool screen: aggiunge ai risultati esistenti ──
      imported.forEach(entry=>{
        if (entry.categoria===currentCategoria) ALL.push(_csvEntryToRow(entry));
      });
      computeBests(); buildEvFilterPanel();
      renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
      const evSel=document.getElementById('f-ev-filter');
      imported.filter(e=>e.categoria===currentCategoria).forEach(e=>{
        if(![...evSel.options].some(o=>o.value===e.ev)){
          const o=document.createElement('option'); o.value=e.ev; o.textContent=e.ev; evSel.appendChild(o);
        }
      });
    }
  }
  btn.disabled=false;
}
// ── END CSV IMPORT ────────────────────────────────────────────────────────────

async function checkSavedManualEntries(categoria, soc_cod){
  try {
    const resp=await fetch(`/api/manual?categoria=${encodeURIComponent(categoria)}`);
    const json=await resp.json();
    if (!json.ok) return;
    // Mostra il banner solo per le entries di questa società.
    // Se l'entry non ha soc_cod (entries vecchie) la includiamo per sicurezza.
    const relevant = json.data.filter(e =>
      !e.soc_cod || !soc_cod || e.soc_cod === soc_cod
    );
    if (relevant.length > 0){
      savedManualEntries = relevant;
      // Mostra il banner solo se il caricamento automatico è disabilitato
      if (!autoLoadManual){
        document.getElementById('reload-count').textContent = relevant.length;
        document.getElementById('manual-reload-bar').style.display = '';
      }
    }
  } catch(e){}
}

function dismissReloadBar(){
  document.getElementById('manual-reload-bar').style.display='none';
  savedManualEntries=[];
}

function reloadManualEntries(){
  savedManualEntries.forEach(entry=>{
    // Evita duplicati (stesso savedId già caricato)
    if (entry.savedId && ALL.some(r=>r.savedId===entry.savedId)) return;
    const r={
      id: manualIdCounter++,
      ev: entry.ev, type: entry.type,
      athlete: entry.athlete, athlete_url:'',
      perf: entry.perf, wind: entry.wind||'', piazz: entry.piazz||'',
      citta: entry.citta||'', data: entry.data||'', anno: entry.anno||'',
      pts: entry.pts||0, pts_ok: entry.pts_ok||false,
      isStaffetta: entry.isStaffetta||false,
      rawStaff: entry.rawStaff||'',
      staffAthl: entry.staffAthl||undefined,
      isManual: true,
      savedId: entry.savedId,
    };
    if (r.isStaffetta && !r.staffAthl)
      r.staffAthl=resolveStaffettaAthletes(r.rawStaff);
    ALL.push(r);

    // Aggiorna filtro discipline
    const evSel=document.getElementById('f-ev-filter');
    if (![...evSel.options].some(o=>o.value===r.ev)){
      const o=document.createElement('option'); o.value=r.ev; o.textContent=r.ev; evSel.appendChild(o);
    }
  });
  computeBests();
  buildEvFilterPanel();
  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
  document.getElementById('manual-reload-bar').style.display='none';
  savedManualEntries=[];
}

// ── FILTRO GARE CDS ───────────────────────────────────────
function buildEvFilterPanel(){
  const panel=document.getElementById('ev-filter-panel');
  const chipsEl=document.getElementById('ev-chips');
  const evs=[...new Set(ALL.map(r=>r.ev))].sort((a,b)=>a.localeCompare(b,'it'));
  if (!evs.length){ panel.style.display='none'; return; }
  panel.style.display='';
  // Mostra bottone preset solo se la categoria corrente ha un programma definito
  const btnPreset=document.getElementById('btn-preset-cds');
  btnPreset.style.display=CDS_PROGRAMS[currentCategoria] ? '' : 'none';
  chipsEl.innerHTML='';
  evs.forEach(ev=>{
    const chip=document.createElement('span');
    chip.className='ev-chip'+(excludedEvs.has(ev)?' excl':'');
    chip.textContent=ev;
    chip.title=excludedEvs.has(ev)?'Clicca per includere nel CdS':'Clicca per escludere dal CdS';
    chip.addEventListener('click',()=>toggleEvFilter(ev));
    chipsEl.appendChild(chip);
  });
}

function applyPresetCds(){
  const fn=CDS_PROGRAMS[currentCategoria];
  if (!fn) return;
  const evs=[...new Set(ALL.map(r=>r.ev))];
  evs.forEach(ev=>{
    if (fn(ev)){
      excludedEvs.delete(ev);
    } else {
      excludedEvs.add(ev);
      ALL.filter(r=>r.ev===ev).forEach(r=>selectedIds.delete(r.id));
    }
  });
  computeBests();
  buildEvFilterPanel();
  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
}

function resetEvFilter(){
  excludedEvs.clear();
  computeBests();
  buildEvFilterPanel();
  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
}

function toggleEvFilter(ev){
  if (excludedEvs.has(ev)){
    excludedEvs.delete(ev);
  } else {
    excludedEvs.add(ev);
    ALL.filter(r=>r.ev===ev).forEach(r=>selectedIds.delete(r.id));
  }
  computeBests();
  buildEvFilterPanel();
  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
}

// ── ATLETI NON DISPONIBILI ────────────────────────────────
function renderUnavailPanel(){
  const allAthletes=[...new Set(ALL.filter(r=>!r.isStaffetta).map(r=>r.athlete))].sort((a,b)=>a.localeCompare(b,'it'));
  const dl=document.getElementById('unavail-list');
  dl.innerHTML=allAthletes.filter(a=>!unavailableAthletes.has(a)).map(a=>`<option value="${a}">`).join('');
  const chips=document.getElementById('unavail-chips');
  chips.innerHTML=[...unavailableAthletes].sort((a,b)=>a.localeCompare(b,'it')).map(a=>
    `<span class="unavail-chip">${a}<button onclick="removeUnavailable(${JSON.stringify(a)})" title="Ripristina">✕</button></span>`
  ).join('');
}

function addUnavailFromInput(){
  const input=document.getElementById('unavail-input');
  const name=(input.value||'').trim();
  if (!name) return;
  unavailableAthletes.add(name);
  // Rimuovi dalla selezione i risultati dell'atleta escluso
  ALL.filter(r=>{
    const athls=r.isStaffetta?(r.staffAthl||[r.athlete]):[r.athlete];
    return athls.some(a=>a===name);
  }).forEach(r=>selectedIds.delete(r.id));
  input.value='';
  _afterGlobalFilter();
}

function removeUnavailable(name){
  unavailableAthletes.delete(name);
  _afterGlobalFilter();
}

// ── FILTRO DATA PRESTAZIONE ───────────────────────────────
function parseResultDate(str){
  if (!str) return null;
  // Con anno: DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY
  let m=str.match(/(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})/);
  if (m) return new Date(+m[3], +m[2]-1, +m[1]);
  // Solo giorno/mese (formato FIDAL standard): DD/MM, DD-MM, DD.MM
  // → usa l'anno della ricerca corrente
  m=str.match(/(\d{1,2})[\/\-\.](\d{1,2})/);
  if (m) return new Date(currentAnno, +m[2]-1, +m[1]);
  return null;
}

function onDateFilterChange(){
  const val=document.getElementById('date-filter-input').value;
  if (val){
    // input type=date restituisce YYYY-MM-DD (UTC): parsifichiamo in ora locale
    const [y,mo,d]=val.split('-').map(Number);
    minDateFilter=new Date(y, mo-1, d);
  } else {
    minDateFilter=null;
  }
  document.getElementById('date-filter-clear').style.display=val?'':'none';
  if (minDateFilter){
    ALL.filter(r=>{
      if (!r.data) return false;
      const d=parseResultDate(r.data);
      return d && d < minDateFilter;
    }).forEach(r=>selectedIds.delete(r.id));
  }
  _afterGlobalFilter();
}

function clearDateFilter(){
  document.getElementById('date-filter-input').value='';
  minDateFilter=null;
  document.getElementById('date-filter-clear').style.display='none';
  _afterGlobalFilter();
}

function _updateDateCount(){
  const el=document.getElementById('date-filter-count');
  if (!minDateFilter){ el.textContent=''; return; }
  const n=ALL.filter(r=>{
    if (!r.data) return false;
    const d=parseResultDate(r.data);
    return d && d < minDateFilter;
  }).length;
  el.textContent=n>0?`(${n} risultat${n===1?'o':'i'} esclus${n===1?'o':'i'})`:'(nessun risultato escluso)';
}

function _afterGlobalFilter(){
  computeBests();
  buildEvFilterPanel();
  renderUnavailPanel();
  _updateDateCount();
  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
}

// ── OTTIMIZZAZIONE ────────────────────────────────────────

function _pruneForProiezione(nPerEv){
  // Per le staffette: tieni solo il top 1 per disciplina (migliore della regione).
  // Per le gare individuali: tieni i top nPerEv (per gestire conflitti atleti).
  // NOTA: usa riferimento oggetto (non r.id) — in proiezione gli id si ripetono tra società.
  const _cdsProg = CDS_PROGRAMS[currentCategoria];
  if (_cdsProg) ALL = ALL.filter(r => _cdsProg(r.ev));
  const byEv = {};
  ALL.forEach(r => { (byEv[r.ev] = byEv[r.ev]||[]).push(r); });
  const keep = new Set(); // Set di oggetti, non di id
  for (const ers of Object.values(byEv)){
    ers.sort((a,b)=>(pts(b)||0)-(pts(a)||0));
    const n = ers[0]?.isStaffetta ? 1 : nPerEv;
    ers.slice(0, n).forEach(r=>keep.add(r));
  }
  ALL = ALL.filter(r=>keep.has(r));
}
function* _staffCombos(groups){
  // Prodotto cartesiano: per ogni tipo di staffetta scegli null (escludi) o una delle entry.
  if (!groups.length){ yield []; return; }
  const [first, ...rest] = groups;
  for (const tail of _staffCombos(rest)){
    yield [null, ...tail];
    for (const entry of first) yield [entry, ...tail];
  }
}
function* combIter(arr, k){
  const n=arr.length;
  if (k===0){yield[];return;} if (k>n) return;
  const idx=Array.from({length:k},(_,i)=>i);
  while(true){
    yield idx.map(i=>arr[i]);
    let i=k-1; while(i>=0&&idx[i]===i+n-k) i--;
    if (i<0) break; idx[i]++;
    for(let j=i+1;j<k;j++) idx[j]=idx[j-1]+1;
  }
}

function isValidSelCaps(sel, evCap, maxAthlInd=2){
  const evUsed={}, acTotal={}, acInd={};
  for (const r of sel){
    if (!r.isStaffetta){
      evUsed[r.ev]=(evUsed[r.ev]||0)+1;
      if (evUsed[r.ev]>(evCap[r.ev]||1)) return false;
    }
    const athls=r.isStaffetta?(r.staffAthl||[r.athlete]):[r.athlete];
    for (const a of athls){
      acTotal[a]=(acTotal[a]||0)+1;
      if (!r.isStaffetta) acInd[a]=(acInd[a]||0)+1;
      if (acTotal[a]>2) return false;
      if (acInd[a]>maxAthlInd) return false;
    }
  }
  return true;
}

// Cache pre-calcolata da searchOptimal: ev → [risultati ordinati, top-N]
let _evCands = null;

function assignBest(evSub, dblSet, inclStaff){
  const C=getC();
  const evCap={};
  for (const ev of evSub) evCap[ev]=dblSet.has(ev)?2:1;
  const staffEvs=new Set(inclStaff.map(r=>r.ev));
  const cands=[];
  for (const ev of evSub){
    if (staffEvs.has(ev)) continue;
    // Usa la cache pre-calcolata se disponibile, altrimenti fallback
    const src = (_evCands && _evCands[ev]) ||
                activeAll().filter(r=>r.ev===ev&&!r.isStaffetta);
    src.forEach(r=>cands.push(r));
  }
  // Tiebreaker stabile: stesso punteggio → ordine deterministico per atleta+prestazione
  cands.sort((a,b)=>pts(b)-pts(a)||a.athlete.localeCompare(b.athlete,'it')||a.perf.localeCompare(b.perf,'it'));

  // Greedy: prenota atleti staffetta, poi riempi in ordine di punteggio
  const sel=[],acTotal={},acInd={},evUsed={};
  inclStaff.filter(r=>evSub.includes(r.ev)).forEach(st=>{
    sel.push(st); evUsed[st.ev]=(evUsed[st.ev]||0)+1;
    (st.staffAthl||[]).forEach(a=>acTotal[a]=(acTotal[a]||0)+1);
  });
  for (const r of cands){
    const ev=r.ev;
    if ((evUsed[ev]||0)>=(evCap[ev]||1)) continue;
    if ((acTotal[r.athlete]||0)>=2) continue;
    if ((acInd[r.athlete]||0)>=C.maxAthlInd) continue;
    sel.push(r);
    acTotal[r.athlete]=(acTotal[r.athlete]||0)+1;
    acInd[r.athlete]=(acInd[r.athlete]||0)+1;
    evUsed[ev]=(evUsed[ev]||0)+1;
  }

  let swapped=true;
  while (swapped){
    swapped=false;
    const indivSel=sel.filter(s=>!s.isStaffetta).sort((a,b)=>pts(a)-pts(b)||a.athlete.localeCompare(b.athlete,'it'));
    for (const r2 of indivSel){
      const idx=sel.indexOf(r2);
      for (const r of cands){
        if (sel.some(s=>s.id===r.id)) continue;
        if (pts(r)<=pts(r2)) break;
        const candidate=[...sel]; candidate[idx]=r;
        // Blocca lo swap se svuoterebbe l'evento di r2 (singolo → 0 risultati)
        if (!candidate.some(x=>!x.isStaffetta&&x.ev===r2.ev)) continue;
        if (isValidSelCaps(candidate, evCap, C.maxAthlInd)){ sel[idx]=r; swapped=true; break; }
      }
      if (swapped) break;
    }
  }

  return {sel, total:sel.reduce((s,r)=>s+pts(r),0)};
}

function searchOptimal(inclStaff, maxDoubles){
  const C=getC();
  const maxD = maxDoubles !== undefined ? maxDoubles : C.nSel-C.minEv;

  // Pre-calcola candidati per evento una sola volta (evita N×activeAll() in assignBest)
  // In proiezione con molte società, limita a top-25 per evento: sufficiente per l'ottimale
  const _ownCache = (_evCands === null);
  if (_ownCache){
    const active = activeAll();
    const TOP = isProiezione ? 25 : Infinity;
    _evCands = {};
    for (const r of active){
      if (r.isStaffetta) continue;
      (_evCands[r.ev] = _evCands[r.ev] || []).push(r);
    }
    for (const ev of Object.keys(_evCands)){
      _evCands[ev].sort((a,b)=>pts(b)-pts(a)||a.athlete.localeCompare(b.athlete,'it'));
      if (TOP !== Infinity && _evCands[ev].length > TOP) _evCands[ev].length = TOP;
    }
  }

  const staffEvs=new Set(inclStaff.map(r=>r.ev));
  const active = activeAll();
  const evList=[...new Set(active.filter(r=>!r.isStaffetta||staffEvs.has(r.ev)).map(r=>r.ev))];
  const dbl=evList.filter(ev=>((_evCands&&_evCands[ev])||active.filter(r=>r.ev===ev)).length>=2);
  let best=-1,bestSel=null;
  let _assignBestCalls=0;
  const _t0=Date.now();
  for (let nEv=C.minEv;nEv<=Math.min(C.nSel,evList.length);nEv++){
    const nD=C.nSel-nEv;
    if (nD > maxD) continue; // salta se richiede troppe doppiature
    for (const evSub of combIter(evList,nEv)){
      let nl=0,ns=0;
      for (const ev of evSub){if(isLancio(ev))nl++;if(isSalto(ev))ns++;}
      if (nl<C.minLanci||ns<C.minSalti) continue;
      const dc=evSub.filter(ev=>dbl.includes(ev));
      if (dc.length<nD) continue;
      for (const de of combIter(dc,nD)){
        _assignBestCalls++;
        const {sel,total}=assignBest(evSub,new Set(de),inclStaff);
        if (sel.length!==C.nSel) continue;
        // Tutti gli eventi di evSub devono avere almeno 1 risultato
        const selEvs=new Set(sel.map(x=>x.ev));
        if (!evSub.every(ev=>selEvs.has(ev))) continue;
        if (total>best){best=total;bestSel=sel;}
      }
    }
  }
  if (_ownCache){
    _evCands = null;
    console.log(`[searchOptimal] staff=[${inclStaff.map(r=>r.ev).join(',')||'—'}] eventi=${evList.length} assignBest=${_assignBestCalls} tempo=${Date.now()-_t0}ms best=${best}`);
  }
  return {total:best,sel:bestSel};
}

function buildOptDiagnostic(){
  const C=getC();
  const all=activeAll();
  const allEvs=[...new Set(all.map(r=>r.ev))];
  const indEvs=[...new Set(all.filter(r=>!r.isStaffetta).map(r=>r.ev))];
  const nDblNeeded=C.nSel-C.minEv;
  const dblEvs=allEvs.filter(ev=>all.filter(r=>r.ev===ev).length>=2);
  const lanciEvs=indEvs.filter(ev=>isLancio(ev));
  const saltiEvs=indEvs.filter(ev=>isSalto(ev));

  function row(ok, label, evList, needed, have){
    const ico=ok?'✅':'❌';
    const cnt=`<strong>${have}/${needed}</strong>`;
    const evStr=evList.length?`<span class="diag-ev">${evList.join(' · ')}</span>`:'';
    const miss=!ok?` — mancano ${needed-have}`:'';
    return `<div class="opt-diag-row"><span class="diag-ico">${ico}</span>${label}: ${cnt}${miss}${evStr}</div>`;
  }

  return `<div class="opt-diag">`+
    row(allEvs.length>=C.minEv,   'Gare disponibili', [], C.minEv, allEvs.length)+
    row(lanciEvs.length>=C.minLanci, 'Lanci', lanciEvs, C.minLanci, lanciEvs.length)+
    row(saltiEvs.length>=C.minSalti, 'Salti', saltiEvs, C.minSalti, saltiEvs.length)+
    row(dblEvs.length>=nDblNeeded,
        `Gare con ≥2 risultati (per ${nDblNeeded} doppiata${nDblNeeded===1?'':'e'})`,
        dblEvs, nDblNeeded, dblEvs.length)+
    `</div>`;
}

function setNoteEst(msg, isError=false){
  const errBanner=document.getElementById('calcola-err');
  if (isError){
    errBanner.innerHTML=msg;
    errBanner.style.display='block';
  } else {
    errBanner.style.display='none';
    errBanner.innerHTML='';
  }
}

async function _ensureManualEntries(){
  if (!currentCategoria) return;
  try {
    const resp = await fetch(`/api/manual?categoria=${encodeURIComponent(currentCategoria)}`);
    const json = await resp.json();
    if (!json.ok || !json.data.length) return;
    // Filtra per società corrente (stessa logica di checkSavedManualEntries)
    const relevant = json.data.filter(e =>
      !e.soc_cod || !currentSocieta || e.soc_cod === currentSocieta
    );
    if (!relevant.length) return;
    let added = false;
    for (const entry of relevant) {
      if (ALL.some(r => r.savedId === entry.savedId)) continue;
      const r = {
        id: manualIdCounter++,
        ev: entry.ev, type: entry.type,
        athlete: entry.athlete, athlete_url: '',
        perf: entry.perf, wind: entry.wind||'', piazz: entry.piazz||'',
        citta: entry.citta||'', data: entry.data||'', anno: entry.anno||'',
        pts: entry.pts||0, pts_ok: entry.pts_ok||false,
        isStaffetta: entry.isStaffetta||false,
        rawStaff: entry.rawStaff||'',
        staffAthl: entry.staffAthl||undefined,
        isManual: true, savedId: entry.savedId,
      };
      if (r.isStaffetta && !r.staffAthl)
        r.staffAthl = resolveStaffettaAthletes(r.rawStaff);
      ALL.push(r);
      added = true;
    }
    if (added) {
      computeBests(); buildEvFilterPanel();
      renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
      document.getElementById('manual-reload-bar').style.display = 'none';
      savedManualEntries = [];
    }
  } catch(e) {}
}

async function computeOptimal(){
  await _ensureManualEntries();
  const missing=activeAll().filter(r=>userPts[r.id]===undefined&&!r.pts_ok);
  if (missing.length>0){
    setNoteEst(`⚠ ${missing.length} risultat${missing.length===1?'o':'i'} senza punteggio — inserisci i punti FIDAL per tutti prima di calcolare.`, true);
    return;
  }
  setNoteEst('');

  const _lbar  = document.getElementById('loading-bar-track');
  const _lfill = document.getElementById('loading-bar-fill');
  const _lsub  = document.getElementById('loading-sub');
  
  _setLoadingMsg('Analisi algoritmica server-side DFS Branch&Bound in corso…');
  if (_lbar)  _lbar.style.display = '';
  if (_lfill) { _lfill.classList.remove('indeterminate'); _lfill.classList.add('indeterminate'); _lfill.style.width = '100%'; }
  document.getElementById('loading').classList.remove('hidden');

  try {
      const active = activeAll();
      const C=getC();
      
      const _p = getFormParams();
      const payload = {
          categoria: currentCategoria,
          soc_cod:  _p.societa || '',
          soc_nome: document.getElementById('f-societa-name')?.value?.trim() || '',
          data: active.map(r => {
            const ret = {...r};
            ret.pts = pts(r);
            return ret;
          })
      };
      
      const resp = await fetch('/api/ottimizza', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
      });
      
      const res = await resp.json();
      if (!res.ok) throw new Error(res.error);
      
      selectedIds.clear();
      
      if (!res.optimal || !res.optimal.sel || res.optimal.sel.length !== C.nSel){
          setNoteEst(`⚠ Impossibile trovare ${C.nSel} risultati validi che incastrino tutti i vincoli.`, true);
          staffAnalysis = [];
      } else {
          setNoteEst('');
          res.optimal.sel.forEach(r => selectedIds.add(Number(r.id)));

          // Costruisce analisi per ogni staffetta in activeAll() con pts_ok
          const baselineScore = res.baseline_score || 0;
          const staffScores   = res.staff_scores   || {};
          const staffette = activeAll().filter(r => r.isStaffetta && r.pts_ok);
          staffAnalysis = staffette.map(staff => {
              const inOpt  = selectedIds.has(staff.id);
              const sid    = String(staff.id);
              // tCon: se nell'ottimale usiamo il punteggio esatto; altrimenti il
              // best calcolato dall'ottimizzatore per quella combo (0 se non trovato).
              const tCon   = inOpt ? res.optimal.score : (staffScores[sid] || 0);
              const tSenza = baselineScore;
              const delta  = tCon - tSenza;
              return {staff, tCon, tSenza, delta, inOpt};
          });
      }

      renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
      renderStaffettaAnalysis();
      
  } catch(e) {
      alert("Errore calcolo ottimale: " + e.message);
  } finally {
      document.getElementById('loading').classList.add('hidden');
      if (_lbar)  _lbar.style.display='none';
  }
}

// Funzioni Legacy rimosse
function _staffCombos(groups) { return []; }
function searchOptimal(inclStaff, maxDoubles) { return {sel:[], total:0}; }
function assignBest(evSub, dblSet, inclStaff) { return {sel:[], total:0}; }

// ── CLASSIFICA REGIONALE ──────────────────────────────────
async function computeClassifica(){
  if (!isProiezione || !ALL.length){
    console.log('[Classifica] Proiezione non caricata — carico da cache...');
    await fetchProiezione(false);
    return; // fetchProiezione chiama computeClassifica di nuovo dopo aver caricato
  }

  const content = document.getElementById('clas-screen-content');
  const title   = document.getElementById('clas-screen-title');

  // ── Percorso veloce: usa societies_meta pre-calcolata dal build ──────────
  if (Object.keys(_societiesMeta).length > 0){
    console.log('[Classifica] Uso societies_meta pre-calcolata dal JSON.');
    const data = Object.entries(_societiesMeta).map(([cod, m]) => ({...m, cod}));
    _renderClassifica(data, title, content);
    return;
  }

  // ── Fallback: calcola da ALL client-side (somma punti, no optimizer) ─────
  content.innerHTML = '<div style="color:var(--muted);font-size:.82rem;padding:.4rem 0">Calcolo classifica…</div>';
  await new Promise(r=>setTimeout(r,0));

  const _lKw=['peso','martello','giavellotto','disco','lancio','vortex','palla'];
  const _sKw=['lungo','triplo','alto','asta','salto'];
  const _cdsProgClas = CDS_PROGRAMS[currentCategoria];
  const bySOC={};
  for (const r of ALL){
    if (_cdsProgClas && !_cdsProgClas(r.ev)) continue;
    const key=r.soc_cod||r.soc_nome; if (!key) continue;
    if (!bySOC[key]) bySOC[key]={nome:r.soc_nome||key, results:[]};
    bySOC[key].results.push(r);
  }

  const data = Object.values(bySOC).map(({nome, results})=>{
    const evs=new Set(results.map(r=>r.ev));
    const evLow=[...evs].map(e=>e.toLowerCase());
    const n_lanci=evLow.filter(e=>_lKw.some(k=>e.includes(k))).length;
    const n_salti=evLow.filter(e=>_sKw.some(k=>e.includes(k))).length;
    const n_ev=evs.size;
    let total_pts=0,pts_corsa=0,pts_lanci=0,pts_salti=0,pts_staffette=0;
    for (const r of results){
      const p=pts(r)||0; total_pts+=p;
      const t=r.type||'corsa';
      if (t==='corsa'||t==='ostacoli') pts_corsa+=p;
      else if (t==='lancio')           pts_lanci+=p;
      else if (t==='salto')            pts_salti+=p;
      else if (t==='staffetta')        pts_staffette+=p;
    }
    const _Cc=CONSTRAINTS[currentCategoria]||CONSTRAINTS.default;
    return {nome,total_pts,pts_corsa,pts_lanci,pts_salti,pts_staffette,
            num_gare:n_ev,n_lanci,n_salti,can_compete:n_ev>=_Cc.minEv&&n_lanci>=_Cc.minLanci&&n_salti>=_Cc.minSalti};
  });
  console.log(`[Classifica] Calcolate ${data.length} società da ALL.`);
  _renderClassifica(data, title, content);
}

function _renderClassifica(data, title, content){
  const p=getFormParams();
  // Ordina per score ottimale (se calcolato) altrimenti per total_pts
  const _score = s => (s.optimal && s.optimal.score > 0) ? s.optimal.score : (s.total_pts || 0);
  const eligible=data.filter(s=>s.can_compete).sort((a,b)=>_score(b)-_score(a));
  const notElig=data.filter(s=>!s.can_compete).length;
  console.log(`[Classifica] ${eligible.length} competitive, ${notElig} non eleggibili. Top 5:`,
    eligible.slice(0,5).map(s=>({nome:s.nome,pts:s.total_pts})));

  document.getElementById('clas-screen-title').textContent=`Proiezione Regionale — ${p.regione}`;
  const _pending = eligible.filter(s=>!s.optimal||!s.optimal.score).length;
  const _subParts = [`CdS ${p.categoria} · ${p.tipo_attivita==='P'?'Outdoor':'Indoor'} ${p.anno}`];
  _subParts.push(`${eligible.filter(s=>s.optimal&&s.optimal.score>0).length} in classifica`);
  if (_pending) _subParts.push(`${_pending} in attesa calcolo`);
  if (notElig)  _subParts.push(`${notElig} non eleggibili`);
  document.getElementById('clas-screen-sub').textContent=_subParts.join(' · ');

  if (!eligible.length){
    content.innerHTML='<div style="color:var(--muted);font-size:.82rem">Nessuna società con requisiti CdS soddisfatti.</div>';
    return;
  }
  // Solo le società con optimal calcolato entrano in classifica
  // (tot. disponibile non rispetta i vincoli CdS → non confrontabile)
  const ranked = eligible.filter(s => s.optimal && s.optimal.score > 0);
  const pendingOpt = eligible.length - ranked.length;

  if (ranked.length === 0){
    const msg = pendingOpt > 0
      ? `Nessuna società ha ancora il punteggio CdS ottimale calcolato (${pendingOpt} competitive trovate). Rigenera il DB regionale per calcolare le schede ottimali.`
      : 'Nessuna società competitiva trovata.';
    content.innerHTML = `<div style="color:var(--muted);font-size:.85rem;padding:.5rem 0">${msg}</div>`;
    return;
  }

  _classificaRanked = ranked;
  _clasDataByRid = {};   // reset mappa rid → {s, maxPts, cat}
  const maxPts = ranked[0].optimal.score;
  const rows = ranked.flatMap((s,i)=>{
    const barW = Math.round(s.optimal.score/maxPts*100);
    const medal = i===0?'🥇':i===1?'🥈':i===2?'🥉':`${i+1}.`;
    const rid = `clas-row-${i}`;
    _clasDataByRid[rid] = {s, maxPts, cat: p.categoria};

    // Punteggio: "scheda ottimale" o "Σ totale disponibile"
    const scoreCell = `<span style="font-weight:700">${s.optimal.score.toLocaleString('it')}</span>
      <span title="Punteggio scheda CdS ottimale: selezione automatica dei 13 migliori risultati rispettando i vincoli (min 10 gare, min 2 lanci, min 2 salti, max 2 atlete per gara)" style="font-size:.66rem;color:var(--green);margin-left:.3rem;cursor:help">CdS ottimale</span>`;

    // Breakdown: Corsa (corse+ostacoli) · Salti · Lanci · Staffette
    const bk = [
      `🏃 ${(s.pts_corsa||0).toLocaleString('it')} cors.`,
      `↑ ${(s.pts_salti||0).toLocaleString('it')} salt.`,
      `⭕ ${(s.pts_lanci||0).toLocaleString('it')} lanc.`,
    ];
    if (s.pts_staffette) bk.push(`🔄 ${s.pts_staffette.toLocaleString('it')} staff.`);
    const breakdown = bk.join(' · ');

    // Bottone expand dettaglio
    const viewBtn = s.optimal.sel && s.optimal.sel.length
      ? `<button class="clas-expand" onclick="toggleClasDetail('${rid}')">+</button>`
      : '';

    // Bottone "Apri scheda" — carica la società nella vista tool
    // Usa virgolette singole per l'attributo onclick così JSON.stringify (doppie) non rompe l'HTML
    const openBtn = `<button class="btn-open-soc" title="Apri scheda"
        onclick='loadSocFromClassifica(${JSON.stringify(s.cod)})'>📂 Apri</button>`;

    // Badge gap al primo posto
    const gap = maxPts - s.optimal.score;
    const gapCell = i===0
      ? `<span class="clas-gap-zero">—</span>`
      : `<span class="clas-gap-badge">−${gap.toLocaleString('it')} pt</span>`;

    // Riga principale
    const mainRow = `<tr>
      <td class="clas-rank">${medal}</td>
      <td>${s.nome}</td>
      <td class="clas-score">${scoreCell}</td>
      <td style="color:var(--muted);font-size:.71rem;white-space:nowrap">${breakdown}</td>
      <td>${gapCell}</td>
      <td class="clas-bar-cell"><div class="clas-bar" style="width:${barW}%"></div></td>
      <td style="width:28px">${viewBtn}</td>
      <td style="width:70px">${openBtn}</td>
    </tr>`;

    // Riga dettaglio (scheda ottimale espandibile)
    let detailRow = '';
    if (s.optimal.sel && s.optimal.sel.length) {
      const detailRows = s.optimal.sel
        .sort((a,b)=>(b.pts||0)-(a.pts||0))
        .map(r=>{
          const typeLbl = r.isStaffetta ? '<span style="color:#1565c0;font-size:.68rem;font-weight:700">STAFF</span>'
                        : '<span style="color:var(--muted);font-size:.68rem">IND</span>';
          return `<tr>
            <td>${typeLbl}</td>
            <td style="font-weight:600">${r.ev}</td>
            <td>${r.athlete||''}</td>
            <td>${r.perf||''}</td>
            <td style="font-family:var(--mono);font-weight:700;color:var(--blue);text-align:right">${(r.pts||0).toLocaleString('it')}</td>
          </tr>`;
        }).join('');
      detailRow = `<tr class="clas-detail" id="${rid}">
        <td colspan="7">
          <table class="clas-detail-table">
            <thead><tr style="color:var(--muted);font-size:.67rem">
              <th></th><th>Disciplina</th><th>Atleta</th><th>Prestazione</th><th style="text-align:right">Punti</th>
            </tr></thead>
            <tbody>${detailRows}</tbody>
          </table>
          <div class="gap-panel" id="gap-panel-${rid}"></div>
        </td>
      </tr>`;
    }
    return [mainRow, detailRow].filter(Boolean);
  }).join('');

  content.innerHTML=`<table class="clas-table">
    <thead><tr>
      <th>#</th><th>Società</th>
      <th title="'CdS ottimale' = scheda calcolata con vincoli (13 ris., min 10 gare, min 2 lanci+salti); 'tot. disponibile' = somma grezza, rigenera il DB per il valore esatto">Punti</th>
      <th style="font-size:.67rem">Corsa · Salti · Lanci · Staff</th>
      <th title="Distanza in punti dal primo classificato">Gap al 1°</th>
      <th></th><th></th><th></th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function loadSocFromClassifica(cod){
  const meta = _societiesMeta[cod];
  if (!meta || !_currentProiezioneP) {
    alert('Dati non disponibili. Ricarica la proiezione regionale.');
    return;
  }

  // Aggiorna i campi del form così l'URL preview e getFormParams() sono coerenti
  const socInput = document.getElementById('f-societa');
  if (socInput) { socInput.value = cod; updateUrlPreview(); }
  const socNameEl = document.getElementById('f-societa-name');
  if (socNameEl) socNameEl.value = meta.nome || '';

  const p = { ..._currentProiezioneP, societa: cod, societa_nome: meta.nome || cod };

  document.getElementById('loading').classList.remove('hidden');
  _setLoadingMsg('Caricamento dati FIDAL…');

  try {
    // Chiamata FIDAL identica a fetchData() ma con la società specifica
    const params = new URLSearchParams({
      anno:          p.anno,
      tipo_attivita: p.tipo_attivita,
      sesso:         p.sesso,
      categoria:     p.categoria,
      regione:       p.regione,
      nazionalita:   p.nazionalita || '0',
      vento:         p.vento       || '2',
      limite:        p.limite      || '100',
      societa:       cod,
    });
    const resp = await fetch('/api/fetch?' + params);
    const json = await resp.json();
    if (!json.ok) throw new Error(json.error);

    ALL = json.data;
    selectedIds.clear(); userPts = {}; staffAnalysis = []; excludedEvs = new Set();
    unavailableAthletes = new Set(); minDateFilter = null; isProiezione = false;
    document.getElementById('staff-panel').style.display = 'none';

    computeBests();
    ALL.filter(r => r.isStaffetta).forEach(r => {
      r.staffAthl = resolveStaffettaAthletes(r.rawStaff);
    });

    // Applica la selezione ottimale pre-calcolata abbinando per (ev, athlete, perf)
    // Gli id cambiano a ogni fetch fresco, quindi non si può usare id numerico
    if (meta.optimal && meta.optimal.sel && meta.optimal.sel.length) {
      meta.optimal.sel.forEach(optR => {
        const match = ALL.find(r =>
          r.ev === optR.ev &&
          r.athlete === optR.athlete &&
          String(r.perf) === String(optR.perf)
        );
        if (match) selectedIds.add(match.id);
      });
    }

    setupToolScreen(p);
    show('scr-tool');
    renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
    await _applyAutoOpts();

  } catch(e) {
    alert('Errore caricamento società: ' + e.message);
    // Ripristina il LED se l'errore è di rete
    try { _setFidalStatus('error', `Errore — ${e.message.slice(0,60)}`); } catch(_){}
  } finally {
    document.getElementById('loading').classList.add('hidden');
  }
}

let _clasDataByRid = {};

function _nextLevel(tabEv, curPts) {
  // Trova il livello immediatamente superiore (minor delta pts) nella tabella
  let best = null;
  for (const [perf, pts] of Object.entries(tabEv || {})) {
    if (pts <= curPts) continue;
    const delta = pts - curPts;
    if (!best || delta < best.delta) best = {perf, pts, delta};
  }
  return best; // {perf, pts, delta} oppure null
}

async function _loadGapPanel(rid) {
  const panel = document.getElementById(`gap-panel-${rid}`);
  if (!panel || panel.dataset.loaded) return;
  panel.dataset.loaded = '1';

  const d = _clasDataByRid[rid];
  if (!d) { panel.style.display='none'; return; }
  const {s, maxPts, cat} = d;
  const gap = maxPts - s.optimal.score;

  panel.innerHTML = `<div class="gap-panel-title">Opportunità di miglioramento</div>
    <div style="font-size:.72rem;color:var(--muted);margin-bottom:.4rem">Caricamento tabelle…</div>`;

  const tabella = await _getTabellaCategoria(cat);

  const sel = (s.optimal.sel || []).filter(r => !r.isStaffetta && r.pts > 0);
  const improvements = [];
  for (const r of sel) {
    const tabEv = tabella[r.ev];
    if (!tabEv) continue;
    const next = _nextLevel(tabEv, r.pts);
    if (!next) continue;
    improvements.push({ev: r.ev, athlete: r.athlete||'', perf_cur: r.perf||'',
      pts_cur: r.pts, perf_next: next.perf, pts_next: next.pts, delta: next.delta});
  }
  improvements.sort((a, b) => b.delta - a.delta);

  if (!improvements.length) {
    panel.innerHTML = `<div class="gap-panel-title">Opportunità di miglioramento</div>
      <div style="font-size:.73rem;color:var(--muted)">Nessun livello superiore trovato in tabella per le gare selezionate.</div>`;
    return;
  }

  const totRec = improvements.reduce((s,r)=>s+r.delta, 0);
  const newTot  = s.optimal.score + totRec;
  const gapStr  = gap > 0 ? `<span style="font-size:.73rem;color:var(--muted)"> — gap al 1°: <strong style="color:#c0392b">−${gap.toLocaleString('it')} pt</strong></span>` : '';

  const trows = improvements.map(r =>
    `<tr>
      <td style="font-weight:600">${r.ev}</td>
      <td style="color:var(--muted);font-size:.72rem">${r.athlete}</td>
      <td style="font-family:var(--mono)">${r.perf_cur}</td>
      <td style="font-family:var(--mono)"><span class="gap-perf-arr">→</span>${r.perf_next}</td>
      <td style="text-align:right"><span class="gap-delta">+${r.delta.toLocaleString('it')}</span></td>
    </tr>`
  ).join('');

  panel.innerHTML = `
    <div class="gap-panel-title">Opportunità di miglioramento${gapStr}</div>
    <table class="gap-impr-table">
      <thead><tr>
        <th>Disciplina</th><th>Atleta</th><th>Prest. attuale</th><th>Target tabella</th>
        <th style="text-align:right">Guadagno</th>
      </tr></thead>
      <tbody>${trows}</tbody>
      <tfoot><tr class="gap-tot-row">
        <td colspan="4">Totale recuperabile (tutti i livelli)</td>
        <td style="text-align:right;font-family:var(--mono)">+${totRec.toLocaleString('it')} pt → ${newTot.toLocaleString('it')}</td>
      </tr></tfoot>
    </table>`;
}

async function toggleClasDetail(rid){
  const row = document.getElementById(rid);
  if (!row) return;
  const isOpen = row.classList.contains('open');
  row.classList.toggle('open', !isOpen);
  const btn = row.previousElementSibling?.querySelector('.clas-expand');
  if (btn) btn.textContent = isOpen ? '+' : '−';
  if (!isOpen) await _loadGapPanel(rid);
}

// ── CLASSIFICA EXPORT ────────────────────────────────────
function downloadClassificaCSV(){
  if (!_classificaRanked || !_classificaRanked.length){ alert('Nessuna classifica disponibile. Carica prima la proiezione regionale.'); return; }
  const p = getFormParams();
  const hdr = ['Rank','Società','Punti_CdS','Corsa','Salti','Lanci','Staffette','Gare_CdS','N_Lanci','N_Salti'];
  const rows = [hdr];
  _classificaRanked.forEach((s,i)=>{
    rows.push([
      i+1, s.nome, s.optimal.score,
      s.pts_corsa||0, s.pts_salti||0, s.pts_lanci||0, s.pts_staffette||0,
      s.num_gare||0, s.n_lanci||0, s.n_salti||0,
    ]);
    if (s.optimal.sel && s.optimal.sel.length){
      rows.push(['','— Scheda ottimale —','','','','','','','','','']);
      s.optimal.sel.slice().sort((a,b)=>(b.pts||0)-(a.pts||0)).forEach(r=>{
        rows.push(['', '', r.pts||0, '', '', '', '', r.ev, r.athlete||'', r.anno||'', r.perf||'']);
      });
    }
  });
  const csv = rows.map(r=>r.map(v=>`"${String(v).replace(/"/g,'""')}"`).join(',')).join('\r\n');
  const blob = new Blob(['﻿'+csv],{type:'text/csv;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  const tag = `Classifica_${p.categoria}_${p.tipo_attivita==='P'?'Outdoor':'Indoor'}_${p.anno}_${p.regione}`;
  a.download = `${tag}.csv`;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}

function printClassificaPDF(){
  if (!_classificaRanked || !_classificaRanked.length){ alert('Nessuna classifica disponibile. Carica prima la proiezione regionale.'); return; }
  const p = getFormParams();
  const today = new Date().toLocaleDateString('it-IT',{day:'2-digit',month:'2-digit',year:'numeric'});
  const tipoLbl = p.tipo_attivita==='P' ? 'Outdoor' : 'Indoor';
  const titleStr = `Proiezione Regionale — ${p.regione}`;
  const subStr   = `CdS ${p.categoria} · ${tipoLbl} ${p.anno}`;

  const medalStr = i => i===0?'🥇':i===1?'🥈':i===2?'🥉':`${i+1}.`;

  const mainRows = _classificaRanked.map((s,i)=>{
    const bk = [
      `🏃 ${(s.pts_corsa||0).toLocaleString('it')}`,
      `↑ ${(s.pts_salti||0).toLocaleString('it')}`,
      `⭕ ${(s.pts_lanci||0).toLocaleString('it')}`,
    ];
    if (s.pts_staffette) bk.push(`🔄 ${s.pts_staffette.toLocaleString('it')}`);
    const rowBg = i%2===0 ? '' : 'background:#f8fafc';
    return `<tr style="border-bottom:1px solid #e2e8f0;${rowBg}">
      <td style="padding:6px 8px;font-size:13pt;text-align:center;width:36px">${medalStr(i)}</td>
      <td style="padding:6px 8px;font-weight:700;font-size:9.5pt">${s.nome}</td>
      <td style="padding:6px 8px;font-family:monospace;font-weight:800;font-size:11pt;color:#054FAE;text-align:right;white-space:nowrap">${s.optimal.score.toLocaleString('it')}</td>
      <td style="padding:6px 8px;font-size:7.5pt;color:#555;white-space:nowrap">${bk.join(' · ')}</td>
    </tr>`;
  }).join('');

  const html = `<!DOCTYPE html>
<html lang="it"><head>
<meta charset="UTF-8">
<title>${titleStr}</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Arial',sans-serif;padding:14mm 18mm;color:#0d1f3c;font-size:10pt}
  @page{size:A4;margin:0}
  @media print{body{padding:10mm 14mm}}
</style>
</head><body>

<div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:14px;border-bottom:3px solid #054FAE;padding-bottom:10px">
  <div>
    <div style="font-size:7pt;text-transform:uppercase;letter-spacing:.1em;color:#6b82a0;margin-bottom:3px">Campionato di Società — Proiezione Regionale</div>
    <div style="font-size:18pt;font-weight:800;color:#054FAE;line-height:1.1">${titleStr}</div>
    <div style="font-size:10pt;color:#444;margin-top:3px">${subStr}</div>
  </div>
  <div style="text-align:right;font-size:8pt;color:#6b82a0">
    <div>Generato il ${today}</div>
    <div style="margin-top:4px;font-size:7pt">FIDAL CdS Tool</div>
  </div>
</div>

<table style="width:100%;border-collapse:collapse">
  <thead>
    <tr style="background:#054FAE;color:#fff">
      <th style="padding:6px 8px;text-align:center;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;width:36px">#</th>
      <th style="padding:6px 8px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase">Società</th>
      <th style="padding:6px 8px;text-align:right;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;width:90px">Punti CdS</th>
      <th style="padding:6px 8px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase">Corsa · Salti · Lanci · Staff</th>
    </tr>
  </thead>
  <tbody>${mainRows}</tbody>
</table>

<div style="margin-top:10px;font-size:7pt;color:#999">Punti CdS = scheda ottimale calcolata con vincoli CdS (13 risultati, min 10 gare, min 2 lanci, min 2 salti)</div>

<script>window.onload=function(){window.print();}<\/script>
</body></html>`;

  const w = window.open('','_blank','width=860,height=700');
  w.document.open(); w.document.write(html); w.document.close();
}

// ── STAFFETTA ANALYSIS ────────────────────────────────────
function renderStaffettaAnalysis(){
  const panel=document.getElementById('staff-panel');
  const container=document.getElementById('staff-cards');
  if (!staffAnalysis.length){
    container.innerHTML='';
    panel.style.display='none';
    return;
  }
  panel.style.display='';
  container.innerHTML=staffAnalysis.map(({staff,tCon,tSenza,delta,inOpt})=>{
    const p=pts(staff);
    const chips=(staff.staffAthl||[staff.athlete]).map(a=>`<span class="chip">${a}</span>`).join('');
    let cardCls, verdictCls, verdictTxt, conLabel;
    if (inOpt){
      cardCls='ok'; verdictCls='ok';
      verdictTxt=`✅ Nell'ottimale · +${delta} pt vs. nessuna staffetta`;
      conLabel=`<strong>${tCon} pt</strong>`;
    } else if (tCon===0){
      // Combo potata interamente dal B&B prima della valutazione: punteggio non disponibile
      cardCls='no'; verdictCls='no';
      verdictTxt=`❌ Esclusa — combinazione non valutata (superata dall'ottimale)`;
      conLabel=`<span style="color:var(--muted)">n/d</span>`;
    } else if (delta>0){
      cardCls='warn'; verdictCls='warn';
      const v2=validate();
      const athls=staff.staffAthl||[staff.athlete];
      const bloccate=athls.filter(a=>(v2.atlCount[a]||0)>=2);
      const motivo=bloccate.length
        ? `${bloccate.join(', ')} ${bloccate.length===1?'è già':'sono già'} a 2 gare nell'ottimale`
        : 'le atlete hanno più valore nelle gare individuali dell\'ottimale';
      verdictTxt=`⚠ Conviene da sola (+${delta} pt) ma esclusa — ${motivo}`;
      conLabel=`<strong>${tCon} pt</strong>`;
    } else {
      cardCls='no'; verdictCls='no';
      verdictTxt=`❌ Non conviene (${delta} pt) — le atlete valgono di più individualmente`;
      conLabel=`<strong>${tCon} pt</strong>`;
    }
    const manBadge=staff.isManual?'<span class="manual-badge">manuale</span>':'';
    return `<div class="staff-card ${cardCls}">
      <div class="scard-head">
        <span class="scard-ev">${staff.ev}${manBadge}</span>
        <span class="scard-perf">${staff.perf}</span>
        <span class="scard-pts">${p} pt${staff.est&&userPts[staff.id]===undefined?' ~':''}</span>
        <span style="margin-left:auto;font-size:.68rem;color:var(--muted)">${(staff.staffAthl||[]).length} atlete</span>
      </div>
      <div class="chips">${chips}</div>
      <div style="font-size:.7rem;color:var(--muted)">
        Con: ${conLabel} &nbsp;|&nbsp; Senza: <strong>${tSenza} pt</strong>
      </div>
      <div class="scard-verdict ${verdictCls}">${verdictTxt}</div>
    </div>`;
  }).join('');
}

// ── CSV EXPORT ────────────────────────────────────────────
function downloadCSV(){
  if (!topCombinations.length){ alert('Esegui prima ⚡ Calcola Ottimale per generare le combinazioni.'); return; }
  const hdr=['Rank','Totale_pt','Configurazione_staffette','Posizione','Gara','Tipo','Atleta_e','Prestazione','Punti_FIDAL'];
  const rows=[hdr];
  topCombinations.forEach(({total,inclStaff,sel},ci)=>{
    const sorted=[...sel].sort((a,b)=>pts(b)-pts(a));
    sorted.forEach((r,i)=>{
      const athlStr=r.isStaffetta?(r.staffAthl||[r.athlete]).join(' / '):r.athlete;
      rows.push([ci+1, total, inclStaff, i+1, r.ev, r.type, athlStr, r.perf, pts(r)]);
    });
  });
  const csv=rows.map(r=>r.map(v=>`"${String(v).replace(/"/g,'""')}"`).join(',')).join('\r\n');
  const blob=new Blob(['﻿'+csv],{type:'text/csv;charset=utf-8'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  const soc=document.getElementById('f-societa').value||'CdS';
  a.download=`${soc}_combinazioni.csv`;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}

// ── STAMPA / PDF ──────────────────────────────────────────
function printPDF(){
  const sel=ALL.filter(r=>selectedIds.has(r.id)).sort((a,b)=>pts(b)-pts(a));
  if (!sel.length){ alert('Nessun risultato selezionato. Seleziona i risultati prima di stampare.'); return; }

  const v=validate();
  const total=sel.reduce((s,r)=>s+pts(r),0);
  const title=document.getElementById('tool-title').textContent;
  const sub=document.getElementById('tool-sub').textContent;
  const today=new Date().toLocaleDateString('it-IT',{day:'2-digit',month:'2-digit',year:'numeric'});

  const ec={};
  sel.forEach(r=>ec[r.ev]=(ec[r.ev]||0)+1);

  const rows=sel.map((r,i)=>{
    const p=pts(r);
    const athlD=r.isStaffetta?(r.staffAthl||[r.athlete]).join(' / '):r.athlete;
    const dbl=ec[r.ev]===2?' <span style="background:#fef3c7;color:#92400e;font-size:7pt;padding:1px 4px;border-radius:3px">×2</span>':'';
    const best=r.isBest?'<span style="color:#c0392b;font-weight:800">*</span> ':'';
    const typeCols={corsa:'#dbeafe',ostacoli:'#ede9fe',salto:'#d1fae5',lancio:'#fef3c7',staffetta:'#fce7f3'};
    const typeClr=typeCols[r.type]||'#f0f4f9';
    return `<tr style="border-bottom:1px solid #e2e8f0;${i%2===0?'':'background:#f8fafc'}">
      <td style="padding:5px 7px;color:#6b82a0;font-size:8pt;text-align:center">${i+1}</td>
      <td style="padding:5px 7px"><span style="background:${typeClr};font-size:7pt;font-weight:700;text-transform:uppercase;letter-spacing:.05em;padding:2px 5px;border-radius:3px">${TYPE_LBL[r.type]}</span></td>
      <td style="padding:5px 7px;font-weight:600;font-size:9pt">${r.ev}${dbl}</td>
      <td style="padding:5px 7px;font-size:8.5pt">${athlD}</td>
      <td style="padding:5px 7px;font-family:monospace;font-size:8pt;color:#6b82a0;text-align:center">${r.anno||''}</td>
      <td style="padding:5px 7px;font-family:monospace;font-weight:600;color:#054FAE;font-size:9pt">${best}${r.perf}${r.wind?' <span style="font-size:7pt;color:#999">'+r.wind+'</span>':''}</td>
      <td style="padding:5px 7px;font-size:8pt;color:#6b82a0">${r.piazz||''}</td>
      <td style="padding:5px 7px;font-size:8pt;color:#6b82a0">${r.citta||''}</td>
      <td style="padding:5px 7px;font-family:monospace;font-size:8pt;color:#6b82a0">${r.data||''}</td>
      <td style="padding:5px 7px;text-align:right;font-family:monospace;font-weight:700;font-size:9.5pt;color:${p>0?'#054FAE':'#999'}">${p||'—'}</td>
    </tr>`;
  }).join('');

  const vincoli=`
    <span style="${v.nSel===13?'color:#1a7f3c':'color:#d46b08'}">● ${v.nSel}/13 risultati</span>
    &nbsp;&nbsp;
    <span style="${v.nEv>=10?'color:#1a7f3c':'color:#d46b08'}">● ${v.nEv} gare</span>
    &nbsp;&nbsp;
    <span style="${v.nLanci>=2?'color:#1a7f3c':'color:#d46b08'}">● ${v.nLanci}/2 lanci</span>
    &nbsp;&nbsp;
    <span style="${v.nSalti>=2?'color:#1a7f3c':'color:#d46b08'}">● ${v.nSalti}/2 salti</span>
    &nbsp;&nbsp;
    <span style="${v.evOk&&v.atlOk?'color:#1a7f3c':'color:#c0392b'}">● Vincoli ${v.evOk&&v.atlOk?'OK':'VIOLATI'}</span>`;

  const html=`<!DOCTYPE html>
<html lang="it"><head>
<meta charset="UTF-8">
<title>Scheda CdS — ${title}</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Arial',sans-serif;padding:15mm 18mm;color:#0d1f3c;font-size:10pt}
  @page{size:A4;margin:0}
  @media print{body{padding:10mm 14mm}}
</style>
</head><body>

<div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:14px;border-bottom:3px solid #054FAE;padding-bottom:10px">
  <div>
    <div style="font-size:7pt;text-transform:uppercase;letter-spacing:.1em;color:#6b82a0;margin-bottom:3px">Scheda Campionato di Società — Fase Provinciale</div>
    <div style="font-size:18pt;font-weight:800;color:#054FAE;line-height:1.1">${title}</div>
    <div style="font-size:10pt;color:#444;margin-top:3px">${sub}</div>
  </div>
  <div style="text-align:right;font-size:8pt;color:#6b82a0">
    <div>Generato il ${today}</div>
    <div style="margin-top:4px;font-size:7pt">FIDAL CdS Tool</div>
  </div>
</div>

<div style="margin-bottom:10px;font-size:8pt">${vincoli}</div>

<table style="width:100%;border-collapse:collapse">
  <thead>
    <tr style="background:#054FAE;color:#fff">
      <th style="padding:6px 7px;text-align:center;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;width:28px">#</th>
      <th style="padding:6px 7px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;width:70px">Tipo</th>
      <th style="padding:6px 7px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase">Disciplina</th>
      <th style="padding:6px 7px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase">Atleta/e</th>
      <th style="padding:6px 7px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;width:38px">Anno</th>
      <th style="padding:6px 7px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase">Prestazione</th>
      <th style="padding:6px 7px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;width:60px">Piazz.</th>
      <th style="padding:6px 7px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;width:65px">Città</th>
      <th style="padding:6px 7px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;width:45px">Data</th>
      <th style="padding:6px 7px;text-align:right;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;width:55px">Punti</th>
    </tr>
  </thead>
  <tbody>${rows}</tbody>
  <tfoot>
    <tr style="background:#054FAE;color:#fff">
      <td colspan="9" style="padding:7px 7px;font-size:9pt;font-weight:700;text-transform:uppercase;letter-spacing:.05em">Totale scheda</td>
      <td style="padding:7px 7px;text-align:right;font-family:monospace;font-size:13pt;font-weight:800;color:#00C9FF">${total.toLocaleString('it')}</td>
    </tr>
  </tfoot>
</table>

<div style="margin-top:10px;font-size:7pt;color:#999">
  * = miglior prestazione nella disciplina &nbsp;|&nbsp; ×2 = gara con doppio risultato
</div>

<script>window.onload=function(){window.print();}<\/script>
</body></html>`;

  const w=window.open('','_blank','width=900,height=750');
  w.document.open(); w.document.write(html); w.document.close();
}
</script>
</body>
</html>
"""

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
