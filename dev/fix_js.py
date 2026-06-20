with open('fidal_cds_tool.py', 'r', encoding='utf-8') as f:
    text = f.read()

text = text.replace('setNoteEst(⚠  risultat senza punteggio — inserisci i punti FIDAL per tutti prima di calcolare., true);', 'setNoteEst(⚠ \ risultat\ senza punteggio — inserisci i punti FIDAL per tutti prima di calcolare., true);')

text = text.replace('setNoteEst(⚠ Impossibile trovare  risultati validi che incastrino tutti i vincoli., true);', 'setNoteEst(⚠ Impossibile trovare \ risultati validi che incastrino tutti i vincoli., true);')

with open('fidal_cds_tool.py', 'w', encoding='utf-8') as f:
    f.write(text)
print('Fixed JS syntax errors due to powershell string interpolation')
