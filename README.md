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

### Note sull'Evoluzione Algoritmica (Ottimizzatore v2)
L'applicativo supererà sempre la logica limitata degli algoritmi ingordi (Greedy). Il motore di base (stralciato e spostato in `core/cds_optimizer.py` e reso ad Oggetti) sfrutta ora un processo di calcolo combinatorio **DFS (Depth-First Search)** supportato da un tagliente **Branch and Bound**. 
Inoltre:
- È subentrata una fortissima politica di **normalizzazione stringhe**: gare e staffette variabilmente etichettate ("4X100" vs "4 x 100") vengono unificate a priori della ricerca per prevenire lo sdoppiamento fraudolento o incoerente.
- L'assegnazione DFS non procede "a casaccio per punti decrescenti" ma prova scambi fluidi valutando simultaneamente l'atleta intrappolato nei limiti di regolamento.
- La pagina Web non calcola più in modo posticcio sul browser con JS. Cliccando *Ottimale*, la web app innesca **l'esploratore Python via API Server (app.route api/ottimizza)** restituendo in un decimo di secondo la soluzione certificata.

### Note sull'Evoluzione Algoritmica (Ottimizzatore v2)
L'applicativo supererà sempre la logica limitata degli algoritmi ingordi (Greedy). Il motore di base (stralciato e spostato in `core/cds_optimizer.py` e reso ad Oggetti) sfrutta ora un processo di calcolo combinatorio **DFS (Depth-First Search)** supportato da un tagliente **Branch and Bound**. 
Inoltre:
- È subentrata una fortissima politica di **normalizzazione stringhe**: gare e staffette variabilmente etichettate ("4X100" vs "4 x 100") vengono unificate a priori della ricerca per prevenire lo sdoppiamento fraudolento o incoerente.
- L'assegnazione DFS non procede "a casaccio per punti decrescenti" ma prova scambi fluidi valutando simultaneamente l'atleta intrappolato nei limiti di regolamento.
- La pagina Web non calcola più in modo posticcio sul browser con JS. Cliccando *Ottimale*, la web app innesca **l'esploratore Python via API Server (app.route api/ottimizza)** restituendo in un decimo di secondo la soluzione certificata.
