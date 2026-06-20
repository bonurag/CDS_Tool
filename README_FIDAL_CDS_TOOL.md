# FIDAL CdS Tool — Scheda Provinciale

Strumento web per la composizione ottimale della scheda di una società al **Campionato di Società** FIDAL (fase provinciale), categorie **CF · CM · RF · RM**.

---

## Installazione

```bash
pip install -r requirements.txt
python fidal_cds_tool.py
```

Il browser si apre automaticamente su `http://localhost:5001`.

---

## Funzionalità principali

### Schermata 1 — Parametri

- Seleziona anno, tipo attività (Outdoor/Indoor), sesso, categoria, regione
- Inserisci il **codice società FIDAL** (es. `BS318` per ATL. CHIARI)
- Clicca **⚡ Carica Graduatorie** per scaricare i dati direttamente da FIDAL
- In alternativa usa **📂 Importa risultati da CSV** per caricare dati manuali

### Schermata 2 — Prospetto CdS

#### Punti FIDAL — lookup automatico

I punti vengono cercati automaticamente nelle tabelle ufficiali FIDAL (JSON in `data/`):

- **Trovato**: valore esatto dalla tabella
- **Non esatto**: bucket immediatamente peggiore (approssimazione per eccesso/difetto — metodo FIDAL)
- **Fuori range**: prestazione sotto la soglia minima → punti = 0
- **Override**: il campo punti è sempre editabile manualmente

#### Preset programma CdS

Pulsanti per filtrare automaticamente le discipline fuori dal programma ufficiale della categoria selezionata (CF, CM, RF, RM).

#### Analisi Staffette

- Elenca tutte le staffette trovate con i componenti risolti
- Calcola delta punti (con vs senza staffetta)

#### ⚡ Calcola Ottimale (server-side, DFS Branch & Bound)

- Ricerca combinatoria esatta con algoritmo DFS + potatura Branch & Bound
- Testa tutti i sottoinsiemi di staffette (2ⁿ)
- Rispetta tutti i vincoli regolamentari per la categoria
- Chiamata a `/api/ottimizza` (Python) — non JavaScript

#### Selezione manuale (con vincoli live)

| Colore | Significato |
| --- | --- |
| 🟢 Verde | Selezionato |
| ➕ Bianco | Aggiungibile liberamente |
| 🟡 Giallo | Ultimo slot disponibile per quell'atleta |
| 🔴 Rosso | Bloccato (atleta esaurito o gara piena) |

#### ⬇ Stampa / PDF

Genera scheda stampabile A4 con tutti i risultati selezionati, punteggi e totale.

---

## Importazione da CSV

Il tool supporta l'importazione massiva di risultati tramite file `.csv`:

| Colonna | Obbligatoria | Note |
| --- | --- | --- |
| `categoria` | Sì | CF · CM · RF · RM |
| `gara` | Sì | Nome canonico dalla lista discipline (case-insensitive) |
| `tipo` | Sì | corsa · ostacoli · salto · lancio · staffetta |
| `prestazione` | Sì | `42.10`, `1:52.30`, `13.45` |
| `atleta` | Sì | Per staffetta: nomi separati da `/` o `,` |
| `punti` | No | Lasciare vuoto per lookup automatico |
| `vento` | No | es. `+1.2` |
| `piazzamento` | No | Numero intero |
| `citta` | No | Testo libero |
| `data` | No | `gg/mm/aaaa` |

Scarica il template con il pulsante **Scarica template CSV**.

---

## Regole per categoria

### Cadette / Cadetti (CF / CM)

- **13 risultati** nella scheda
- **≥ 10 gare diverse** (max 3 doppiate)
- Obbligatori: ≥ 2 lanci diversi + ≥ 2 salti diversi
- Max 2 risultati per gara individuale
- Ogni atleta max 2 volte (2 individuali o 1 individuale + staffetta)

### Ragazze / Ragazzi (RF / RM)

- **8 risultati** nella scheda
- **≥ 6 gare diverse** (max 2 doppiate)
- Obbligatori: ≥ 1 lancio + ≥ 1 salto diversi
- Max 2 risultati per gara individuale
- Ogni atleta max 1 volta individualmente (+ eventuale staffetta)

---

## Classifica regionale (`/api/proiezione/build`)

Il build scarica le graduatorie FIDAL per tutte le società della regione e calcola la scheda ottimale per ognuna:

- **SSE streaming** con barra di avanzamento in tempo reale
- **Cache incrementale**: le società invariate vengono riutilizzate senza re-fetch
- **Log di build**: ogni esecuzione scrive `logs/build_log_<params>_<timestamp>.txt`

---

## Distribuzione come eseguibile (.exe)

```bat
build.bat
```

Produce `dist\FIDAL_CDS_Tool.exe` — eseguibile autonomo Windows che include Python, Flask e tutte le librerie. Non richiede Python installato sul PC di destinazione.

---

## Struttura del progetto

```text
C:\...\CDS_Tool
 ├── core\
 │    ├── cds_optimizer.py   # Algoritmo DFS Branch & Bound
 │    ├── cds_utils.py       # Classificazione eventi, preset CdS
 │    └── cds_manual.py      # Persistenza manual_entries.json
 ├── data\                   # Tabelle punteggi FIDAL (CF, CM, RF, RM)
 ├── static\css\             # style.css
 ├── static\js\              # app.js (frontend vanilla JS)
 ├── templates\              # index.html (Jinja2)
 ├── tests\unit\             # Test unitari (pytest)
 ├── tests\integration\      # Test di integrazione (pytest)
 ├── dev\                    # Script one-shot di sviluppo (storico)
 ├── fidal_cds_tool.py       # Server Flask — API, scraper, business logic
 ├── requirements.txt
 ├── pytest.ini
 ├── ruff.toml
 ├── fidal_cds_tool.spec     # PyInstaller
 └── build.bat
```
