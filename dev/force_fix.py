with open('fidal_cds_tool.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Find index of sync function computeOptimal()
start = text.find('async function computeOptimal(){')
end = text.find('// ── CLASSIFICA')

new_optimal = '''async function computeOptimal(){
  const missing=activeAll().filter(r=>userPts[r.id]===undefined&&!r.pts_ok);
  if (missing.length>0){
    setNoteEst(⚠ \\ risultat\\ senza punteggio — inserisci i punti FIDAL per tutti prima di calcolare., true);
    return;
  }
  setNoteEst('');

  const _lbar  = document.getElementById('loading-bar-track');
  const _lfill = document.getElementById('loading-bar-fill');
  const _lsub  = document.getElementById('loading-sub');
  
  _setLoadingMsg('Analisi algoritmica server-side DFS Branch&Bound in corso…');
  if (_lbar)  _lbar.style.display = '';
  if (_lfill) { _lfill.classList.remove('indeterminate'); _lfill.classList.add('indeterminate'); _lfill.style.width = '100%'; }
  document.getElementById('loading').classList.remove('hidden');

  try {
      const active = activeAll();
      const C=getC();
      
      const payload = {
          categoria: currentCategoria,
          data: active.map(r => {
            const ret = {...r};
            ret.pts = pts(r);
            return ret;
          })
      };
      
      const resp = await fetch('/api/ottimizza', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
      });
      
      const res = await resp.json();
      if (!res.ok) throw new Error(res.error);
      
      selectedIds.clear();
      
      if (!res.optimal || !res.optimal.sel || res.optimal.sel.length !== C.nSel){
          setNoteEst(⚠ Impossibile trovare \\ risultati validi che incastrino tutti i vincoli., true);
      } else {
          setNoteEst('');
          staffAnalysis = []; 
          res.optimal.sel.forEach(r => selectedIds.add(Number(r.id)));
      }
      
      renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
      
  } catch(e) {
      alert("Errore calcolo ottimale: " + e.message);
  } finally {
      document.getElementById('loading').classList.add('hidden');
      if (_lbar)  _lbar.style.display='none';
  }
}

// Funzioni Legacy rimosse
function _staffCombos(groups) { return []; }
function searchOptimal(inclStaff, maxDoubles) { return {sel:[], total:0}; }
function assignBest(evSub, dblSet, inclStaff) { return {sel:[], total:0}; }

'''

text = text[:start] + new_optimal + text[end:]

with open('fidal_cds_tool.py', 'w', encoding='utf-8') as f:
    f.write(text)
print('COMPLETELY FIXED!')
