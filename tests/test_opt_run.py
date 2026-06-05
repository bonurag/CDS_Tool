import json, os
from fidal_cds_tool import _do_fidal_fetch, _TABELLE, _soc_meta
from core.cds_optimizer import CdsOptimizer
from core.cds_utils import CdsUtils

p = {'anno':'2026','tipo_attivita':'P','sesso':'F','categoria':'CF','vento':'2','regione':'LOM','nazionalita':'0','limite':'100','societa':'BS318'}

print("Fetching data...")
data, _ = _do_fidal_fetch(p)
print(f"Fetched {len(data)} rows.")

staffette = [r for r in data if r.get('isStaffetta')]
for s in staffette:
    print(f"Staffetta: ev='{s.get('ev')}', pts={s.get('pts')}, perf={s.get('perf')}")

meta = _soc_meta(data, 'CF')
print(f"Num gare agg: {meta['num_gare']}")

opt = CdsOptimizer.compute_optimal(data, 'CF')
if opt and opt.get('sel'):
    print("\nOttimale Trovato:")
    for s in opt['sel']:
        print(f" > {s.get('ev')} - {s.get('athlete')} - {s.get('pts')}")
    
    sel_staff = [s for s in opt['sel'] if s.get('isStaffetta')]
    print(f"\nStaffette selezionate: {len(sel_staff)}")
    
    evs = set(s['ev'] for s in opt['sel'] if not s.get('isStaffetta'))
    print(f"Gare individuali distinte: {len(evs)}")
    print(f"Punteggio: {opt.get('score')}")
else:
    print("Nessun ottimale trovato.")
