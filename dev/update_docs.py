with open('README.md', 'r', encoding='utf-8') as f:
    text = f.read()

import re

old_struct_match = re.search(r'## Struttura del progetto\n\n`	ext\n.*?\n`', text, re.DOTALL)
if old_struct_match:
    new_structure = \"\"\"## Struttura del progetto

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
 ├── dev\                    # Script e tool ausiliari (sviluppo)
 ├── tests\                  # Directory predisposta per i test engine (es. pytest)
 │
 ├── fidal_cds_tool.py       # Interfaccia UI e Server API (Web App Flask)
 ├── fidal_cds_tool.spec     # Schema di compilazione aggiornato per PyInstaller
 └── build.bat               # Costruttore dell'eseguibile autonomo (.exe)
`

### L'Algoritmo di Ottimizzazione 
L'applicativo supererà sempre la logica umana o gli script "Greedy" (ingordi). L'algoritmo integrato nella libreria core sfrutta un processo di esplorazione **Backtracking (Depth-First Search)** con potatura dell'albero **(Pruning, Branch and Bound)**:
- Riconosce i vicoli ciechi normativi.
- Taglia preventivamente i calcoli che, seppur alti individualmente, limiterebbero il risultato del team complessivo privandolo di atleti bloccati in gare minori.
- Prova esattamente l'incastro combinatorio massimo assoluto per raggiungere la vetta dei punti totali consentiti dalle graduatorie.\"\"\"
    
    text = text.replace(old_struct_match.group(0), new_structure)

with open('README.md', 'w', encoding='utf-8') as f:
    f.write(text)

with open('README_FIDAL_CDS_TOOL.md', 'r', encoding='utf-8') as f:
    text2 = f.read()

text2 = text2.replace('- Ricerca combinatoria esatta (141K+ combinazioni)', '- Ricerca combinatoria esatta (Algoritmo DFS Backtracking & Pruning) per garantire il Max Globale')

with open('README_FIDAL_CDS_TOOL.md', 'w', encoding='utf-8') as f:
    f.write(text2)
print("Aggiornamento Eseguito")
