import re

with open('fidal_cds_tool.py', 'r', encoding='utf-8') as f:
    text = f.read()

new_str = '''def _opt_assign_best(by_ev, ev_sub, dbl_set, incl_staff, n_sel, max_athl_ind):
    \"\"\"Backtracking DFS for optimal assignment to avoid greedy limits e incastri ciechi.\"\"\"
    ev_cap = {ev: (2 if ev in dbl_set else 1) for ev in ev_sub}
    sel_staff = []
    ac_total = {}
    for st in incl_staff:
        if st['ev'] in ev_sub:
            sel_staff.append(st)
            ev_cap[st['ev']] -= 1
            for k in _staff_athlete_keys(st.get('rawStaff', '')):
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

    counts = {ev: len(by_ev.get(ev, [])) for ev in set(slots_to_fill)}
    slots_to_fill.sort(key=lambda ev: (counts.get(ev, 0), ev))
    
    best_score = -1
    best_sel = None
    
    max_rem = [0] * len(slots_to_fill)
    for i in range(len(slots_to_fill)-1, -1, -1):
        ev = slots_to_fill[i]
        cands = by_ev.get(ev, [])
        m = cands[0].get('pts', 0) if cands else 0
        max_rem[i] = m + (max_rem[i+1] if i+1 < len(max_rem) else 0)

    ev_cands = {ev: by_ev.get(ev, [])[:15] for ev in set(slots_to_fill)}

    def dfs(slot_idx, current_score, current_sel, ac_t, ac_i, used_ids, last_cand_idx):
        nonlocal best_score, best_sel
        if slot_idx == len(slots_to_fill):
            if current_score > best_score:
                best_score = current_score
                best_sel = current_sel[:]
            return
            
        if current_score + max_rem[slot_idx] <= best_score:
            return
            
        ev = slots_to_fill[slot_idx]
        cands = ev_cands[ev]
        
        start_idx = 0
        if slot_idx > 0 and slots_to_fill[slot_idx] == slots_to_fill[slot_idx-1]:
            start_idx = last_cand_idx + 1

        for idx in range(start_idx, len(cands)):
            r = cands[idx]
            rid = id(r)
            if rid in used_ids: continue
                
            ak = _athlete_key(r.get('athlete', ''))
            if ac_t.get(ak, 0) >= 2: continue
            if ac_i.get(ak, 0) >= max_athl_ind: continue
            
            pts = r.get('pts') or 0
            
            ac_t[ak] = ac_t.get(ak, 0) + 1
            ac_i[ak] = ac_i.get(ak, 0) + 1
            used_ids.add(rid)
            current_sel.append(r)
            
            dfs(slot_idx + 1, current_score + pts, current_sel, ac_t, ac_i, used_ids, idx)
            
            current_sel.pop()
            used_ids.remove(rid)
            ac_t[ak] -= 1
            ac_i[ak] -= 1

    dfs(0, 0, [], ac_total, {}, set(), -1)
    
    if best_sel is None: return None, -1
    final_sel = sel_staff + best_sel
    return final_sel, sum(r.get('pts') or 0 for r in final_sel)

'''

text = re.sub(r'def _opt_assign_best\(.*?(?=\ndef _compute_optimal_py)', new_str + '\n', text, flags=re.DOTALL)

with open('fidal_cds_tool.py', 'w', encoding='utf-8') as f:
    f.write(text)

print('Dope!')
