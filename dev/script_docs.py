with open('README.md', 'r', encoding='utf-8') as f:
    text = f.read()

import re

old_struct = re.search(r'## Struttura del progetto.*?```(.*?)```', text, re.DOTALL)
if old_struct:
    new_structure = """## Struttura del progetto

```text
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
 ├── dev\                    # Script ausiliari usati in fase di sviluppo/sostituzione python
 ├── tests\                  # Test empirici di offline evaluation e chiamate server simulate
 │
 ├── fidal_cds_tool.py       # Interfaccia UI e Server API Web App Flask
 ├── fidal_cds_tool.spec     # Schema compilazione PyInstaller (.exe)
 └── build.bat               # Costruttore dell'eseguibile autonomo Windows
```

### Note sull'Evoluzione Algoritmica (Ottimizzatore v2)
L'applicativo supererà sempre la logica limitata degli algoritmi ingordi (Greedy). Il motore di base (stralciato e spostato in `core/cds_optimizer.py` e reso ad Oggetti) sfrutta ora un processo di calcolo combinatorio **DFS (Depth-First Search)** supportato da un tagliente **Branch and Bound**. 
Inoltre:
- È subentrata una fortissima politica di **normalizzazione stringhe**: gare e staffette variabilmente etichettate ("4X100" vs "4 x 100") vengono unificate a priori della ricerca per prevenire lo sdoppiamento fraudolento o incoerente.
- L'assegnazione DFS non procede "a casaccio per punti decrescenti" ma prova scambi fluidi valutando simultaneamente l'atleta intrappolato nei limiti di regolamento.
- La pagina Web non calcola più in modo posticcio sul browser con JS. Cliccando *Ottimale*, la web app innesca **l'esploratore Python via API Server (app.route api/ottimizza)** restituendo in un decimo di secondo la soluzione certificata."""

    text = text.replace(old_struct.group(0), new_structure)
    
with open('README.md', 'w', encoding='utf-8') as f:
    f.write(text)

print('Updated README')
