with open('fidal_cds_tool.py', 'r', encoding='utf-8') as f:
    text = f.read()

start = text.find('async function computeOptimal')
print(text[start:start+1500])
