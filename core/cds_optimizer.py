import time
import re
from itertools import combinations
from core.cds_utils import CdsUtils

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
        Generatore cartesiano per la prova di tutte le staffette:
        per ogni evento di staffetta prova a non scegliere nessuno (None) o uno qualsiasi tra le formazioni qualificate.
        """
        if not groups:
            yield []
            return
        first, *rest = groups
        for tail in CdsOptimizer._staff_combos(rest):
            yield [None] + tail
            for entry in first:
                yield [entry] + tail

    @staticmethod
    def opt_assign_best(by_ev, ev_sub, dbl_set, incl_staff, n_sel, max_athl_ind):
        """
        Backtracking DFS for optimal assignment. Evita i gridlock dell'approccio greedy
        testando la profondità e massimizzando il risultato globalmente.
        
        :param by_ev: Dizionario {evento: list[risultati]} degli eventi individuali
        :param ev_sub: Sottoinsieme esatto delle gare valutate in questo nodo (es. 10 eventi)
        :param dbl_set: Set di gare raddoppiate (es. in cui prenderemo 2 risultati)
        :param incl_staff: Lista di staffette fissate incluse nel calcolo
        :param n_sel: Numero di gare totali attese (es. 13)
        :param max_athl_ind: Massimo di risultati individuali imputabili al singolo atleta
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
    def compute_optimal(cls, results, cat):
        """
        Calcola la scheda ottimale per una società per la categoria specificata.
        Restituisce un dizionario coi risultati validati o None.
        
        :param results: Array coi dizionari risultati fidal parsati.
        :param cat: Stringa denominazione categoria (CF, CM, RF, RM, ecc)
        """
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

        ev_list = list(by_ev.keys())
        dbl = [ev for ev in ev_list if len(by_ev[ev]) >= 2]

        # Raggruppa staffette CdS (4x100) per tipo
        staff_by_ev = {}
        for r in staff:
            if re.search(r'4\s*[xX]\s*100(?!0)', r['ev']):
                staff_by_ev.setdefault(r['ev'], []).append(r)
        for ev in staff_by_ev:
            staff_by_ev[ev].sort(key=lambda r: r.get('pts') or 0, reverse=True)
        staff_groups = list(staff_by_ev.values())

        best_total, best_sel = -1, None

        for combo in cls._staff_combos(staff_groups):
            incl = [r for r in combo if r is not None]
            staff_evs_m = {r['ev'] for r in incl}
            ev_full = ev_list + [ev for ev in staff_evs_m if ev not in ev_list]
            dbl_full = dbl

            # Check numero eventi validi
            for n_ev in range(min_ev, min(n_sel, len(ev_full)) + 1):
                n_d = n_sel - n_ev
                if n_d > max_d: continue
                # Genera tutte le combinazioni eventi possibili
                for ev_sub in combinations(ev_full, n_ev):
                    if sum(CdsUtils.is_lancio(e) for e in ev_sub) < min_lanci: continue
                    if sum(CdsUtils.is_salto(e) for e in ev_sub) < min_salti:  continue
                    
                    dc = [e for e in ev_sub if e in dbl_full]
                    if len(dc) < n_d: continue
                    
                    for de in combinations(dc, n_d):
                        sel, total = cls.opt_assign_best(by_ev, ev_sub, set(de), incl, n_sel, max_athl_ind)
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
