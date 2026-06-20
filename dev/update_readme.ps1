with open('README.md', 'r', encoding='utf-8') as f:
    text = f.read()

import re

old_struct = re.search(r'## Struttura del progetto.*?`(.*?)`', text, re.DOTALL)
if old_struct:
    new_structure = """
## Struttura del progetto
`	ext
C:\...\CDS_Tool\
 ├── core\                   # Logica matematica
 │    ├── cds_optimizer.py   # Algoritmo DFS (Branch & Bound) per l'ottimizzazione dell'assegnazione atleti
 │    └── cds_utils.py       # Utilità, preset regolamentari per categorie, filtri e controlli
 │
 ├── data\                   # Database dei punteggi tabellari FIDAL (JSON)
 │    ├── Cadette.json       # (CF)
 │    ├── Cadetti.json       # (CM)
 │    ├── Ragazze.json       # (RF)
 │    └── Ragazzi.json       # (RM)
 │
 ├── dev\                    # Script ausiliari usati in fase di sviluppo/sostituzione regex
 ├── tests\                  # Test empirici di offline evaluation e chiamate server simulate
 │
 ├── fidal_cds_tool.py       # Interfaccia UI e Server API Web App Flask
 ├── fidal_cds_tool.spec     # Schema compilazione PyInstaller (.exe)
 └── build.bat               # Costruttore dell'eseguibile autonomo Windows
`

### Note sull'Evoluzione Algoritmica (Ottimizzatore v2)
L'applicativo supererà sempre la logica limitata degli algoritmi ingordi. Il motore di base (spostato in core/cds_optimizer.py) sfrutta ora un processo di esplorazione **Backtracking Depth-First Search** supportato da **Branch and Bound**. 
Inoltre:
- È subentrata una fortissima politica di normalizzazione stringhe: staffette che variavano da provincia a provincia ("4X100" vs "4 x 100") vengono unificate alla fonte, prevenendo lo sdoppiamento illecito delle staffette conteggiate dalle graduatorie HTML.
- L'assegnazione DFS evita i vicoli ciechi normativi permettendo spostamenti fluidi delle atlete laddove incastrate su limiti (es. 2 individuali vs 1 individuale+staffetta).
- L'interfaccia UI demanda la ricerca ottimale interamente al server Python.
"""
    text = text.replace(old_struct.group(0), new_structure.strip())
    
with open('README.md', 'w', encoding='utf-8') as f:
    f.write(text)

print('Updated README')
