with open('fidal_cds_tool.py', 'r', encoding='utf-8') as f:
    text = f.read()

text = text.replace(
    "cds_prog = _CDS_PROGRAMS.get(cat) if cat else None",
    "cds_prog = CdsUtils.get_cds_program(cat) if cat else None",
)

with open('fidal_cds_tool.py', 'w', encoding='utf-8') as f:
    f.write(text)
print('Fixed missing variable')
