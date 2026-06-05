with open('fidal_cds_tool.py', 'r', encoding='utf-8') as f:
    text = f.read()

import re
match = re.search(r'async function computeOptimal\(\)\{.*?(?=function renderProspetto\(\))', text, re.DOTALL)
if match:
    print("Found JS computeOptimal, length:", len(match.group(0)))
    with open('js_opt.txt', 'w', encoding='utf-8') as f:
        f.write(match.group(0))
else:
    print("JS computeOptimal not found.")
