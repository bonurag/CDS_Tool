with open('fidal_cds_tool.py', 'r', encoding='utf-8') as f:
    text = f.read()

import re
matches = list(re.finditer(r'@app\.route\(\'/api/ottimizza\'\)', text))
if len(matches) > 1:
    print('Found duplicate routes')
    text = text[:matches[-1].start()] + text[matches[-1].end():]

# In fact let's just make it clean
text = re.sub(r'(@app\.route\(\'/api/ottimizza\', methods=\[\'POST\'\]\)\s*def api_ottimizza\(\):.*?)@app\.route\(\'/api/ottimizza\', methods=\[\'POST\'\]\)\s*def api_ottimizza\(\):.*?(?=@app\.route)', r'\1', text, flags=re.DOTALL)

with open('fidal_cds_tool.py', 'w', encoding='utf-8') as f:
    f.write(text)
print('Fixed duplicate route')
