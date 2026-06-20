with open('fidal_cds_tool.py', 'r', encoding='utf-8') as f:
    text = f.read()

import re
old = "soc_manual = _match_manual_to_soc(\n                                manual_entries, soc['cod'], soc['nome'], results)\n                            results_full = results + soc_manual"
new = "soc_manual = _match_manual_to_soc(\n                                manual_entries, soc['cod'], soc['nome'], results)\n                            _normalize_events(soc_manual, cat)\n                            results_full = results + soc_manual"
text = text.replace(old, new)


old2 = "soc_manual = _match_manual_to_soc(\n                                manual_entries, soc['cod'], soc['nome'], cached_results)\n                            new_manual_count = len(soc_manual)"
new2 = "soc_manual = _match_manual_to_soc(\n                                manual_entries, soc['cod'], soc['nome'], cached_results)\n                            _normalize_events(soc_manual, cat)\n                            new_manual_count = len(soc_manual)"
text = text.replace(old2, new2)


with open('fidal_cds_tool.py', 'w', encoding='utf-8') as f:
    f.write(text)
print('Fixed manual norm')
