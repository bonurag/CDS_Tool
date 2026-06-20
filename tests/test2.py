with open('fidal_cds_tool.py', 'r', encoding='utf-8') as f:
    text = f.read()

start = text.find('async function computeOptimal')
end = text.find('// ── CLASSIFICA', start)
with open('js_optimal.txt', 'w', encoding='utf-8') as f:
    f.write(text[start:end])
