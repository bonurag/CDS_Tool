import time
import re
from itertools import combinations
from math import comb
from core.cds_utils import CdsUtils

# Tetto massimo assoluto in secondi (fallback di sicurezza).
OPT_TIME_BUDGET_MAX = 300

class CdsOptimizer:
    """
    Classe core per il calcolo dell'ottimizzazione e selezione punteggi per i CdS FIDAL.
    Utilizza un esploratore combinatorio dello spazio delle gare (brute-force) supportato da
    un meccanismo DFS branch & bound per la ripartizione atleti intra-gara.
    
    Adatto per qualsiasi categoria provinciale (CF, CM, RF, RM) a patto
    che i metadati (limiti, massimali, regole) siano correttamente forniti in CAT_CONSTRAINTS.
    """

    CAT_CONSTRAINTS = {
        'CF': {'nSel':13, 'minEv':10, 'minLanci':2, 'minSalti':2, 'maxAthlInd':2, 'maxD':3},
        'CM': {'nSel':13, 'minEv':10, 'minLanci':2, 'minSalti':2, 'maxAthlInd':2, 'maxD':3},
        'RF': {'nSel':8,  'minEv':6,  'minLanci':1, 'minSalti':1, 'maxAthlInd':1, 'maxD':2},
        'RM': {'nSel':8,  'minEv':6,  'minLanci':1, 'minSalti':1, 'maxAthlInd':1, 'maxD':2},
    }

    @staticmethod
    def _staff_combos(groups):
        """
        Generatore cartesiano per la prova di tutte le staffette.
        Emette prima le combinazioni CON staffetta (punteggi più alti → B&B
        imposta subito una baseline elevata e pota il pass senza staffetta),
        poi le combinazioni senza (None).
        """
        if not groups:
            yield []
            return
        first, *rest = groups
        for tail in CdsOptimizer._staff_combos(rest):
            for entry in first:          # con staffetta — prima
                yield [entry] + tail
            yield [None] + tail          # senza staffetta — dopo

    @staticmethod
    def opt_assign_best(by_ev, ev_sub, dbl_set, incl_staff, n_sel, max_athl_ind, deadline=None):
        """
        Backtracking DFS for optimal assignment. Evita i gridlock dell'approccio greedy
        testando la profondità e massimizzando il risultato globalmente.

        :param by_ev: Dizionario {evento: list[risultati]} degli eventi individuali
        :param ev_sub: Sottoinsieme esatto delle gare valutate in questo nodo (es. 10 eventi)
        :param dbl_set: Set di gare raddoppiate (es. in cui prenderemo 2 risultati)
        :param incl_staff: Lista di staffette fissate incluse nel calcolo
        :param n_sel: Numero di gare totali attese (es. 13)
        :param max_athl_ind: Massimo di risultati individuali imputabili al singolo atleta
        :param deadline: Timestamp float (time.time()) oltre il quale interrompere il DFS
        :return: (lista dei risultati scelti, punteggio totale intero) oppure (None, -1) se sforo
        """
        ev_cap = {ev: (2 if ev in dbl_set else 1) for ev in ev_sub}
        sel_staff = []
        ac_total = {}
        for st in incl_staff:
            if st['ev'] in ev_sub:
                sel_staff.append(st)
                ev_cap[st['ev']] -= 1
                for k in CdsUtils.staff_athlete_keys(st.get('rawStaff', '')):
                    ac_total[k] = ac_total.get(k, 0) + 1

        slots_to_fill = []
        for ev in ev_sub:
            count = ev_cap.get(ev, 0)
            for _ in range(count):
                slots_to_fill.append(ev)

        if not slots_to_fill:
            if len(sel_staff) == n_sel:
                return sel_staff, sum(r.get('pts') or 0 for r in sel_staff)
            return None, -1

        # Raggruppa preferendo gli slot con meno candidati (più rigidi da piazzare per l'algoritmo)
        counts = {ev: len(by_ev.get(ev, [])) for ev in set(slots_to_fill)}
        slots_to_fill.sort(key=lambda ev: (counts.get(ev, 0), ev))

        best_score = -1
        best_sel = None

        # Max_rem: vettore punteggi massimi rimanenti per Branch and Bound. Taglia i rami svantaggiosi.
        max_rem = [0] * len(slots_to_fill)
        for i in range(len(slots_to_fill)-1, -1, -1):
            ev = slots_to_fill[i]
            cands = by_ev.get(ev, [])
            m = cands[0].get('pts', 0) if cands else 0
            max_rem[i] = m + (max_rem[i+1] if i+1 < len(max_rem) else 0)

        # Usiamo al max la top-15 dei risultati per accelerare il backtracking
        ev_cands = {ev: by_ev.get(ev, [])[:15] for ev in set(slots_to_fill)}

        def dfs(slot_idx, current_score, current_sel, ac_t, ac_i, used_ids, last_cand_idx):
            nonlocal best_score, best_sel
            if deadline is not None and time.time() > deadline:
                return
            if slot_idx == len(slots_to_fill):
                if current_score > best_score:
                    best_score = current_score
                    best_sel = current_sel[:]
                return

            # Pruning
            if current_score + max_rem[slot_idx] <= best_score:
                return

            ev = slots_to_fill[slot_idx]
            cands = ev_cands[ev]

            # Rottura di simmetria per slot doppi così da dimezzare prove ridondanti identiche
            start_idx = 0
            if slot_idx > 0 and slots_to_fill[slot_idx] == slots_to_fill[slot_idx-1]:
                start_idx = last_cand_idx + 1

            for idx in range(start_idx, len(cands)):
                r = cands[idx]
                rid = id(r)
                if rid in used_ids: continue

                ak = CdsUtils.athlete_key(r.get('athlete', ''))
                if ac_t.get(ak, 0) >= 2: continue
                if ac_i.get(ak, 0) >= max_athl_ind: continue

                pts = r.get('pts') or 0

                # Applica stato nodo
                ac_t[ak] = ac_t.get(ak, 0) + 1
                ac_i[ak] = ac_i.get(ak, 0) + 1
                used_ids.add(rid)
                current_sel.append(r)

                # Branch
                dfs(slot_idx + 1, current_score + pts, current_sel, ac_t, ac_i, used_ids, idx)

                # Rollback stato nodo (backtrack)
                current_sel.pop()
                used_ids.remove(rid)
                ac_t[ak] -= 1
                ac_i[ak] -= 1

        dfs(0, 0, [], ac_total, {}, set(), -1)

        if best_sel is None: return None, -1
        final_sel = sel_staff + best_sel
        return final_sel, sum(r.get('pts') or 0 for r in final_sel)

    @classmethod
    def _estimate_budget(cls, results, cat):
        """
        Calcola il budget di tempo adattivo in secondi in funzione della complessità
        combinatoria del problema (numero di eventi e candidati per gara).

        Logica:
        - Conta il numero totale di chiamate a opt_assign_best che il loop esterno
          produrrebbe: Σ C(n_gare, k) × Σ C(n_doppie, j) per ogni k,j valido.
        - Il costo per chiamata dipende dall'efficacia del Branch & Bound:
          più candidati per gara → pruning più aggressivo → ogni chiamata è più veloce.
          Calibrato empiricamente su ~9 atleti / 14 gare / ~3 cand/gara ≈ 25 s.
        - Il risultato viene raddoppiato come margine di sicurezza e clampato tra
          15 s (minimo utile) e OPT_TIME_BUDGET_MAX.
        """
        C = cls.CAT_CONSTRAINTS.get(cat, cls.CAT_CONSTRAINTS['CF'])
        n_sel, min_ev, max_d = C['nSel'], C['minEv'], C['maxD']

        ind = [r for r in results if not r.get('isStaffetta') and r.get('pts_ok')]
        by_ev = {}
        for r in ind:
            by_ev.setdefault(r['ev'], []).append(r)

        n_ev_tot = len(by_ev)
        if n_ev_tot < min_ev:
            return 5  # non competitiva, esce quasi subito

        n_dbl = sum(1 for v in by_ev.values() if len(v) >= 2)
        # media candidati per gara, capped a 15 (top-15 usati nel DFS)
        avg_cands = sum(min(len(v), 15) for v in by_ev.values()) / n_ev_tot

        # Numero totale di chiamate al DFS (loop esterno: eventi × doppie)
        total_calls = 0
        for k in range(min_ev, min(n_sel, n_ev_tot) + 1):
            nd = n_sel - k
            if nd > max_d:
                continue
            total_calls += comb(n_ev_tot, k) * sum(comb(n_dbl, j) for j in range(nd + 1))

        # Costo per chiamata: 0.5 ms base, ridotto con B&B più efficace
        # (avg_cands / 3)^2.5 calibrato su punto noto: 3 cand/gara → 0.5ms/call
        bb_factor = max((avg_cands / 3.0) ** 2.5, 0.3)
        cost_per_call = 5e-4 / bb_factor

        estimated = total_calls * cost_per_call
        # Margine di sicurezza ×2, minimo 15 s, massimo OPT_TIME_BUDGET_MAX
        return max(15, min(OPT_TIME_BUDGET_MAX, int(estimated * 2) + 10))

    @classmethod
    def compute_optimal(cls, results, cat, time_budget=None):
        """
        Calcola la scheda ottimale per una società per la categoria specificata.
        Restituisce un dizionario coi risultati validati o None.

        :param results: Array coi dizionari risultati fidal parsati.
        :param cat: Stringa denominazione categoria (CF, CM, RF, RM, ecc)
        :param time_budget: Secondi massimi (None = nessun limite, risultato garantito ottimale).
                           Usare solo se si accetta un risultato sub-ottimale in cambio di velocità.
        """
        deadline = (time.time() + time_budget) if time_budget is not None else None
        C = cls.CAT_CONSTRAINTS.get(cat, cls.CAT_CONSTRAINTS['CF'])
        n_sel, min_ev = C['nSel'], C['minEv']
        min_lanci, min_salti = C['minLanci'], C['minSalti']
        max_athl_ind, max_d = C['maxAthlInd'], C['maxD']

        cds_prog = CdsUtils.get_cds_program(cat)
        def _in_cds(r):
            return not cds_prog or cds_prog(r.get('ev', ''))

        ind   = [r for r in results if not r.get('isStaffetta') and r.get('pts_ok') and _in_cds(r)]
        staff = [r for r in results if r.get('isStaffetta')     and r.get('pts_ok') and _in_cds(r)]
        if not ind: return None

        # Raggruppa individuale per evento, top-25 max all'ingresso per non incastrarti in calcoli cicolpici
        by_ev = {}
        for r in ind:
            by_ev.setdefault(r['ev'], []).append(r)

        for ev in by_ev:
            by_ev[ev].sort(key=lambda r: r.get('pts') or 0, reverse=True)
            by_ev[ev] = by_ev[ev][:25]

        # Ordina gli eventi per punteggio massimo decrescente: combinations() genererà
        # prima i sottoinsiemi con gli eventi più redditizi, il B&B imposta subito
        # una baseline alta e pota aggressivamente il resto.
        ev_list = sorted(by_ev.keys(), key=lambda ev: by_ev[ev][0].get('pts') or 0, reverse=True)
        dbl = [ev for ev in ev_list if len(by_ev[ev]) >= 2]

        # Raggruppa staffette CdS (4x100) per tipo.
        # NORMALIZZA il nome prima del raggruppamento: "Staffetta 4 X 100",
        # "4X100", "4 x 100" ecc. sono la stessa gara — devono stare nello
        # stesso gruppo così l'optimizer ne sceglie AL MASSIMO UNA.
        _STAFF_4X100 = 'Staffetta 4X100'
        staff_by_ev = {}
        for r in staff:
            if re.search(r'4\s*[xX]\s*100(?!0)', r['ev']):
                # Copia shallow con nome normalizzato: garantisce coerenza tra
                # staff_evs_m, ev_full, ev_sub e il check in opt_assign_best.
                r_norm = dict(r)
                r_norm['ev'] = _STAFF_4X100
                staff_by_ev.setdefault(_STAFF_4X100, []).append(r_norm)
        for ev in staff_by_ev:
            staff_by_ev[ev].sort(key=lambda r: r.get('pts') or 0, reverse=True)
        staff_groups = list(staff_by_ev.values())

        best_total, best_sel = -1, None

        for combo in cls._staff_combos(staff_groups):
            if deadline is not None and time.time() > deadline:
                break
            incl = [r for r in combo if r is not None]
            staff_evs_m = {r['ev'] for r in incl}
            # Staffetta in TESTA a ev_full: combinations() genera prima i sottoinsiemi
            # che la includono (posizione ~30 su 2000 vs ~1000 su 3000), trovando
            # subito la soluzione ottimale e permettendo a B&B di potare il resto.
            staff_extra = [ev for ev in staff_evs_m if ev not in ev_list]
            ev_full = staff_extra + ev_list if staff_extra else ev_list
            dbl_full = dbl

            # Check numero eventi validi
            for n_ev in range(min_ev, min(n_sel, len(ev_full)) + 1):
                if deadline is not None and time.time() > deadline:
                    break
                n_d = n_sel - n_ev
                if n_d > max_d: continue
                # Genera tutte le combinazioni eventi possibili.
                # Con staffetta: salta i sottoinsiemi che non la includono (già
                # coperti dall'iterazione senza staffetta).
                for ev_sub in combinations(ev_full, n_ev):
                    if deadline is not None and time.time() > deadline:
                        break
                    if staff_evs_m and not staff_evs_m.issubset(ev_sub):
                        continue
                    if sum(CdsUtils.is_lancio(e) for e in ev_sub) < min_lanci: continue
                    if sum(CdsUtils.is_salto(e) for e in ev_sub) < min_salti:  continue

                    dc = [e for e in ev_sub if e in dbl_full]
                    if len(dc) < n_d: continue

                    # ── Outer upper-bound pruning (greedy con vincolo atleta) ──
                    # Assegna greedily i migliori risultati disponibili rispettando
                    # il vincolo "ogni atleta max 2 apparizioni totali".
                    # Questo bound è molto più stretto di quello senza vincoli e pota
                    # la grande maggioranza dei ev_sub che non possono battere best_total.
                    staff_in_sub = [r for r in incl if r.get('ev') in set(ev_sub)]
                    n_ind = n_sel - len(staff_in_sub)
                    staff_ub = sum(r.get('pts', 0) for r in staff_in_sub)
                    # Conta gli atleti già usati nella staffetta (base immutabile)
                    ac_staff_base = {}
                    for st in staff_in_sub:
                        for k in CdsUtils.staff_athlete_keys(st.get('rawStaff', '')):
                            ac_staff_base[k] = ac_staff_base.get(k, 0) + 1
                    # Tutti i candidati individuali (top-n_sel per evento) ordinati per pts desc.
                    # top-n_sel (non top-2) per evitare false potature quando i top-2 sono
                    # esauriti per vincolo atleta e l'ottimale usa il 3°/4°/... candidato.
                    staff_evs_sub = {s.get('ev') for s in staff_in_sub}
                    cands_ub = sorted(
                        (r for ev in ev_sub if ev not in staff_evs_sub
                         for r in by_ev.get(ev, [])[:n_sel]),
                        key=lambda r: r.get('pts', 0) or 0, reverse=True
                    )
                    # Outer bound: ogni evento può contribuire fino a 2 slot
                    ac_ub = dict(ac_staff_base)
                    ub_pts = staff_ub
                    n_filled = 0
                    seen_ub = set()
                    for r in cands_ub:
                        if n_filled >= n_ind:
                            break
                        rid = id(r)
                        if rid in seen_ub:
                            continue
                        ak = CdsUtils.athlete_key(r.get('athlete', ''))
                        if ac_ub.get(ak, 0) >= 2:
                            continue
                        ub_pts += r.get('pts', 0) or 0
                        ac_ub[ak] = ac_ub.get(ak, 0) + 1
                        seen_ub.add(rid)
                        n_filled += 1
                    if ub_pts <= best_total:
                        continue
                    # ──────────────────────────────────────────────────────────

                    for de in combinations(dc, n_d):
                        # ── De-specific bound (più stretto dell'outer bound) ──
                        # Usa esattamente i slot previsti da de (2 se in de, 1 altrimenti).
                        # Parte da ac_staff_base (solo staffetta), non da ac_ub modificato.
                        de_set = set(de)
                        ev_slots = {ev: (2 if ev in de_set else 1)
                                    for ev in ev_sub if ev not in staff_evs_sub}
                        ub_de = staff_ub
                        ac_de = dict(ac_staff_base)
                        n_de_filled = 0
                        seen_de = set()
                        for r in cands_ub:
                            if n_de_filled >= n_ind:
                                break
                            rid = id(r)
                            if rid in seen_de:
                                continue
                            ev_r = r.get('ev', '')
                            if ev_slots.get(ev_r, 0) <= 0:
                                continue
                            ak = CdsUtils.athlete_key(r.get('athlete', ''))
                            if ac_de.get(ak, 0) >= 2:
                                continue
                            ub_de += r.get('pts', 0) or 0
                            ac_de[ak] = ac_de.get(ak, 0) + 1
                            ev_slots[ev_r] -= 1
                            seen_de.add(rid)
                            n_de_filled += 1
                        if ub_de <= best_total:
                            continue
                        # ─────────────────────────────────────────────────────
                        sel, total = cls.opt_assign_best(by_ev, ev_sub, de_set, incl, n_sel, max_athl_ind, deadline)
                        if sel and total > best_total:
                            best_total, best_sel = total, sel

        if best_sel is None: return None
        return {
            'score': best_total,
            'ids': [r.get('id') for r in best_sel],
            'sel': [{'id': r.get('id'), 'ev': r['ev'], 'athlete': r.get('athlete',''),
                     'pts': r.get('pts', 0), 'perf': r.get('perf',''),
                     'isStaffetta': r.get('isStaffetta', False)} for r in best_sel],
            'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }
