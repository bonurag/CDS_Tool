# FIDAL CdS Tool — Scheda Provinciale Cadette/Cadetti

## Installazione

```bash
pip install flask requests beautifulsoup4
python fidal_cds_tool.py
```

Il browser si apre automaticamente su http://localhost:5001

## Funzionalità

### Schermata 1 — Parametri
- Seleziona anno, tipo attività, sesso, categoria, regione
- Inserisci il **codice società FIDAL** (es. `BS318` per ATL. CHIARI)
- L'URL FIDAL viene costruito in anteprima automaticamente
- Clicca **⚡ Carica Graduatorie** per scaricare i dati

### Schermata 2 — Prospetto CdS

**Analisi Staffette**
- Elenca tutte le staffette trovate (3 o 4 partecipanti)
- Risolve automaticamente i nomi abbreviati FIDAL → nomi completi
- Calcola se conviene includerla (con vs senza staffetta, delta pt)

**⚡ Calcola Ottimale**
- Ricerca combinatoria esatta (141K+ combinazioni)
- Testa tutti i sottoinsiemi possibili di staffette (2ⁿ)
- Garantisce: ≥2 lanci diversi, ≥2 salti diversi, ≥10 gare coperte
- Max 2 risultati per gara, max 2 volte per atleta

**Selezione manuale (con vincoli live)**
- 🟢 Verde: aggiungibile liberamente
- 🟡 Giallo: ultimo slot disponibile per quell'atleta
- 🔴 Rosso: bloccato (atleta maxed o gara piena)
- Pannello atleti mostra contatore per ciascuna (0/2, 1/2, 2/2)
- Alert se si cerca di aggiungere un risultato non valido

**Punti FIDAL**
- Stimati automaticamente (in arancio con ~) dalla performance relativa nella gara
- **Modificabili singolarmente** nel prospetto per inserire i valori reali FIDAL
- L'ottimale si ricalcola con i nuovi punti

## Regole implementate (Reg. Fasi Provinciali)
- 13 risultati totali (art. c)
- ≥10 gare diverse → max 3 gare "doppiate" (art. c)
- Obbligatori: 2 lanci diversi + 2 salti diversi (art. c)
- Max 2 risultati per gara individuale, staffetta esclusa (art. c)
- Ogni atleta max 2 volte: 2 individuali O 1 individuale + staffetta (art. a)
