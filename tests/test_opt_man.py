import json
from fidal_cds_tool import _do_fidal_fetch, _read_manual, _match_manual_to_soc, _normalize_events
from core.cds_optimizer import CdsOptimizer

p = {'anno':'2026','tipo_attivita':'P','sesso':'F','categoria':'CF','vento':'2','regione':'LOM','nazionalita':'0','limite':'100','societa':'BS318'}
data, _ = _do_fidal_fetch(p)

manual = _read_manual().get('CF', [])
soc_manual = _match_manual_to_soc(manual, 'BS318', 'ATL. CHIARI 1964 LIB.', data)
_normalize_events(soc_manual, 'CF')

data_full = data + soc_manual

staffette = [r for r in data_full if r.get('isStaffetta')]
for s in staffette:
    print(f"Staffetta: ev='{s.get('ev')}', pts={s.get('pts')}, perf={s.get('perf')}")

opt = CdsOptimizer.compute_optimal(data_full, 'CF')
if opt and opt.get('sel'):
    print("\nOttimale Trovato:")
    for s in opt['sel']:
        print(f" > {s.get('ev')} - {s.get('athlete')} - {s.get('pts')}")
    
    sel_staff = [s for s in opt['sel'] if s.get('isStaffetta')]
    print(f"\nStaffette selezionate: {len(sel_staff)} ({sel_staff[0]['ev'] if sel_staff else ''})")
    
    evs = set(s['ev'] for s in opt['sel'] if not s.get('isStaffetta'))
    print(f"Gare individuali distinte: {len(evs)}")
    print(f"Punteggio: {opt.get('score')}")
    
    # Check occorrenze
    evCount = {}
    for r in opt['sel']: 
        if not r.get('isStaffetta'): evCount[r['ev']] = evCount.get(r['ev'], 0) + 1
    
    print(f"Doppiate: {len([ev for ev, c in evCount.items() if c > 1])}")
else:
    print("Nessun ottimale trovato.")
