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
|---|---|
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

#### Punti FIDAL
I punti **non sono presenti** nelle graduatorie FIDAL e devono essere inseriti manualmente.
- Inserisci i punti direttamente nella colonna **Pt FIDAL** della tabella risultati
- I punti si possono inserire anche dopo aver selezionato un risultato nel prospetto
- Il pulsante **⚡ Calcola Ottimale** richiede che tutti i risultati abbiano un punteggio

#### Miglior prestazione `*`
L'asterisco rosso `*` indica la **miglior prestazione** di ogni disciplina nella graduatoria caricata.

#### Analisi Staffette
- Elenca tutte le staffette trovate con i componenti risolti dal nome abbreviato FIDAL
- Calcola se conviene includerla rispetto alle alternative individuali (delta pt)

#### ⚡ Calcola Ottimale
- Ricerca combinatoria esatta su tutte le combinazioni possibili (141 K+)
- Testa tutti i sottoinsiemi di staffette (2ⁿ)
- Garantisce il rispetto di tutti i vincoli regolamentari
- **Richiede** che tutti i punteggi siano stati inseriti prima di partire

#### Selezione manuale

| Colore riga | Significato |
|---|---|
| 🟢 Verde | Selezionato |
| ➕ Bianco | Aggiungibile liberamente |
| 🟡 Giallo | Ultimo slot disponibile per quell'atleta |
| 🔴 Rosso | Bloccato (atleta esaurito o gara piena) |

Il pannello atleti in cima alla tabella mostra il contatore `X/2` per ogni atleta selezionato.

---

## Regole implementate (Fase Provinciale)

- **13 risultati** totali nella scheda (art. c)
- **≥ 10 gare diverse** → massimo 3 gare "doppiate" (art. c)
- **Obbligatori**: almeno 2 lanci di discipline diverse + almeno 2 salti di discipline diverse (art. c)
- **Max 2 risultati** per gara individuale (staffetta esclusa) (art. c)
- **Ogni atleta max 2 volte**: 2 individuali, oppure 1 individuale + 1 staffetta (art. a)

---

## Struttura del progetto

```
fidal_cds_tool.py   # Server Flask + frontend HTML/CSS/JS (single file)
```
