# FIDAL CdS Tool — Scheda Provinciale

Strumento web per la composizione ottimale della scheda di una società al **Campionato di Società** FIDAL (fase provinciale).
Scarica le graduatorie direttamente dalla banca dati FIDAL e aiuta a scegliere i migliori risultati rispettando tutti i vincoli regolamentari, per categoria.

---

## Requisiti

```bash
pip install flask requests beautifulsoup4
```

Python 3.8+

---

## Avvio

```bash
python fidal_cds_tool.py
```

Il browser si apre automaticamente su `http://localhost:5001`.
Per usare una porta diversa: `python fidal_cds_tool.py 8080`

---

## Utilizzo

### Schermata 1 — Parametri

| Campo | Descrizione |
| --- | --- |
| Anno | Anno sportivo (es. 2026) |
| Tipo attività | Outdoor (pista/campo) o Indoor |
| Sesso | Femminile / Maschile |
| Categoria | CF, CM, RF, RM, AF, AM, JF, JM, PF, PM, SF, SM |
| Regione | Regione FIDAL di riferimento |
| Nazionalità | Filtro atleti (default: tutti) |
| Vento | Filtro vento (default: tutti) |
| Limite risultati | Numero massimo di risultati per gara |
| Codice Società | Codice FIDAL della società (es. `BS318`) |

Clicca **⚡ Carica Graduatorie FIDAL** per scaricare i dati.

---

### Schermata 2 — Prospetto CdS

#### Preset programma CdS

Il pannello filtro gare include pulsanti preset per le categorie con programma tecnico definito (RM, RF, CM, CF). Il preset esclude automaticamente le discipline fuori dal programma ufficiale CdS; il pulsante **Reset** ripristina tutte le gare.

#### Punti FIDAL — lookup automatico da tabella

I punti vengono cercati automaticamente nella tabella ufficiale FIDAL (file JSON nella stessa cartella).

- **Trovato in tabella**: il campo viene pre-compilato con il valore esatto
- **Valore non esatto in tabella**: si usa il bucket immediatamente peggiore (approssimazione per eccesso per i tempi, per difetto per misure e lanci) — metodo ufficiale FIDAL
- **Fuori range**: la prestazione è sotto la soglia minima → punti = 0
- **Override manuale**: il campo punti è sempre editabile

#### Miglior prestazione `*`

L'asterisco rosso `*` indica la **miglior prestazione** di ogni disciplina nella graduatoria caricata.

#### Risultati a parità di punteggio

Quando due o più risultati non selezionati hanno lo stesso punteggio di un risultato selezionato nella stessa gara, vengono mostrati in un pannello giallo con un pulsante di scambio diretto (swap con un click).

#### Analisi Staffette

- Elenca tutte le staffette trovate con i componenti risolti dal nome abbreviato FIDAL
- Calcola se conviene includerla rispetto alle alternative individuali (delta pt)

#### ⚡ Calcola Ottimale

- Ricerca combinatoria esatta su tutte le combinazioni possibili
- Testa tutti i sottoinsiemi di staffette (2ⁿ)
- Garantisce il rispetto di tutti i vincoli regolamentari per la categoria
- Usa i punteggi da tabella (o manuali) per massimizzare il totale
- In caso di fallimento mostra un **pannello diagnostico** con i vincoli non soddisfatti e le gare disponibili

#### Selezione manuale

| Indicatore | Significato |
| --- | --- |
| 🟢 Verde | Selezionato |
| ➕ Bianco | Aggiungibile liberamente |
| 🟡 Giallo | Ultimo slot disponibile per quell'atleta |
| 🔴 Rosso | Bloccato (atleta esaurito o gara piena) |

Il pannello atleti mostra il contatore per ogni atleta nella selezione corrente.

L'eventuale banner di errore dell'ottimizzatore si azzera automaticamente appena la selezione manuale soddisfa tutti i vincoli.

#### ⬇ Stampa / PDF

Genera una scheda stampabile in formato A4 con tutti i risultati selezionati, punteggi e totale. Si apre una nuova finestra — dal dialogo di stampa del browser scegliere **Salva come PDF**.

---

## Regole implementate per categoria

### Cadette / Cadetti (CF / CM)

- **13 risultati** totali nella scheda
- **≥ 10 gare diverse** → massimo 3 gare "doppiate"
- **Obbligatori**: ≥ 2 lanci di discipline diverse + ≥ 2 salti di discipline diverse
- **Max 2 risultati** per gara individuale (staffetta esclusa)
- **Ogni atleta max 2 volte**: 2 individuali, oppure 1 individuale + 1 staffetta

### Ragazze / Ragazzi (RF / RM)

- **8 risultati** totali nella scheda
- **≥ 6 gare diverse** → massimo 2 gare "doppiate"
- **Obbligatori**: ≥ 1 lancio + ≥ 1 salto di discipline diverse
- **Max 2 risultati** per gara individuale (staffetta esclusa)
- **Ogni atleta max 1 volta individualmente** (+ eventuale staffetta)

---

## Tabelle punteggi disponibili

| File | Categoria | Gare |
| --- | --- | --- |
| `Cadette.json` | CF — Cadette Femmine | 23 |
| `Cadetti.json` | CM — Cadetti Maschi | 22 |
| `Ragazze.json` | RF — Ragazze | 20 |
| `Ragazzi.json` | RM — Ragazzi | 20 |

