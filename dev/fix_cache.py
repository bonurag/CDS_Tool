with open('fidal_cds_tool.py', 'r', encoding='utf-8') as f:
    text = f.read()

import re
old = "old_cache = json.load(f)\n                    cached_meta = old_cache.get('societies_meta', {})"
new = "old_cache = json.load(f)\n                    _normalize_events(old_cache.get('data', []), cat)\n                    cached_meta = old_cache.get('societies_meta', {})"

text = text.replace(old, new)

with open('fidal_cds_tool.py', 'w', encoding='utf-8') as f:
    f.write(text)
print('Fixed cache normalization')
