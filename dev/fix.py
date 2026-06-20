with open('fidal_cds_tool.py', 'r', encoding='utf-8') as f:
    text = f.read()

import re
text = text.replace(\"    categoria = fp.get('categoria', '')\\n    for row in data:\", \"    categoria = fp.get('categoria', '')\\n    _normalize_events(data, categoria)\\n    for row in data:\")

with open('fidal_cds_tool.py', 'w', encoding='utf-8') as f:
    f.write(text)
print('Fixed normalize')