Per aggiungere altre categorie: inserire il JSON nella cartella del progetto con la stessa struttura e registrarlo in `_TABELLE` in `fidal_cds_tool.py`.

---

## Distribuzione come eseguibile (.exe)

Per distribuire il tool ad altri utenti **senza richiedere Python installato**, usa PyInstaller:

```bat
build.bat
```

Lo script installa automaticamente le dipendenze e produce `dist\FIDAL_CDS_Tool.exe` — un singolo file eseguibile autonomo che include Python, Flask e tutte le librerie. I file JSON delle tabelle punteggi sono incorporati nel bundle.

> **Requisiti per la build**: Python 3.8+ e connessione internet (solo per scaricare le dipendenze al primo avvio).
> Il file `.exe` risultante non richiede Python.

---

## Struttura del progetto

```text
C:\...\CDS_Tool ├── core\                   # Logica matematica
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

### Evoluzione algoritmica — Ottimizzatore v2 (DFS + Branch & Bound)

Il motore di ottimizzazione è stato riscritto in `core/cds_optimizer.py`:

- **DFS Branch & Bound**: esplora lo spazio combinatorio in profondità; il vettore `max_rem` (somma cumulativa dei punteggi massimi residui) permette di tagliare rami non migliorativi prima di completarli.
- **Normalizzazione stringhe**: gare e staffette con etichette variabili ("4X100" / "4 x 100") vengono unificate prima della ricerca, evitando il raddoppio fraudolento delle gare.
- **API server-side**: cliccando *Calcola Ottimale* la web app chiama `/api/ottimizza` (Python), non JavaScript; il risultato certificato torna in pochi secondi.

---

### Evoluzione algoritmica — Ottimizzatore v3 (Correttezza garantita)

Aggiornamento che risolve la sotto-ottimalità del v2 per le squadre più grandi:

**Problema v2**: un budget fisso di 20 s tagliava l'esplorazione prima che venisse trovata la combinazione ottimale (es. ATL. CHIARI 1964: score 7933 invece di 8122).

**Soluzioni introdotte**:

| Tecnica | Effetto |
| --- | --- |
| `_staff_combos`: staffetta esplorata **prima** di None | La soluzione ottimale (con staffetta) viene trovata ai primi ~30 tentativi; B&B scala tutto il resto |
| `ev_list` ordinato per score decrescente | `combinations()` genera prima i sottoinsiemi con gli eventi più redditizi |
| Staffetta in **testa** a `ev_full` | I combo con staffetta appaiono in posizione ~29 su 2000 invece di ~1000 su 3000 |
| Skip `ev_sub` senza staffetta nel loop WITH-staffetta | Dimezza le chiamate nell'iterazione con staffetta |
| **Outer upper-bound pruning** (greedy con vincolo atleta, top-n_sel candidati) | Salta interi ev_sub il cui massimo teorico non può battere best_total; usa top-13 per evento (non top-2) per evitare false potature quando i migliori atleti sono esauriti |
| **De-specific bound** (slot esatti per evento) | Salta singoli de-combo: 2 slot per eventi in de, 1 per gli altri — pota ~90% delle combinazioni doubles dopo il primo ottimale |
| `deadline=None` di default | Nessun taglio artificiale: il risultato è sempre il vero massimo |

**Risultati su casi reali (Lombardia CF 2026)**:

| Società | Atleti | Gare | Score vecchio | Score nuovo | Tempo |
| --- | --- | --- | --- | --- | --- |
| C.U.S. PAVIA | 29 | 16 | 9879 | **10097** | 4.0 s |
| ATL. BRUSAPORTO | 21 | 19 | 10685 | **10739** | 2.9 s |
| BERGAMO STARS ATLETICA | 23 | 18 | 9135 | **9743** | 9.0 s |
| CREMONA SPORTIVA ATL. ARVEDI | 19 | 17 | 9255 | **9821** | 9.9 s |
| ATHLETIC CLUB VILLASANTA | 22 | 15 | 9147 | **9311** | 1.2 s |

---

### Build database regionale (`/api/proiezione/build`)

Il build scarica le graduatorie FIDAL per tutte le società della regione e calcola la scheda ottimale per ognuna:

- **SSE streaming** con barra di avanzamento in tempo reale
- **Keepalive SSE** (`_opt_keepalive`): l'ottimizzatore gira in un thread separato; ogni 5 s viene emesso un commento SSE per mantenere viva la connessione anche durante calcoli lunghi
- **Refresh incrementale**: le società invariate (stesso numero di gare e punti totali) riutilizzano la cache senza ri-scaricare da FIDAL
- **Log di build**: ogni esecuzione scrive `logs/build_log_<params>_<timestamp>.txt` con il dettaglio di ogni società (punteggi, errori, ottimale trovato)

---

### Struttura `logs/`

```text
logs/
  build_log_2026_P_F_CF_LOM_20260605_202955.txt
  ...
```

I log sono ignorati da git (`.gitignore`). Ogni riga riporta: numero progressivo, nome società, numero atleti/gare/punti e ottimale calcolato (o motivo di esclusione).
