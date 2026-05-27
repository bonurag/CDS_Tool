# FIDAL CdS Tool — Scheda Provinciale

Strumento web per la composizione ottimale della scheda di una società al **Campionato di Società** FIDAL (fase provinciale).
Scarica le graduatorie direttamente dalla banca dati FIDAL e aiuta a scegliere i 13 risultati migliori rispettando tutti i vincoli regolamentari.

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
| Categoria | CF, CM, AF, AM, JF, JM, PF, PM, SF, SM |
| Regione | Regione FIDAL di riferimento |
| Nazionalità | Filtro atleti (default: tutti) |
| Vento | Filtro vento (default: tutti) |
| Limite risultati | Numero massimo di risultati per gara |
| Codice Società | Codice FIDAL della società (es. `BS318`) |

Clicca **⚡ Carica Graduatorie FIDAL** per scaricare i dati.

---

### Schermata 2 — Prospetto CdS

#### Punti FIDAL — lookup automatico da tabella

I punti vengono cercati automaticamente nella tabella ufficiale FIDAL (file JSON nella stessa cartella).

- **Trovato in tabella**: il campo viene pre-compilato con il valore esatto
- **Valore non esatto in tabella**: si usa il bucket immediatamente peggiore (approssimazione per eccesso per i tempi, per difetto per misure e lanci) — metodo ufficiale FIDAL
- **Fuori range**: la prestazione è sotto la soglia minima della tabella → punti = 0
- **Override manuale**: il campo punti è sempre editabile per correggere o inserire manualmente

Il pulsante **⚡ Calcola Ottimale** blocca solo se esistono risultati per cui non è disponibile né un valore da tabella né un inserimento manuale.

#### Tabelle punteggi disponibili

| File             | Categoria             | Note                      |
| ---------------- | --------------------- | ------------------------- |
| `Cadette.json`   | CF — Cadette Femmine  | 23 gare, lookup completo  |

Per aggiungere altre categorie: inserire il JSON nella cartella del progetto con la stessa struttura e registrarlo in `_TABELLE` in `fidal_cds_tool.py`.

#### Miglior prestazione `*`

L'asterisco rosso `*` indica la **miglior prestazione** di ogni disciplina nella graduatoria caricata.

#### Analisi Staffette

- Elenca tutte le staffette trovate con i componenti risolti dal nome abbreviato FIDAL
- Calcola se conviene includerla rispetto alle alternative individuali (delta pt)

#### ⚡ Calcola Ottimale

- Ricerca combinatoria esatta su tutte le combinazioni possibili
- Testa tutti i sottoinsiemi di staffette (2ⁿ)
- Garantisce il rispetto di tutti i vincoli regolamentari
- Usa i punteggi da tabella (o manuali) per massimizzare il totale

#### ⬇ Stampa / PDF

Genera una scheda stampabile in formato A4 con tutti i risultati selezionati, punteggi e totale. Si apre una nuova finestra — dal dialogo di stampa del browser scegliere **Salva come PDF**.

#### Selezione manuale

| Indicatore | Significato |
| --- | --- |
| 🟢 Verde | Selezionato |
| ➕ Bianco | Aggiungibile liberamente |
| 🟡 Giallo | Ultimo slot disponibile per quell'atleta |
| 🔴 Rosso | Bloccato (atleta esaurito o gara piena) |

Il pannello atleti mostra il contatore `X/2` per ogni atleta nella selezione corrente.

---

## Regole implementate (Fase Provinciale)

- **13 risultati** totali nella scheda (art. c)
- **≥ 10 gare diverse** → massimo 3 gare "doppiate" (art. c)
- **Obbligatori**: almeno 2 lanci di discipline diverse + almeno 2 salti di discipline diverse (art. c)
- **Max 2 risultati** per gara individuale (staffetta esclusa) (art. c)
- **Ogni atleta max 2 volte**: 2 individuali, oppure 1 individuale + 1 staffetta (art. a)

---

## Distribuzione come eseguibile (.exe)

Per distribuire il tool ad altri utenti **senza richiedere Python installato**, usa PyInstaller:

```bat
build.bat
```

Lo script installa automaticamente le dipendenze e produce `dist\FIDAL_CDS_Tool.exe` — un singolo file eseguibile autonomo che include Python, Flask e tutte le librerie. L'utente fa doppio click e il browser si apre automaticamente.

> **Requisiti per la build**: Python 3.8+ e connessione internet (solo per scaricare le dipendenze al primo avvio).
> Il file `.exe` risultante non richiede Python.

---

## Struttura del progetto

```text
fidal_cds_tool.py      # Server Flask + frontend HTML/CSS/JS (single file)
Cadette.json           # Tabella punteggi FIDAL — Cadette (CF)
fidal_cds_tool.spec    # Configurazione PyInstaller per la build .exe
build.bat              # Script di build Windows
```
