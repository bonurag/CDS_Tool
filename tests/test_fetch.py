import json
from fidal_cds_tool import _do_fidal_fetch

p = {'anno':'2026','tipo_attivita':'P','sesso':'F','categoria':'CF','vento':'2','regione':'LOM','nazionalita':'0','limite':'100','societa':'BS318'}
data, _ = _do_fidal_fetch(p)
staff = [r for r in data if 'affett' in r.get('perf', '').lower() or 'affett' in r.get('ev', '').lower()]
for s in staff:
    print(s)
