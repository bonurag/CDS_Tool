with open('fidal_cds_tool.py', 'r', encoding='utf-8') as f:
    text = f.read()

import re
matches = re.findall(r'_normalize_events\(soc_manual, cat\)', text)
print(f"Norm manual called {len(matches)} times")
