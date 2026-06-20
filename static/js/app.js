// ── COSTANTI ────────────────────────────────────────────
const LANCIO_EVS = new Set(['peso','martello','giavellotto','disco','lancio','vortex','palla']);
const SALTO_EVS  = new Set(['lungo','triplo','alto','asta','salto']);
const TYPE_LBL   = {corsa:'Corsa',ostacoli:'Ostacoli',salto:'Salto',lancio:'Lancio',staffetta:'Staffetta'};

// ── PROGRAMMI TECNICI CdS (per preset filtro gare) ──────
// Helper: vero se l'evento è un ostacolo (FIDAL usa sia "ostacoli" che "hs"/"Hs")
const _isOstac = e => e.includes('ostac') || e.includes(' hs') || e.includes('hs ') || e.startsWith('hs');

const CDS_PROGRAMS = {
  // Ragazzi/Ragazze: 60hs, 60, 1000, Marcia 2km, Alto, Lungo, Peso 2kg, Vortex, 4x100
  RM: ev => {
    const e = ev.toLowerCase();
    return (/(?<!\d)60(?!\d)/.test(e) && (e.includes('piani') || _isOstac(e))) ||
           (/(?<!\d)1000(?!\d)/.test(e) && !e.includes('3x') && !e.includes('3 x')) ||
           e.includes('marcia') || e.includes('in alto') || e.includes('in lungo') ||
           (e.includes('peso') && e.includes('2')) ||
           e.includes('vortex') ||
           (e.includes('staffetta') && /4\s*[xX]\s*100(?!0)/.test(e));
  },
  // Cadetti: 100hs, 80, 300hs, 300, 1000, 2000, 1200 siepi, Asta, Alto, Lungo, Triplo,
  //          Peso 4kg, Martello 4kg, Disco 1.5kg, Giavellotto 600g, 4x100, Marcia 5km
  CM: ev => {
    const e = ev.toLowerCase();
    return (e.includes('80') && e.includes('piani')) ||
           (/(?<!\d)100(?!\d)/.test(e) && _isOstac(e)) ||
           (e.includes('300') && (_isOstac(e) || e.includes('piani'))) ||
           (/(?<!\d)1000(?!\d)/.test(e) && !e.includes('3x') && !e.includes('3 x')) ||
           e.includes('2000') || e.includes('1200') ||
           e.includes('asta') || e.includes('in alto') || e.includes('in lungo') ||
           e.includes('triplo') ||
           (e.includes('peso') && e.includes('4')) ||
           e.includes('martello') || e.includes('disco') || e.includes('giavellott') ||
           (e.includes('staffetta') && /4\s*[xX]\s*100(?!0)/.test(e)) ||
           e.includes('marcia');
  },
};
CDS_PROGRAMS.RF = CDS_PROGRAMS.RM; // stesso programma tecnico dei Ragazzi
// Cadette: 80hs, 80, 300hs, 300, 1000, 2000, 1200 siepi, Asta, Alto, Lungo, Triplo,
//          Peso 3kg, Martello 3kg, Disco 1kg, Giavellotto 400g, Staffetta 4x100, Marcia 3km
CDS_PROGRAMS.CF = ev => {
  const e = ev.toLowerCase();
  return (e.includes('80') && (e.includes('piani') || _isOstac(e))) ||
         (e.includes('300') && (_isOstac(e) || e.includes('piani'))) ||
         (/(?<!\d)1000(?!\d)/.test(e) && !e.includes('3x') && !e.includes('3 x')) ||
         e.includes('2000') || e.includes('1200') ||
         e.includes('asta') || e.includes('in alto') || e.includes('in lungo') ||
         e.includes('triplo') ||
         e.includes('peso') || e.includes('martello') || e.includes('disco') ||
         e.includes('giavellott') ||
         (e.includes('staffetta') && /4\s*[xX]\s*100(?!0)/.test(e)) ||
         e.includes('marcia');
};

// ── VINCOLI PER CATEGORIA ────────────────────────────────
// nSel: risultati totali | minEv: gare minime distinte
// minLanci/minSalti: discipline obbligatorie | maxAthlInd: max gare individuali per atleta
const CONSTRAINTS = {
  default: { nSel:13, minEv:10, minLanci:2, minSalti:2, maxAthlInd:2 },
  RM:      { nSel:8,  minEv:6,  minLanci:1, minSalti:1, maxAthlInd:1 },
  RF:      { nSel:8,  minEv:6,  minLanci:1, minSalti:1, maxAthlInd:1 },
};
function getC(){ return CONSTRAINTS[currentCategoria] || CONSTRAINTS.default; }

// ── STATO ───────────────────────────────────────────────
let ALL = [], selectedIds = new Set(), userPts = {}, staffAnalysis = [], excludedEvs = new Set(), topCombinations = [];
let _societiesMeta = {}; // pre-calcolato dal build, caricato con la proiezione
let _classificaRanked = null; // ultimo ranked salvato da _renderClassifica, usato per export
let _tabelleCache = {};   // cache tabelle punteggi: categoria → {gara → {perf → pts}}

async function _getTabellaCategoria(cat) {
  if (_tabelleCache[cat]) return _tabelleCache[cat];
  try {
    const r = await fetch(`/api/tabelle?categoria=${cat}`);
    const j = await r.json();
    if (j.ok) _tabelleCache[cat] = j.tabelle[cat] || {};
  } catch(e) { _tabelleCache[cat] = {}; }
  return _tabelleCache[cat] || {};
}
let currentCategoria = '', currentAnno = 2026, savedManualEntries = [];
let unavailableAthletes = new Set(), minDateFilter = null;
let _fidalConnected = false, _fidalCheckTimer = null;
let _currentProiezioneP = null; // parametri dell'ultima proiezione caricata
let currentSocieta = '';        // codice società corrente (stringa vuota in proiezione)
// Impostazioni persistenti (localStorage)
let autoLoadManual = localStorage.getItem('cds_autoLoadManual') !== 'false';
let autoPresetCds  = localStorage.getItem('cds_autoPresetCds')  !== 'false';
let societaList = [];
let isProiezione = false;

function athleteDisplay(r, short=false){
  if (r.isStaffetta) return (r.staffAthl||[r.athlete]).join(' / ');
  if (r.athlete_url && !short)
    return `<a class="athl-link" href="${r.athlete_url}" target="_blank" rel="noopener">${r.athlete}</a>`;
  return r.athlete;
}
let sortCol = -1, sortAsc = true;

function isLancio(ev){
  // Keyword ha priorità (override per eventi FIDAL classificati erroneamente)
  if ([...LANCIO_EVS].some(k=>ev.toLowerCase().includes(k))) return true;
  const r=activeAll().find(x=>x.ev===ev);
  return !!(r && r.type==='lancio');
}
function isSalto(ev){
  if ([...SALTO_EVS].some(k=>ev.toLowerCase().includes(k))) return true;
  const r=activeAll().find(x=>x.ev===ev);
  return !!(r && r.type==='salto');
}
function pts(r){ return userPts[r.id] !== undefined ? userPts[r.id] : r.pts; }
function activeAll(){
  return ALL.filter(r=>{
    if (excludedEvs.has(r.ev)) return false;
    const athls = r.isStaffetta ? (r.staffAthl||[r.athlete]) : [r.athlete];
    if (athls.some(a=>unavailableAthletes.has(a))) return false;
    if (minDateFilter && r.data){
      const d = parseResultDate(r.data);
      if (d && d < minDateFilter) return false;
    }
    // In proiezione: escludi risultati senza punteggio tabella
    if (isProiezione && !r.pts_ok && userPts[r.id]===undefined) return false;
    return true;
  });
}

// ── CATEGORIA PICKLIST ──────────────────────────────────
// Limitato alle categorie con tabelle punteggi FIDAL disponibili
const CATS={
  F:[{v:'CF',l:'Cadette (CF)'},{v:'RF',l:'Ragazze (RF)'}],
  M:[{v:'RM',l:'Ragazzi (RM)'},{v:'CM',l:'Cadetti (CM)'}],
};
function updateCatOptions(){
  const sesso=document.getElementById('f-sesso').value;
  const sel=document.getElementById('f-cat');
  const prev=sel.value;
  sel.innerHTML=CATS[sesso].map(c=>`<option value="${c.v}">${c.l}</option>`).join('');
  if ([...sel.options].some(o=>o.value===prev)) sel.value=prev;
  updateUrlPreview();
}

// ── URL PREVIEW ─────────────────────────────────────────
function updateUrlPreview(){
  const p = getFormParams();
  const q = new URLSearchParams({...p, gara:'0', tipologia_estrazione:'2', submit:'Invia'});
  document.getElementById('url-preview').textContent =
    'https://www.fidal.it/graduatorie.php?' + q.toString();
}
['f-anno','f-tipo','f-sesso','f-cat','f-reg','f-naz','f-vento','f-limite','f-societa']
  .forEach(id => document.getElementById(id).addEventListener('change', updateUrlPreview));
document.getElementById('f-societa').addEventListener('input', updateUrlPreview);
// Ricarica lista società quando cambia la regione
document.getElementById('f-reg').addEventListener('change', () => {
  document.getElementById('f-societa-name').value = '';
  loadSocieta(document.getElementById('f-reg').value);
});
updateCatOptions(); // popola la picklist categorie al caricamento
loadSocieta(document.getElementById('f-reg').value); // carica società per la regione default
setTimeout(checkFidalConnection, 0); // verifica connessione FIDAL dopo il parsing dello script

// ── RICERCA SOCIETÀ ──────────────────────────────────────
async function loadSocieta(regione){
  const statusEl = document.getElementById('societa-status');
  const dl       = document.getElementById('societa-datalist');
  societaList = [];
  dl.innerHTML = '';
  statusEl.textContent = 'Caricamento…';
  try {
    const resp = await fetch(`/api/societa?regione=${encodeURIComponent(regione)}`);
    const json = await resp.json();
    if (!json.ok) throw new Error(json.error);
    societaList = json.data;
    dl.innerHTML = societaList.map(s =>
      `<option value="${s.nome.replace(/"/g,'&quot;')}">`
    ).join('');
    statusEl.textContent = `${societaList.length} società`;
  } catch(e) {
    statusEl.textContent = `⚠ ${e.message}`;
  }
}

function onSocietaNomeInput(){
  const nome  = document.getElementById('f-societa-name').value.trim();
  const found = societaList.find(s => s.nome === nome);
  if (found){
    document.getElementById('f-societa').value = found.cod;
    updateUrlPreview();
  }
}

function onSocietaCodiceInput(){
  document.getElementById('f-societa-name').value = '';
  updateUrlPreview();
}

function getFormParams(){
  return {
    anno: document.getElementById('f-anno').value,
    tipo_attivita: document.getElementById('f-tipo').value,
    sesso: document.getElementById('f-sesso').value,
    categoria: document.getElementById('f-cat').value,
    regione: document.getElementById('f-reg').value,
    nazionalita: document.getElementById('f-naz').value,
    vento: document.getElementById('f-vento').value,
    limite: document.getElementById('f-limite').value,
    societa: document.getElementById('f-societa').value.toUpperCase().trim(),
  };
}

// ── FIDAL CONNECTION STATUS ──────────────────────────────
function _setFidalStatus(state, msg){
  const led = document.getElementById('fidal-led');
  const txt = document.getElementById('fidal-status-txt');
  const retry = document.getElementById('fidal-retry-btn');
  if (!led) return;
  led.className = 'fidal-led ' + state;
  txt.textContent = msg;
  const connected = state === 'ok';
  _fidalConnected = connected;
  retry.style.display = state === 'error' ? '' : 'none';
  ['btn-fetch-fidal','btn-fetch-proj','btn-build-proj'].forEach(id => {
    const btn = document.getElementById(id);
    if (btn) btn.disabled = !connected;
  });
}

async function checkFidalConnection(){
  try {
    _setFidalStatus('checking', 'Verifica connessione FIDAL…');
    const resp = await fetch('/api/fidal_status', {cache:'no-store'});
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    if (data.ok){
      _setFidalStatus('ok', `Connessione FIDAL attiva · ${data.latency_ms} ms`);
    } else {
      _setFidalStatus('error', `FIDAL non raggiungibile — ${(data.error||'?').slice(0,60)}`);
    }
  } catch(e){
    try { _setFidalStatus('error', `Errore — ${(e.message||'?').slice(0,60)}`); } catch(_){}
  }
  clearTimeout(_fidalCheckTimer);
  _fidalCheckTimer = setTimeout(checkFidalConnection, 60000);
}

// ── FETCH DATA ───────────────────────────────────────────
async function fetchData(){
  const errEl = document.getElementById('form-error');
  errEl.style.display='none';
  const p = getFormParams();
  if (!p.societa){ errEl.style.display='block'; errEl.textContent='Inserisci il codice società.'; return; }

  document.getElementById('loading').classList.remove('hidden');
  try {
    const resp = await fetch('/api/fetch?' + new URLSearchParams(p));
    const json = await resp.json();
    if (!json.ok) throw new Error(json.error);

    ALL = json.data;
    selectedIds.clear(); userPts = {}; staffAnalysis = []; excludedEvs = new Set();
    unavailableAthletes = new Set(); minDateFilter = null; isProiezione = false;
    document.getElementById('staff-panel').style.display='none';

    // Segna miglior prestazione per disciplina
    computeBests();

    // Risolvi nomi staffette
    ALL.filter(r=>r.isStaffetta).forEach(r=>{
      r.staffAthl = resolveStaffettaAthletes(r.rawStaff);
    });

    // Popola UI
    setupToolScreen(p);
    show('scr-tool');
    await _applyAutoOpts();
  } catch(e){
    errEl.style.display='block'; errEl.textContent='Errore: ' + e.message;
    // Aggiorna il LED: potrebbe essere un problema di connessione FIDAL
    _setFidalStatus('error', `Errore comunicazione FIDAL — ${e.message.slice(0,80)}`);
    document.getElementById('fidal-retry-btn').style.display='';
  } finally {
    document.getElementById('loading').classList.add('hidden');
  }
}

function _setLoadingMsg(msg){ const el=document.getElementById('loading-msg'); if(el) el.textContent=msg; }

// ── BUILD DB REGIONALE (SSE) ─────────────────────────────
let _buildES = null, _buildCompete = 0;

function startBuildProiezione(){
  const p = getFormParams();
  const area = document.getElementById('build-area');
  const fill = document.getElementById('build-fill');
  const status = document.getElementById('build-status');
  const log = document.getElementById('build-log');

  area.style.display = '';
  fill.style.width = '0%';
  status.textContent = 'Connessione…';
  log.innerHTML = '';

  if (_buildES) _buildES.close();

  const params = new URLSearchParams({
    anno: p.anno, tipo_attivita: p.tipo_attivita, sesso: p.sesso,
    categoria: p.categoria, regione: p.regione,
    nazionalita: p.nazionalita, vento: p.vento,
  });

  _buildES = new EventSource('/api/proiezione/build?' + params);

  _buildES.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch(_){ return; }

    if (msg.type === 'status'){
      status.textContent = msg.msg;
    }
    else if (msg.type === 'total'){
      status.textContent = `0/${msg.n} società analizzate · 0 con ${p.categoria}`;
      _buildCompete = 0;
    }
    else if (msg.type === 'found' || msg.type === 'skip' || msg.type === 'unchanged'){
      const pct = Math.round(msg.done / msg.total * 100);
      fill.style.width = pct + '%';
      if (msg.type === 'found' && msg.can_compete) _buildCompete++;
      const unch = msg.unchanged || 0;
      status.textContent = `${msg.done}/${msg.total} analizzate · ${msg.found} aggiornate · ${unch} invariate · 🏆 ${_buildCompete} competitive`;
      if (msg.type === 'found'){
        const badge = msg.can_compete ? '🏆' : '⚠';
        const hint  = msg.can_compete
          ? `${msg.n_ev} gare · ${msg.n_la} lanci · ${msg.n_sa} salti`
          : `solo ${msg.n_ev} gare (min 10), ${msg.n_la} lanci, ${msg.n_sa} salti`;
        const optStr = (msg.can_compete && msg.optimal_score > 0) ? ` · ottimale Σ ${msg.optimal_score}` : '';
        log.innerHTML += `<div>${badge} ${msg.soc} — ${msg.n_athl} atlet${p.sesso==='F'?'e':'i'} · ${hint} · Σ ${msg.total_pts}pt${optStr}</div>`;
        log.scrollTop = log.scrollHeight;
      } else if (msg.type === 'unchanged'){
        log.innerHTML += `<div style="color:var(--muted)">= ${msg.soc} — invariata (${msg.num_gare} gare · Σ ${msg.total_pts}pt)</div>`;
        log.scrollTop = log.scrollHeight;
      }
    }
    else if (msg.type === 'done'){
      _buildES.close(); _buildES = null;
      fill.style.width = '100%';
      const d = new Date(msg.updated_at);
      const fmt = d.toLocaleString('it-IT',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
      const unch = msg.unchanged_societies || 0;
      status.textContent = `✅ Completato — ${msg.found_societies} aggiornate · ${unch} invariate (🏆 ${_buildCompete} competitive) · ${msg.n_results} prestazioni · ${fmt}`;
      const logFile = msg.log_path ? `<br><span style="font-size:.8em;color:var(--muted)">📄 Log: ${msg.log_path}</span>` : '';
      log.innerHTML += `<div style="font-weight:600;color:var(--green)">Dati salvati in cache. Avvio proiezione...${logFile}</div>`;
      log.scrollTop = log.scrollHeight;
      setTimeout(() => {
        area.style.display = 'none';
        fetchProiezione(false);
      }, 2500);
    }
    else if (msg.type === 'error'){
      _buildES.close(); _buildES = null;
      status.textContent = `⚠ ${msg.msg}`;
      fill.style.width = '0%';
    }
  };

  _buildES.onerror = () => {
    if (_buildES){ _buildES.close(); _buildES = null; }
    status.textContent = '⚠ Connessione interrotta — riprova';
  };
}

function cancelBuild(){
  if (_buildES){ _buildES.close(); _buildES = null; }
  document.getElementById('build-area').style.display = 'none';
}

function _proiezioneParams(p, force){
  return new URLSearchParams({
    anno: p.anno, tipo_attivita: p.tipo_attivita, sesso: p.sesso,
    categoria: p.categoria, regione: p.regione,
    nazionalita: p.nazionalita, vento: p.vento,
    force: force ? '1' : '0',
  });
}

function _applyProiezioneData(json, p){
  ALL = json.data;
  _societiesMeta = json.societies_meta || {};
  _currentProiezioneP = p;
  selectedIds.clear(); userPts = {}; staffAnalysis = []; excludedEvs = new Set();
  unavailableAthletes = new Set(); minDateFilter = null; isProiezione = true;
  document.getElementById('staff-panel').style.display='none';
  computeBests();
  ALL.filter(r=>r.isStaffetta).forEach(r=>{ r.staffAthl = resolveStaffettaAthletes(r.rawStaff); });
  _pruneForProiezione(10); // top-10 per evento: include atleti fino al 10° posto regionale
}

function _setProiezioneBannerTs(json){
  if (!json.updated_at) return;
  const d = new Date(json.updated_at);
  const fmt = d.toLocaleString('it-IT',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
  document.getElementById('proiezione-ts').textContent =
    json.from_cache ? `📦 cache · ${fmt}` : `🌐 aggiornato · ${fmt}`;
}

async function fetchProiezione(forceRefresh=false){
  // Refresh da scr-classifica già attiva
  if (forceRefresh && document.getElementById('scr-classifica').classList.contains('active')){
    _bgRefreshProiezione(); return;
  }

  const errEl = document.getElementById('form-error');
  errEl.style.display='none';
  const p = getFormParams();

  _setLoadingMsg('Caricamento proiezione regionale…');
  document.getElementById('loading').classList.remove('hidden');
  try {
    const resp = await fetch('/api/proiezione?' + _proiezioneParams(p, forceRefresh));
    const json = await resp.json();
    if (!json.ok) throw new Error(json.error);

    _setLoadingMsg('Elaborazione classifica…');
    _applyProiezioneData(json, p);

    // Intestazione schermata classifica
    const catLbl = `CdS ${p.categoria} · ${p.tipo_attivita==='P'?'Outdoor':'Indoor'} ${p.anno} · ${p.regione}`;
    document.getElementById('clas-screen-title').textContent = `Proiezione Regionale — ${p.regione}`;
    document.getElementById('clas-screen-sub').textContent = catLbl;
    if (json.updated_at){
      const d=new Date(json.updated_at);
      const fmt=d.toLocaleString('it-IT',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
      document.getElementById('clas-screen-ts').textContent = json.from_cache ? `📦 ${fmt}` : `🌐 ${fmt}`;
    }

    show('scr-classifica');
    await computeClassifica();
  } catch(e){
    errEl.style.display='block'; errEl.textContent='Errore: ' + e.message;
  } finally {
    document.getElementById('loading').classList.add('hidden');
    _setLoadingMsg('Caricamento dati FIDAL…');
  }
}

async function _bgRefreshProiezione(){
  const ts  = document.getElementById('clas-screen-ts');
  const p   = getFormParams();
  if (ts) ts.textContent = '<span class="bspin">⟳</span> Aggiornamento…';

  try {
    const resp = await fetch('/api/proiezione?' + _proiezioneParams(p, true));
    const json = await resp.json();
    if (!json.ok) throw new Error(json.error);
    _applyProiezioneData(json, p);
    if (json.updated_at){
      const d=new Date(json.updated_at);
      const fmt=d.toLocaleString('it-IT',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
      if (ts) ts.textContent = `🌐 ${fmt}`;
    }
    await computeClassifica();
  } catch(e){
    if (ts) ts.textContent = `⚠ ${e.message}`;
  }
}

function setupToolScreen(p){
  currentCategoria = p.categoria;
  currentSocieta   = p.societa || '';
  currentAnno = +p.anno || new Date().getFullYear();
  document.getElementById('manual-reload-bar').style.display='none';
  savedManualEntries=[];
  checkSavedManualEntries(p.categoria, p.societa || '');

  // Reset filtri globali
  document.getElementById('date-filter-input').value='';
  document.getElementById('date-filter-clear').style.display='none';
  document.getElementById('date-filter-count').textContent='';
  renderUnavailPanel();

  const cat = document.getElementById('f-cat');
  const catLabel = cat.options[cat.selectedIndex].text;
  const sesso = p.sesso==='F'?'Femminile':'Maschile';
  const proBar = document.getElementById('proiezione-bar');
  const btnClas = document.getElementById('btn-to-classifica');
  if (isProiezione){
    proBar.style.display='';
    if (btnClas) btnClas.style.display='none';
    document.getElementById('tool-title').textContent = `Proiezione Regionale — ${p.regione}`;
  } else {
    proBar.style.display='none';
    // Mostra "Classifica" solo se c'è una proiezione disponibile in memoria
    if (btnClas) btnClas.style.display = _currentProiezioneP ? '' : 'none';
    document.getElementById('tool-title').textContent = 'Graduatorie — ' + (p.societa_nome || ('Soc. ' + p.societa));
  }
  document.getElementById('tool-sub').textContent =
    `CdS ${catLabel} · ${p.tipo_attivita==='P'?'Outdoor':'Indoor'} ${p.anno} · ${p.regione}`;
  document.getElementById('tag-cat').textContent = catLabel;

  // Popola filtro discipline
  const evSel = document.getElementById('f-ev-filter');
  evSel.innerHTML = '<option value="">Tutte le discipline</option>';
  [...new Set(ALL.map(r=>r.ev))].forEach(e=>{
    const o=document.createElement('option'); o.value=e; o.textContent=e; evSel.appendChild(o);
  });

  // Staffetta panel
  const staffette = ALL.filter(r=>r.isStaffetta);
  document.getElementById('staff-panel').style.display = staffette.length ? '' : 'none';

  // Sincronizza i checkbox delle opzioni automatiche
  const cbManual = document.getElementById('opt-auto-manual');
  const cbPreset = document.getElementById('opt-auto-preset');
  if (cbManual) cbManual.checked = autoLoadManual;
  if (cbPreset) cbPreset.checked = autoPresetCds;

  buildEvFilterPanel();
  updateConstraints(); renderAll(); renderAthleteTracker();
}

function setAutoOpt(which, val){
  if (which === 'manual'){ autoLoadManual = val; localStorage.setItem('cds_autoLoadManual', val); }
  if (which === 'preset') { autoPresetCds  = val; localStorage.setItem('cds_autoPresetCds',  val); }
}

async function _applyAutoOpts(){
  // Applica preset CdS se abilitato (sincrono, prima di caricare i manuali)
  if (autoPresetCds && CDS_PROGRAMS[currentCategoria]) applyPresetCds();
  // Carica manuali in silenzio se abilitato
  if (autoLoadManual) await _ensureManualEntries();
}

async function _triggerSocReoptimize(soc_cod, categoria){
  // Ricalcola l'ottimale nel JSON della proiezione cached dopo un cambio di manuali.
  // Operazione in background: non blocca la UI, mostra solo una notifica breve.
  if (!soc_cod || !categoria || !_currentProiezioneP) return;
  const noteEl = document.getElementById('note-est');
  const prev = noteEl ? noteEl.textContent : '';
  if (noteEl) setNoteEst('⟳ Aggiornamento ottimale nella proiezione regionale…');
  try {
    const resp = await fetch('/api/reoptimize_soc', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        soc_cod,
        categoria,
        anno:          _currentProiezioneP.anno,
        tipo_attivita: _currentProiezioneP.tipo_attivita,
        sesso:         _currentProiezioneP.sesso,
        regione:       _currentProiezioneP.regione,
      }),
    });
    const data = await resp.json();
    if (data.ok && _societiesMeta[soc_cod]) {
      // Aggiorna la meta in memoria così la classifica riflette il nuovo punteggio
      if (!_societiesMeta[soc_cod].optimal) _societiesMeta[soc_cod].optimal = {};
      _societiesMeta[soc_cod].optimal.score = data.score;
      _societiesMeta[soc_cod].manual_count  = data.manual_count;
    }
    if (noteEl) setNoteEst(data.ok
      ? `✓ Proiezione aggiornata — nuovo ottimale: ${(data.score||0).toLocaleString('it')} pt`
      : `⚠ Aggiornamento proiezione non riuscito: ${data.error||'?'}`
    );
  } catch(e){
    if (noteEl) setNoteEst('⚠ Aggiornamento proiezione non riuscito');
  }
  // Ripristina il messaggio precedente dopo 4s
  setTimeout(()=>{ if (noteEl) setNoteEst(''); }, 4000);
}

// ── MIGLIORI PRESTAZIONI ─────────────────────────────────
function parsePerf(perf){
  perf = perf.trim();
  if (perf.includes(':')){
    const p = perf.split(':').map(Number);
    return p.length===2 ? p[0]*60+p[1] : p[0]*3600+p[1]*60+p[2];
  }
  return parseFloat(perf.replace(',','.'));
}

function computeBests(){
  ALL.forEach(r=>r.isBest=false);
  const byEvent = {};
  activeAll().forEach(r=>{ if(!byEvent[r.ev]) byEvent[r.ev]=[]; byEvent[r.ev].push(r); });
  for (const ers of Object.values(byEvent)){
    if (ers[0].isStaffetta){ ers.forEach(r=>r.isBest=true); continue; }
    const isTime = !isLancio(ers[0].ev) && !isSalto(ers[0].ev);
    const parsed = ers.map(r=>({r, p:parsePerf(r.perf)})).filter(x=>!isNaN(x.p)&&x.p>0);
    if (!parsed.length) continue;
    const bestVal = isTime ? Math.min(...parsed.map(x=>x.p)) : Math.max(...parsed.map(x=>x.p));
    parsed.filter(x=>x.p===bestVal).forEach(x=>x.r.isBest=true);
  }
}

// ── NAME RESOLUTION (staffette) ─────────────────────────
function resolveStaffettaAthletes(rawStr){
  // FIDAL usa sia "COGNOME N. / COGNOME N." che "cognome-cognome-cognome"
  const byCS = rawStr.split(/[,\/]/).map(s=>s.trim()).filter(Boolean);
  const parts = byCS.length > 1 ? byCS : rawStr.split('-').map(s=>s.trim()).filter(Boolean);
  const indiv = activeAll().filter(r=>!r.isStaffetta);
  return parts.map(part=>{
    const cleaned = part.replace(/\s+[A-Z]{2}\s*$/, '').trim();
    // Pattern "COGNOME I." o "COGNOME I"
    const m = cleaned.match(/^([A-Z][A-Z'\-]+(?:\s+[A-Z][A-Z'\-]+)*)\s+([A-Z])\.?$/i);
    if (m) {
      const [,sur,ini] = m;
      const found = indiv.find(r=>{
        const w=r.athlete.split(/\s+/);
        return w[0].toUpperCase()===sur.toUpperCase() && w.length>1 && w[1][0].toUpperCase()===ini.toUpperCase();
      });
      if (found) return found.athlete;
      return `${sur.toUpperCase()} ${ini.toUpperCase()}.`;
    }
    // Fallback: solo cognome (formato FIDAL dash)
    const surOnly = cleaned.toUpperCase().trim();
    if (!/\s/.test(surOnly) && surOnly.length > 1) {
      const found = indiv.find(r => r.athlete.split(/\s+/)[0].toUpperCase() === surOnly);
      if (found) return found.athlete;
    }
    return cleaned || null;
  }).filter(Boolean);
}

// ── VALIDATION ───────────────────────────────────────────
function validate(){
  const C = getC();
  const sel = ALL.filter(r=>selectedIds.has(r.id));
  const evCount={}, atlCount={}, atlIndCount={}, lancioSet=new Set(), saltoSet=new Set();
  sel.forEach(r=>{
    if (!r.isStaffetta) evCount[r.ev]=(evCount[r.ev]||0)+1;
    const athls = r.isStaffetta ? r.staffAthl : [r.athlete];
    athls.forEach(a=>atlCount[a]=(atlCount[a]||0)+1);
    if (!r.isStaffetta) athls.forEach(a=>atlIndCount[a]=(atlIndCount[a]||0)+1);
    if (r.type==='lancio'||isLancio(r.ev)) lancioSet.add(r.ev);
    if (r.type==='salto' ||isSalto(r.ev))  saltoSet.add(r.ev);
  });
  const nEv = Object.keys(evCount).length + (sel.some(r=>r.isStaffetta)?1:0);
  return {
    nSel:sel.length, nEv, nLanci:lancioSet.size, nSalti:saltoSet.size,
    evOk: Object.values(evCount).every(v=>v<=2),
    atlOk: Object.values(atlCount).every(v=>v<=2) &&
           Object.values(atlIndCount).every(v=>v<=C.maxAthlInd),
    sel, evCount, atlCount, atlIndCount
  };
}

// ── STATUS PER RIGA (per colori nella tabella) ───────────
function rowStatus(r){
  const C = getC();
  const v = validate();
  const inSel = selectedIds.has(r.id);
  if (inSel) return 'sel';

  const athls = r.isStaffetta ? (r.staffAthl||[]) : [r.athlete];
  const atlBlocked = r.isStaffetta
    ? athls.some(a=>(v.atlCount[a]||0)>=2)
    : athls.some(a=>(v.atlIndCount[a]||0)>=C.maxAthlInd || (v.atlCount[a]||0)>=2);
  const evBlocked  = !r.isStaffetta && (v.evCount[r.ev]||0)>=2;
  const atLimit    = v.nSel >= C.nSel;

  if (atlBlocked || evBlocked || atLimit) return 'block';
  // warn se atleta è all'ultimo slot individuale disponibile
  const warnInd = !r.isStaffetta && athls.some(a=>(v.atlIndCount[a]||0)===C.maxAthlInd-1);
  const warnTot = athls.some(a=>(v.atlCount[a]||0)===1 && !warnInd);
  if (warnInd || warnTot) return 'warn';
  return 'free';
}

function statusIcon(s){
  return {sel:'✅',free:'➕',warn:'🟡',block:'🔴'}[s]||'';
}
function statusTitle(s,r){
  const C = getC();
  if (s==='sel') return 'Clicca per rimuovere';
  if (s==='block'){
    const v=validate();
    const athls=r.isStaffetta?(r.staffAthl||[]):[r.athlete];
    if (!r.isStaffetta && athls.some(a=>(v.atlIndCount[a]||0)>=C.maxAthlInd))
      return `🔴 Atleta già in ${C.maxAthlInd} gara${C.maxAthlInd>1?'':'individuale'}`;
    if (athls.some(a=>(v.atlCount[a]||0)>=2)) return '🔴 Atleta già selezionata 2 volte';
    if (!r.isStaffetta&&(v.evCount[r.ev]||0)>=2) return '🔴 Gara già con 2 risultati';
    if (v.nSel>=C.nSel) return `🔴 Già ${C.nSel} risultati selezionati`;
    return '🔴 Non aggiungibile';
  }
  if (s==='warn'){
    const v=validate();
    const athls=r.isStaffetta?(r.staffAthl||[]):[r.athlete];
    const used=athls.find(a=>(v.atlCount[a]||0)===1);
    return `🟡 ${used}: ultima occorrenza disponibile`;
  }
  return 'Clicca per aggiungere';
}

// ── TOGGLE SELECT ────────────────────────────────────────
function toggleSelect(id){
  const r = ALL.find(x=>x.id===id);
  if (!r) return;

  if (selectedIds.has(id)){
    selectedIds.delete(id);
  } else {
    const C = getC();
    const v = validate();
    const athls = r.isStaffetta ? (r.staffAthl||[]) : [r.athlete];
    if (v.nSel>=C.nSel){ alert(`Hai già ${C.nSel} risultati. Rimuovine uno prima.`); return; }
    if (!r.isStaffetta && athls.some(a=>(v.atlIndCount[a]||0)>=C.maxAthlInd)){
      alert(`⚠ Atleta già in ${C.maxAthlInd} gara${C.maxAthlInd>1?' individuale':''}!`); return;
    }
    if (athls.some(a=>(v.atlCount[a]||0)>=2)){ alert('⚠ Atleta già presente 2 volte!'); return; }
    if (!r.isStaffetta && (v.evCount[r.ev]||0)>=2){ alert('⚠ Gara già con 2 risultati!'); return; }
    selectedIds.add(id);
  }
  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
}

function clearAll(){ selectedIds.clear(); staffAnalysis=[]; topCombinations=[];
  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
  document.getElementById('staff-cards').innerHTML='';
  document.getElementById('staff-panel').style.display='none'; }

function goBack(){ show('scr-form'); }

async function goToClassifica(){
  if (!_currentProiezioneP) return;
  // Ricarica proiezione dalla cache (veloce, no fetch FIDAL) e mostra la classifica
  document.getElementById('loading').classList.remove('hidden');
  _setLoadingMsg('Caricamento proiezione…');
  try {
    const resp = await fetch('/api/proiezione?' + _proiezioneParams(_currentProiezioneP, false));
    const json = await resp.json();
    if (!json.ok) throw new Error(json.error||'Errore proiezione');
    _applyProiezioneData(json, _currentProiezioneP);
    setupToolScreen(_currentProiezioneP);
    show('scr-tool');
    buildEvFilterPanel();
    renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
    // Apre direttamente la classifica
    computeClassifica();
    setTimeout(()=>{
      const el = document.getElementById('clas-panel');
      if (el) el.scrollIntoView({behavior:'smooth', block:'start'});
    }, 150);
  } catch(e){
    alert('Errore: ' + e.message);
  } finally {
    document.getElementById('loading').classList.add('hidden');
  }
}
function show(id){ document.querySelectorAll('.screen').forEach(s=>s.classList.remove('active'));
  document.getElementById(id).classList.add('active'); }

// ── RENDER CONSTRAINTS ────────────────────────────────────
function updateConstraints(){
  const C=getC();
  const v=validate();
  const nDbl=Math.max(0,C.nSel-v.nEv);
  document.getElementById('c-n-t').textContent=`${v.nSel}/${C.nSel} risultati`;
  document.getElementById('c-n').className='cbox '+(v.nSel===C.nSel?'ok':v.nSel>0?'warn':'err');
  document.getElementById('c-ev-t').textContent=`${v.nEv}/${C.minEv} gare (${nDbl} doppiate)`;
  document.getElementById('c-ev').className='cbox '+(v.nEv>=C.minEv?'ok':v.nEv>=Math.ceil(C.minEv*.7)?'warn':'err');
  document.getElementById('c-la-t').textContent=`${v.nLanci}/${C.minLanci} lanci`;
  document.getElementById('c-la').className='cbox '+(v.nLanci>=C.minLanci?'ok':v.nLanci>0?'warn':'err');
  document.getElementById('c-sa-t').textContent=`${v.nSalti}/${C.minSalti} salti`;
  document.getElementById('c-sa').className='cbox '+(v.nSalti>=C.minSalti?'ok':v.nSalti>0?'warn':'err');
  document.getElementById('c-at-t').textContent=v.evOk&&v.atlOk?'Vincoli OK':'⚠ Violazione!';
  document.getElementById('c-at').className='cbox '+(v.evOk&&v.atlOk?'ok':'err');

  const total=v.sel.reduce((s,r)=>s+pts(r),0);
  document.getElementById('tot-pts').textContent=total.toLocaleString('it');
  document.getElementById('tot-n').textContent=v.nSel;
  document.getElementById('tot-ev').textContent=v.nEv;
  document.getElementById('tag-tot').textContent=`Tot. ${total.toLocaleString('it')} pt`;
  document.getElementById('grand-total').textContent=total.toLocaleString('it');
  document.getElementById('sel-n').textContent=v.nSel;

  // Auto-clear stale optimizer error when selection is fully valid
  const allOk = v.nSel===C.nSel && v.nEv>=C.minEv && v.nLanci>=C.minLanci && v.nSalti>=C.minSalti && v.evOk && v.atlOk;
  if (allOk) setNoteEst('');
}

// ── RENDER ATHLETE TRACKER ────────────────────────────────
function renderAthleteTracker(){
  const v=validate();
  const tracker=document.getElementById('atl-tracker');
  if (!Object.keys(v.atlCount).length){ tracker.innerHTML=''; return; }
  const C=getC();
  tracker.innerHTML = Object.entries(v.atlCount).sort((a,b)=>b[1]-a[1]).map(([a,c])=>{
    const indC=v.atlIndCount[a]||0;
    const full=c>=2||indC>=C.maxAthlInd;
    const cls = full?'full':c===1?'half':'free';
    // Mostra il conteggio totale (che determina il colore) + dettaglio individuale
    const label = c>=2 ? `${c}/2` : `${c}/2 · ${indC} ind`;
    return `<span class="atl-chip ${cls}">${a} <span class="atl-cnt">${label}</span></span>`;
  }).join('');
}

// ── RENDER PROSPETTO ──────────────────────────────────────
function renderProspetto(){
  const C=getC();
  const sel=ALL.filter(r=>selectedIds.has(r.id)).sort((a,b)=>pts(b)-pts(a));
  const ec={};
  sel.forEach(r=>ec[r.ev]=(ec[r.ev]||0)+1);

  // Aggiorna titolo dinamico
  document.getElementById('pros-title').textContent=`Prospetto Scheda — ${C.nSel} risultati`;

  // Calcola pareggi: risultati NON selezionati con stesso punteggio nella stessa gara
  const tieMap={};
  sel.filter(r=>!r.isStaffetta).forEach(r=>{
    const p=pts(r);
    if (!p) return;
    const alts=activeAll().filter(x=>x.ev===r.ev&&!x.isStaffetta&&!selectedIds.has(x.id)&&pts(x)===p);
    if (alts.length) tieMap[r.id]=alts;
  });
  const tieIds=Object.keys(tieMap);
  const tiePanel=document.getElementById('tie-panel');
  if (tieIds.length){
    tiePanel.style.display='';
    tiePanel.innerHTML=`<div class="tie-panel-title">⚠ ${tieIds.length} risultat${tieIds.length===1?'o':'i'} con alternative a pari punteggio (scelti arbitrariamente) — clicca un'alternativa per sostituire:</div>`+
      tieIds.map(id=>{
        const r=ALL.find(x=>x.id===+id);
        const alts=tieMap[id];
        const altChips=alts.map(a=>`<span class="tie-chip" title="Clicca per sostituire" onclick="swapResult(${r.id},${a.id})">${a.athlete} <span style="color:var(--muted)">${a.perf}</span></span>`).join('');
        return `<div class="tie-row"><span class="tie-sel">📌 <strong>${r.ev}</strong> — ${r.athlete} (${r.perf})</span><span style="color:var(--muted);margin:0 .3rem">→ anche:</span>${altChips}</div>`;
      }).join('');
  } else {
    tiePanel.style.display='none';
  }

  const tbody=document.getElementById('pros-body');
  if (!sel.length){
    tbody.innerHTML='<tr><td colspan="11" style="padding:2rem;text-align:center;color:var(--muted)">Nessun risultato selezionato.</td></tr>';
    updateConstraints();
    return;
  }
  tbody.innerHTML=sel.map((r,i)=>{
    const p=pts(r);
    const pVal=userPts[r.id]!==undefined?userPts[r.id]:(r.pts_ok?r.pts:'');
    const dbl=ec[r.ev]===2?'<span class="dbl-badge">×2</span>':'';
    const best=r.isBest?'<span class="best-mark" title="Miglior prestazione nella disciplina">*</span>':'';
    const tieBadge=tieMap[r.id]?`<span class="tie-badge" title="${tieMap[r.id].length} alternativa/e con uguale punteggio">≡${tieMap[r.id].length}</span>`:'';
    return `<tr class="selected-row">
      <td style="color:var(--muted);font-family:var(--mono);font-size:.72rem">${i+1}</td>
      <td><span class="etype ${r.type}">${TYPE_LBL[r.type]}</span></td>
      <td style="font-weight:600;white-space:nowrap">${r.ev}${dbl}</td>
      <td style="font-size:.78rem">${athleteDisplay(r)}${tieBadge}</td>
      <td style="font-size:.75rem;color:var(--muted);font-family:var(--mono)">${r.anno||''}</td>
      <td class="perf">${best}${r.perf}${r.wind?` <span style="font-size:.68rem;color:var(--muted)">${r.wind}</span>`:''}</td>
      <td style="font-size:.75rem;color:var(--muted)">${r.piazz||''}</td>
      <td style="font-size:.75rem;color:var(--muted);white-space:nowrap">${r.citta||''}</td>
      <td style="font-size:.75rem;color:var(--muted);white-space:nowrap;font-family:var(--mono)">${r.data||''}</td>
      <td style="text-align:right">
        <input class="pts-inp" type="number" min="0" value="${pVal}" placeholder="—"
          title="Inserisci punti FIDAL"
          onchange="userPts[${r.id}]=this.value!==''?+this.value:undefined;updateConstraints();renderProspetto();"
          onclick="event.stopPropagation()">
      </td>
      <td><button class="del-btn" title="Rimuovi" onclick="toggleSelect(${r.id})">✕</button></td>
    </tr>`;
  }).join('');
  updateConstraints();
}

function swapResult(oldId, newId){
  selectedIds.delete(oldId);
  selectedIds.add(newId);
  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
}

// ── RENDER ALL RESULTS ────────────────────────────────────
let sortedAll = [];
function sortAll(col){
  if (sortCol===col) sortAsc=!sortAsc; else {sortCol=col;sortAsc=true;}
  renderAll();
}

function renderAll(){
  const typeF=document.getElementById('f-type').value;
  const nameF=document.getElementById('f-name').value.toLowerCase();
  const evF=document.getElementById('f-ev-filter').value;

  let filtered=activeAll().filter(r=>
    (!typeF||r.type===typeF)&&
    (!nameF||r.athlete.toLowerCase().includes(nameF))&&
    (!evF||r.ev===evF)
  );

  if (sortCol>=0){
    const keys=[r=>r.type,r=>r.ev,r=>r.athlete,r=>r.perf,r=>r.piazz,r=>pts(r)];
    const fn=keys[sortCol]||((r)=>r.id);
    filtered.sort((a,b)=>{
      const va=fn(a),vb=fn(b);
      const n=parseFloat(va),m=parseFloat(vb);
      if (!isNaN(n)&&!isNaN(m)) return sortAsc?n-m:m-n;
      return sortAsc?String(va).localeCompare(String(vb),'it'):String(vb).localeCompare(String(va),'it');
    });
  }

  document.getElementById('all-n').textContent=filtered.length;
  const tbody=document.getElementById('all-body');
  tbody.innerHTML=filtered.map(r=>{
    const st=rowStatus(r);
    const inSel=selectedIds.has(r.id);
    const pVal=userPts[r.id]!==undefined?userPts[r.id]:(r.pts_ok?r.pts:'');
    const rowCls=`avail-row row-${st}`;
    const canClick=st!=='block';
    const btnTxt=inSel?'✓ Incluso':'+ Aggiungi';
    const btnCls='add-btn'+(inSel?' sel':'');
    const best=r.isBest?'<span class="best-mark" title="Miglior prestazione nella disciplina">*</span>':'';
    const manualBadge=r.isManual?'<span class="manual-badge">manuale</span>':'';
    const delManual=r.isManual?`<button class="del-btn" title="Rimuovi risultato manuale"
      onclick="event.stopPropagation();removeManual(${r.id})" style="margin-left:.25rem">🗑</button>`:'';
    return `<tr class="${rowCls}" ${canClick?`onclick="toggleSelect(${r.id})" title="${statusTitle(st,r)}"`:''}>
      <td style="text-align:center">${statusIcon(st)}</td>
      <td><span class="etype ${r.type}">${TYPE_LBL[r.type]}</span></td>
      <td style="font-weight:600;white-space:nowrap;font-size:.8rem">${r.ev}${manualBadge}</td>
      <td style="font-size:.78rem" onclick="event.stopPropagation()">${athleteDisplay(r)}</td>
      <td style="color:var(--muted);font-size:.72rem;font-family:var(--mono)">${r.anno||''}</td>
      <td class="perf">${best}${r.perf}</td>
      <td style="color:var(--muted);font-size:.72rem">${r.wind||'—'}</td>
      <td style="color:var(--muted);font-size:.72rem">${r.piazz||''}</td>
      <td style="color:var(--muted);font-size:.72rem;white-space:nowrap">${r.citta||''}</td>
      <td style="color:var(--muted);font-size:.72rem;white-space:nowrap;font-family:var(--mono)">${r.data||''}</td>
      <td style="text-align:right" onclick="event.stopPropagation()">
        <input class="pts-inp" type="number" min="0" value="${pVal}" placeholder="—"
          title="Inserisci punti FIDAL"
          onchange="userPts[${r.id}]=this.value!==''?+this.value:undefined;updateConstraints();renderProspetto();">
      </td>
      <td onclick="event.stopPropagation()">
        <button class="${btnCls}" ${st==='block'?'disabled':''} title="${statusTitle(st,r)}"
          onclick="toggleSelect(${r.id})">${btnTxt}</button>${delManual}
      </td>
    </tr>`;
  }).join('');
}

// ── INSERIMENTO MANUALE ───────────────────────────────────
let manualIdCounter = 100000;

function detectType(evName){
  const n=evName.toLowerCase();
  if (/staffetta|[34]x\d+|\dx\d/.test(n)) return 'staffetta';
  if (/\bhs\b|ostacoli|siepi/.test(n))     return 'ostacoli';
  if (/lungo|triplo|alto|asta|salto/.test(n)) return 'salto';
  if (/peso|martello|giavellotto|disco|lancio/.test(n)) return 'lancio';
  return 'corsa';
}

function onManualEvInput(){
  const ev=document.getElementById('m-ev').value;
  document.getElementById('m-tipo').value=detectType(ev);
}

function toggleManualForm(){
  const f=document.getElementById('manual-form');
  const visible=f.style.display!=='none';
  f.style.display=visible?'none':'';
  if (!visible){
    // Popola datalist con le gare esistenti
    const dl=document.getElementById('m-ev-list');
    dl.innerHTML=[...new Set(ALL.map(r=>r.ev))].sort((a,b)=>a.localeCompare(b,'it'))
      .map(e=>`<option value="${e}">`).join('');
    document.getElementById('m-ev').focus();
    document.getElementById('manual-err').textContent='';
  }
}

function submitManual(){
  const ev=(document.getElementById('m-ev').value||'').trim();
  const tipo=document.getElementById('m-tipo').value;
  const perf=(document.getElementById('m-perf').value||'').trim();
  const athlRaw=(document.getElementById('m-athl').value||'').trim();
  const ptsVal=document.getElementById('m-pts').value;
  const errEl=document.getElementById('manual-err');
  errEl.textContent='';

  if (!ev)     { errEl.textContent='⚠ Inserisci il nome della gara.'; return; }
  if (!perf)   { errEl.textContent='⚠ Inserisci la prestazione.'; return; }
  if (!athlRaw){ errEl.textContent='⚠ Inserisci almeno un/una atleta.'; return; }
  const _cdsCheck = CDS_PROGRAMS[currentCategoria];
  if (_cdsCheck && !_cdsCheck(ev)){
    errEl.style.color='var(--warn,#e67e00)';
    errEl.textContent=`⚠ "${ev}" non è nel programma CdS ${currentCategoria} — verrà salvato ma escluso dall'ottimizzatore.`;
  }

  const isStaff=(tipo==='staffetta');
  const staffAthl=isStaff ? athlRaw.split(/[,;\/]/).map(s=>s.trim()).filter(Boolean) : null;
  const pts_num=ptsVal!==''?+ptsVal:0;
  const pts_ok=ptsVal!=='';

  const citta=(document.getElementById('m-citta').value||'').trim();
  const data=(document.getElementById('m-data').value||'').trim();

  const r={
    id: manualIdCounter++,
    ev, type: tipo,
    athlete: isStaff ? (staffAthl.join(' / ')) : athlRaw,
    athlete_url:'', perf, wind:'', piazz:'', citta, data, anno:'',
    pts: pts_num, pts_ok,
    isStaffetta: isStaff,
    rawStaff: isStaff ? athlRaw : '',
    staffAthl: isStaff ? staffAthl : undefined,
    isManual: true,
  };

  ALL.push(r);

  // Persisti sul server
  const _mp = getFormParams();
  const savePayload={
    categoria:currentCategoria, ev:r.ev, type:r.type, athlete:r.athlete,
    perf:r.perf, wind:r.wind, piazz:r.piazz, citta:r.citta, data:r.data,
    anno:r.anno, pts:r.pts, pts_ok:r.pts_ok,
    isStaffetta:r.isStaffetta, rawStaff:r.rawStaff, staffAthl:r.staffAthl,
    isManual:true,
    soc_cod: _mp.societa || '',
    soc_nome: document.getElementById('f-societa-name')?.value?.trim() || '',
  };
  fetch('/api/manual',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(savePayload)})
    .then(res=>res.json()).then(json=>{
      if(json.ok){
        r.savedId=json.savedId;
        _triggerSocReoptimize(savePayload.soc_cod, savePayload.categoria);
      }
    })
    .catch(()=>{});

  computeBests();
  buildEvFilterPanel();

  // Aggiorna filtro discipline
  const evSel=document.getElementById('f-ev-filter');
  if (![...evSel.options].some(o=>o.value===ev)){
    const o=document.createElement('option'); o.value=ev; o.textContent=ev; evSel.appendChild(o);
  }

  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();

  // Reset form (mantieni aperto per aggiungerne altri)
  document.getElementById('m-ev').value='';
  document.getElementById('m-perf').value='';
  document.getElementById('m-athl').value='';
  document.getElementById('m-pts').value='';
  document.getElementById('m-citta').value='';
  document.getElementById('m-data').value='';
  document.getElementById('m-ev').focus();
}

function removeManual(id){
  const idx=ALL.findIndex(r=>r.id===id&&r.isManual);
  if (idx<0) return;
  const r=ALL[idx];
  if (r.savedId){
    fetch(`/api/manual/${r.savedId}`,{method:'DELETE'})
      .then(()=>_triggerSocReoptimize(currentSocieta, currentCategoria))
      .catch(()=>{});
  }
  ALL.splice(idx,1);
  selectedIds.delete(id);
  computeBests();
  buildEvFilterPanel();
  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
}

// ── CSV IMPORT ───────────────────────────────────────────────────────────────
let _csvFile = null;
let _csvOpenedFromForm = false;  // true = aperto dal form screen (modalità manuale)

function openCsvModal(fromForm){
  _csvOpenedFromForm = !!fromForm;
  // Mostra/nasconde la nota contestuale
  document.getElementById('csv-form-note').style.display = fromForm ? '' : 'none';
  document.getElementById('csv-overlay').style.display='flex';
  document.getElementById('csv-result-box').style.display='none';
  document.getElementById('csv-result-box').innerHTML='';
  document.getElementById('csv-file-input').value='';
  document.getElementById('csv-file-chosen').style.display='none';
  document.getElementById('btn-csv-upload').disabled=true;
}
function closeCsvModal(){
  document.getElementById('csv-overlay').style.display='none';
  _csvFile=null;
  _csvOpenedFromForm=false;
  document.getElementById('csv-file-input').value='';
  document.getElementById('csv-file-chosen').style.display='none';
  document.getElementById('btn-csv-upload').disabled=true;
  document.getElementById('csv-result-box').style.display='none';
  document.getElementById('csv-result-box').innerHTML='';
  document.getElementById('csv-upload-spinner').style.display='none';
}

function csvFileChosen(file){
  if (!file) return;
  _csvFile=file;
  const label=document.getElementById('csv-file-chosen');
  label.textContent=`File selezionato: ${file.name} (${(file.size/1024).toFixed(1)} KB)`;
  label.style.display='block';
  document.getElementById('btn-csv-upload').disabled=false;
  document.getElementById('csv-result-box').style.display='none';
  document.getElementById('csv-result-box').innerHTML='';
}

function csvHandleDrop(e){
  e.preventDefault();
  document.getElementById('csv-drop-zone').classList.remove('drag-over');
  const file=e.dataTransfer?.files?.[0];
  if (file) csvFileChosen(file);
}

function csvDownloadTemplate(){
  const a=document.createElement('a');
  a.href='/api/manual/template_csv';
  a.download='template_importazione_cds.csv';
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
}

let _discListLoaded = false;
function toggleDisciplineList(){
  const panel = document.getElementById('csv-discipline-list');
  const toggle = document.getElementById('csv-disc-toggle');
  const open = panel.style.display === 'none';
  panel.style.display = open ? '' : 'none';
  toggle.textContent = open ? 'nascondi lista ▴' : 'mostra lista ▾';
  if (open && !_discListLoaded){
    _discListLoaded = true;
    fetch('/api/discipline_list').then(r=>r.json()).then(data=>{
      const order = ['CF','CM','RF','RM'];
      const labels = {CF:'Cadette (CF)', CM:'Cadetti (CM)', RF:'Ragazze (RF)', RM:'Ragazzi (RM)'};
      const content = document.getElementById('csv-disc-content');
      content.innerHTML = order.map(cat=>{
        const pills = (data[cat]||[]).map(g=>`<span class="csv-disc-pill">${g}</span>`).join('');
        return `<div><div class="csv-disc-cat">${labels[cat]}</div><div>${pills}</div></div>`;
      }).join('');
    }).catch(()=>{
      document.getElementById('csv-disc-content').innerHTML='<em style="color:var(--muted)">Errore caricamento lista.</em>';
    });
  }
}

function _csvEntryToRow(entry){
  return {
    id: manualIdCounter++,
    ev: entry.ev,
    type: entry.type,
    athlete: entry.athlete,
    athlete_url: '',
    perf: entry.perf,
    wind: entry.wind||'',
    piazz: entry.piazz||'',
    citta: entry.citta||'',
    data: entry.data||'',
    anno: '',
    pts: entry.pts||0,
    pts_ok: entry.pts_ok||false,
    isStaffetta: entry.isStaffetta||false,
    rawStaff: entry.rawStaff||'',
    staffAthl: entry.staffAthl||undefined,
    isManual: true,
    savedId: entry.savedId,
  };
}

async function csvUpload(){
  if (!_csvFile){ return; }
  const btn=document.getElementById('btn-csv-upload');
  const spin=document.getElementById('csv-upload-spinner');
  const box=document.getElementById('csv-result-box');
  btn.disabled=true; spin.style.display='inline'; box.style.display='none'; box.innerHTML='';

  const fd=new FormData();
  fd.append('file', _csvFile);

  let json;
  try {
    const resp=await fetch('/api/manual/import_csv',{method:'POST',body:fd});
    json=await resp.json();
  } catch(err) {
    spin.style.display='none'; btn.disabled=false;
    box.innerHTML=`<div class="csv-warn-bar">Errore di rete: ${err.message}</div>`;
    box.style.display='block';
    return;
  }
  spin.style.display='none';

  if (!json.ok && !json.imported){
    box.innerHTML=`<div class="csv-warn-bar"><strong>Errore:</strong> ${json.error||'Importazione fallita'}</div>`;
    box.style.display='block';
    btn.disabled=false;
    return;
  }

  const imported=json.imported||[];
  const errors=json.errors||[];

  // Feedback testuale
  let html='';
  if (imported.length>0){
    const n=imported.length;
    html+=`<div class="csv-ok-bar">${n} riga${n!==1?'he':''} importata${n!==1?'e':''} con successo.</div>`;
  }
  if (errors.length>0){
    const n=errors.length;
    html+=`<div class="csv-warn-bar" style="margin-top:.5rem"><strong>${n} riga${n!==1?'he':''} con errori (non importat${n!==1?'e':'a'}):</strong>
    <ul class="csv-err-list" style="margin-top:.4rem">`;
    errors.forEach(e=>{
      const ante=e.anteprima?` <em style="color:var(--muted)">(${e.anteprima})</em>`:'';
      html+=`<li><strong>Riga ${e.riga}${ante}:</strong> ${e.errori.join('; ')}</li>`;
    });
    html+=`</ul></div>`;
  }
  box.innerHTML=html; box.style.display='block';

  if (imported.length>0){
    if (_csvOpenedFromForm){
      // ── Flusso da form screen: inizializza tool screen con i soli dati CSV ──
      const p = getFormParams();
      p.societa_nome = document.getElementById('f-societa-name')?.value?.trim() || '';
      // La categoria può venire dal form oppure, se tutte le righe hanno la stessa, da lì
      const cats=[...new Set(imported.map(e=>e.categoria))];
      if (!p.categoria && cats.length===1) p.categoria=cats[0];

      // Reset stato tool
      ALL=[]; selectedIds.clear(); userPts={}; staffAnalysis=[]; excludedEvs=new Set();
      unavailableAthletes=new Set(); minDateFilter=null; isProiezione=false;

      // Popola ALL con i record importati (filtro per categoria del form)
      imported.forEach(entry=>{
        if (!p.categoria || entry.categoria===p.categoria) ALL.push(_csvEntryToRow(entry));
      });

      computeBests();
      ALL.filter(r=>r.isStaffetta).forEach(r=>{ r.staffAthl=r.staffAthl||[]; });
      setupToolScreen(p);
      show('scr-tool');
      await _applyAutoOpts();
      // Chiudi il modale dopo un breve ritardo per far vedere il feedback
      setTimeout(closeCsvModal, 1200);
    } else {
      // ── Flusso da tool screen: aggiunge ai risultati esistenti ──
      imported.forEach(entry=>{
        if (entry.categoria===currentCategoria) ALL.push(_csvEntryToRow(entry));
      });
      computeBests(); buildEvFilterPanel();
      renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
      const evSel=document.getElementById('f-ev-filter');
      imported.filter(e=>e.categoria===currentCategoria).forEach(e=>{
        if(![...evSel.options].some(o=>o.value===e.ev)){
          const o=document.createElement('option'); o.value=e.ev; o.textContent=e.ev; evSel.appendChild(o);
        }
      });
    }
  }
  btn.disabled=false;
}
// ── END CSV IMPORT ────────────────────────────────────────────────────────────

async function checkSavedManualEntries(categoria, soc_cod){
  try {
    const resp=await fetch(`/api/manual?categoria=${encodeURIComponent(categoria)}`);
    const json=await resp.json();
    if (!json.ok) return;
    // Mostra il banner solo per le entries di questa società.
    // Se l'entry non ha soc_cod (entries vecchie) la includiamo per sicurezza.
    const relevant = json.data.filter(e =>
      !e.soc_cod || !soc_cod || e.soc_cod === soc_cod
    );
    if (relevant.length > 0){
      savedManualEntries = relevant;
      // Mostra il banner solo se il caricamento automatico è disabilitato
      if (!autoLoadManual){
        document.getElementById('reload-count').textContent = relevant.length;
        document.getElementById('manual-reload-bar').style.display = '';
      }
    }
  } catch(e){}
}

function dismissReloadBar(){
  document.getElementById('manual-reload-bar').style.display='none';
  savedManualEntries=[];
}

function reloadManualEntries(){
  savedManualEntries.forEach(entry=>{
    // Evita duplicati (stesso savedId già caricato)
    if (entry.savedId && ALL.some(r=>r.savedId===entry.savedId)) return;
    const r={
      id: manualIdCounter++,
      ev: entry.ev, type: entry.type,
      athlete: entry.athlete, athlete_url:'',
      perf: entry.perf, wind: entry.wind||'', piazz: entry.piazz||'',
      citta: entry.citta||'', data: entry.data||'', anno: entry.anno||'',
      pts: entry.pts||0, pts_ok: entry.pts_ok||false,
      isStaffetta: entry.isStaffetta||false,
      rawStaff: entry.rawStaff||'',
      staffAthl: entry.staffAthl||undefined,
      isManual: true,
      savedId: entry.savedId,
    };
    if (r.isStaffetta && !r.staffAthl)
      r.staffAthl=resolveStaffettaAthletes(r.rawStaff);
    ALL.push(r);

    // Aggiorna filtro discipline
    const evSel=document.getElementById('f-ev-filter');
    if (![...evSel.options].some(o=>o.value===r.ev)){
      const o=document.createElement('option'); o.value=r.ev; o.textContent=r.ev; evSel.appendChild(o);
    }
  });
  computeBests();
  buildEvFilterPanel();
  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
  document.getElementById('manual-reload-bar').style.display='none';
  savedManualEntries=[];
}

// ── FILTRO GARE CDS ───────────────────────────────────────
function buildEvFilterPanel(){
  const panel=document.getElementById('ev-filter-panel');
  const chipsEl=document.getElementById('ev-chips');
  const evs=[...new Set(ALL.map(r=>r.ev))].sort((a,b)=>a.localeCompare(b,'it'));
  if (!evs.length){ panel.style.display='none'; return; }
  panel.style.display='';
  // Mostra bottone preset solo se la categoria corrente ha un programma definito
  const btnPreset=document.getElementById('btn-preset-cds');
  btnPreset.style.display=CDS_PROGRAMS[currentCategoria] ? '' : 'none';
  chipsEl.innerHTML='';
  evs.forEach(ev=>{
    const chip=document.createElement('span');
    chip.className='ev-chip'+(excludedEvs.has(ev)?' excl':'');
    chip.textContent=ev;
    chip.title=excludedEvs.has(ev)?'Clicca per includere nel CdS':'Clicca per escludere dal CdS';
    chip.addEventListener('click',()=>toggleEvFilter(ev));
    chipsEl.appendChild(chip);
  });
}

function applyPresetCds(){
  const fn=CDS_PROGRAMS[currentCategoria];
  if (!fn) return;
  const evs=[...new Set(ALL.map(r=>r.ev))];
  evs.forEach(ev=>{
    if (fn(ev)){
      excludedEvs.delete(ev);
    } else {
      excludedEvs.add(ev);
      ALL.filter(r=>r.ev===ev).forEach(r=>selectedIds.delete(r.id));
    }
  });
  computeBests();
  buildEvFilterPanel();
  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
}

function resetEvFilter(){
  excludedEvs.clear();
  computeBests();
  buildEvFilterPanel();
  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
}

function toggleEvFilter(ev){
  if (excludedEvs.has(ev)){
    excludedEvs.delete(ev);
  } else {
    excludedEvs.add(ev);
    ALL.filter(r=>r.ev===ev).forEach(r=>selectedIds.delete(r.id));
  }
  computeBests();
  buildEvFilterPanel();
  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
}

// ── ATLETI NON DISPONIBILI ────────────────────────────────
function renderUnavailPanel(){
  const allAthletes=[...new Set(ALL.filter(r=>!r.isStaffetta).map(r=>r.athlete))].sort((a,b)=>a.localeCompare(b,'it'));
  const dl=document.getElementById('unavail-list');
  dl.innerHTML=allAthletes.filter(a=>!unavailableAthletes.has(a)).map(a=>`<option value="${a}">`).join('');
  const chips=document.getElementById('unavail-chips');
  chips.innerHTML=[...unavailableAthletes].sort((a,b)=>a.localeCompare(b,'it')).map(a=>
    `<span class="unavail-chip">${a}<button onclick="removeUnavailable(${JSON.stringify(a)})" title="Ripristina">✕</button></span>`
  ).join('');
}

function addUnavailFromInput(){
  const input=document.getElementById('unavail-input');
  const name=(input.value||'').trim();
  if (!name) return;
  unavailableAthletes.add(name);
  // Rimuovi dalla selezione i risultati dell'atleta escluso
  ALL.filter(r=>{
    const athls=r.isStaffetta?(r.staffAthl||[r.athlete]):[r.athlete];
    return athls.some(a=>a===name);
  }).forEach(r=>selectedIds.delete(r.id));
  input.value='';
  _afterGlobalFilter();
}

function removeUnavailable(name){
  unavailableAthletes.delete(name);
  _afterGlobalFilter();
}

// ── FILTRO DATA PRESTAZIONE ───────────────────────────────
function parseResultDate(str){
  if (!str) return null;
  // Con anno: DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY
  let m=str.match(/(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})/);
  if (m) return new Date(+m[3], +m[2]-1, +m[1]);
  // Solo giorno/mese (formato FIDAL standard): DD/MM, DD-MM, DD.MM
  // → usa l'anno della ricerca corrente
  m=str.match(/(\d{1,2})[\/\-\.](\d{1,2})/);
  if (m) return new Date(currentAnno, +m[2]-1, +m[1]);
  return null;
}

function onDateFilterChange(){
  const val=document.getElementById('date-filter-input').value;
  if (val){
    // input type=date restituisce YYYY-MM-DD (UTC): parsifichiamo in ora locale
    const [y,mo,d]=val.split('-').map(Number);
    minDateFilter=new Date(y, mo-1, d);
  } else {
    minDateFilter=null;
  }
  document.getElementById('date-filter-clear').style.display=val?'':'none';
  if (minDateFilter){
    ALL.filter(r=>{
      if (!r.data) return false;
      const d=parseResultDate(r.data);
      return d && d < minDateFilter;
    }).forEach(r=>selectedIds.delete(r.id));
  }
  _afterGlobalFilter();
}

function clearDateFilter(){
  document.getElementById('date-filter-input').value='';
  minDateFilter=null;
  document.getElementById('date-filter-clear').style.display='none';
  _afterGlobalFilter();
}

function _updateDateCount(){
  const el=document.getElementById('date-filter-count');
  if (!minDateFilter){ el.textContent=''; return; }
  const n=ALL.filter(r=>{
    if (!r.data) return false;
    const d=parseResultDate(r.data);
    return d && d < minDateFilter;
  }).length;
  el.textContent=n>0?`(${n} risultat${n===1?'o':'i'} esclus${n===1?'o':'i'})`:'(nessun risultato escluso)';
}

function _afterGlobalFilter(){
  computeBests();
  buildEvFilterPanel();
  renderUnavailPanel();
  _updateDateCount();
  renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
}

// ── OTTIMIZZAZIONE ────────────────────────────────────────

function _pruneForProiezione(nPerEv){
  // Per le staffette: tieni solo il top 1 per disciplina (migliore della regione).
  // Per le gare individuali: tieni i top nPerEv (per gestire conflitti atleti).
  // NOTA: usa riferimento oggetto (non r.id) — in proiezione gli id si ripetono tra società.
  const _cdsProg = CDS_PROGRAMS[currentCategoria];
  if (_cdsProg) ALL = ALL.filter(r => _cdsProg(r.ev));
  const byEv = {};
  ALL.forEach(r => { (byEv[r.ev] = byEv[r.ev]||[]).push(r); });
  const keep = new Set(); // Set di oggetti, non di id
  for (const ers of Object.values(byEv)){
    ers.sort((a,b)=>(pts(b)||0)-(pts(a)||0));
    const n = ers[0]?.isStaffetta ? 1 : nPerEv;
    ers.slice(0, n).forEach(r=>keep.add(r));
  }
  ALL = ALL.filter(r=>keep.has(r));
}
function* _staffCombos(groups){
  // Prodotto cartesiano: per ogni tipo di staffetta scegli null (escludi) o una delle entry.
  if (!groups.length){ yield []; return; }
  const [first, ...rest] = groups;
  for (const tail of _staffCombos(rest)){
    yield [null, ...tail];
    for (const entry of first) yield [entry, ...tail];
  }
}
function* combIter(arr, k){
  const n=arr.length;
  if (k===0){yield[];return;} if (k>n) return;
  const idx=Array.from({length:k},(_,i)=>i);
  while(true){
    yield idx.map(i=>arr[i]);
    let i=k-1; while(i>=0&&idx[i]===i+n-k) i--;
    if (i<0) break; idx[i]++;
    for(let j=i+1;j<k;j++) idx[j]=idx[j-1]+1;
  }
}

function isValidSelCaps(sel, evCap, maxAthlInd=2){
  const evUsed={}, acTotal={}, acInd={};
  for (const r of sel){
    if (!r.isStaffetta){
      evUsed[r.ev]=(evUsed[r.ev]||0)+1;
      if (evUsed[r.ev]>(evCap[r.ev]||1)) return false;
    }
    const athls=r.isStaffetta?(r.staffAthl||[r.athlete]):[r.athlete];
    for (const a of athls){
      acTotal[a]=(acTotal[a]||0)+1;
      if (!r.isStaffetta) acInd[a]=(acInd[a]||0)+1;
      if (acTotal[a]>2) return false;
      if (acInd[a]>maxAthlInd) return false;
    }
  }
  return true;
}

// Cache pre-calcolata da searchOptimal: ev → [risultati ordinati, top-N]
let _evCands = null;

function assignBest(evSub, dblSet, inclStaff){
  const C=getC();
  const evCap={};
  for (const ev of evSub) evCap[ev]=dblSet.has(ev)?2:1;
  const staffEvs=new Set(inclStaff.map(r=>r.ev));
  const cands=[];
  for (const ev of evSub){
    if (staffEvs.has(ev)) continue;
    // Usa la cache pre-calcolata se disponibile, altrimenti fallback
    const src = (_evCands && _evCands[ev]) ||
                activeAll().filter(r=>r.ev===ev&&!r.isStaffetta);
    src.forEach(r=>cands.push(r));
  }
  // Tiebreaker stabile: stesso punteggio → ordine deterministico per atleta+prestazione
  cands.sort((a,b)=>pts(b)-pts(a)||a.athlete.localeCompare(b.athlete,'it')||a.perf.localeCompare(b.perf,'it'));

  // Greedy: prenota atleti staffetta, poi riempi in ordine di punteggio
  const sel=[],acTotal={},acInd={},evUsed={};
  inclStaff.filter(r=>evSub.includes(r.ev)).forEach(st=>{
    sel.push(st); evUsed[st.ev]=(evUsed[st.ev]||0)+1;
    (st.staffAthl||[]).forEach(a=>acTotal[a]=(acTotal[a]||0)+1);
  });
  for (const r of cands){
    const ev=r.ev;
    if ((evUsed[ev]||0)>=(evCap[ev]||1)) continue;
    if ((acTotal[r.athlete]||0)>=2) continue;
    if ((acInd[r.athlete]||0)>=C.maxAthlInd) continue;
    sel.push(r);
    acTotal[r.athlete]=(acTotal[r.athlete]||0)+1;
    acInd[r.athlete]=(acInd[r.athlete]||0)+1;
    evUsed[ev]=(evUsed[ev]||0)+1;
  }

  let swapped=true;
  while (swapped){
    swapped=false;
    const indivSel=sel.filter(s=>!s.isStaffetta).sort((a,b)=>pts(a)-pts(b)||a.athlete.localeCompare(b.athlete,'it'));
    for (const r2 of indivSel){
      const idx=sel.indexOf(r2);
      for (const r of cands){
        if (sel.some(s=>s.id===r.id)) continue;
        if (pts(r)<=pts(r2)) break;
        const candidate=[...sel]; candidate[idx]=r;
        // Blocca lo swap se svuoterebbe l'evento di r2 (singolo → 0 risultati)
        if (!candidate.some(x=>!x.isStaffetta&&x.ev===r2.ev)) continue;
        if (isValidSelCaps(candidate, evCap, C.maxAthlInd)){ sel[idx]=r; swapped=true; break; }
      }
      if (swapped) break;
    }
  }

  return {sel, total:sel.reduce((s,r)=>s+pts(r),0)};
}

function searchOptimal(inclStaff, maxDoubles){
  const C=getC();
  const maxD = maxDoubles !== undefined ? maxDoubles : C.nSel-C.minEv;

  // Pre-calcola candidati per evento una sola volta (evita N×activeAll() in assignBest)
  // In proiezione con molte società, limita a top-25 per evento: sufficiente per l'ottimale
  const _ownCache = (_evCands === null);
  if (_ownCache){
    const active = activeAll();
    const TOP = isProiezione ? 25 : Infinity;
    _evCands = {};
    for (const r of active){
      if (r.isStaffetta) continue;
      (_evCands[r.ev] = _evCands[r.ev] || []).push(r);
    }
    for (const ev of Object.keys(_evCands)){
      _evCands[ev].sort((a,b)=>pts(b)-pts(a)||a.athlete.localeCompare(b.athlete,'it'));
      if (TOP !== Infinity && _evCands[ev].length > TOP) _evCands[ev].length = TOP;
    }
  }

  const staffEvs=new Set(inclStaff.map(r=>r.ev));
  const active = activeAll();
  const evList=[...new Set(active.filter(r=>!r.isStaffetta||staffEvs.has(r.ev)).map(r=>r.ev))];
  const dbl=evList.filter(ev=>((_evCands&&_evCands[ev])||active.filter(r=>r.ev===ev)).length>=2);
  let best=-1,bestSel=null;
  let _assignBestCalls=0;
  const _t0=Date.now();
  for (let nEv=C.minEv;nEv<=Math.min(C.nSel,evList.length);nEv++){
    const nD=C.nSel-nEv;
    if (nD > maxD) continue; // salta se richiede troppe doppiature
    for (const evSub of combIter(evList,nEv)){
      let nl=0,ns=0;
      for (const ev of evSub){if(isLancio(ev))nl++;if(isSalto(ev))ns++;}
      if (nl<C.minLanci||ns<C.minSalti) continue;
      const dc=evSub.filter(ev=>dbl.includes(ev));
      if (dc.length<nD) continue;
      for (const de of combIter(dc,nD)){
        _assignBestCalls++;
        const {sel,total}=assignBest(evSub,new Set(de),inclStaff);
        if (sel.length!==C.nSel) continue;
        // Tutti gli eventi di evSub devono avere almeno 1 risultato
        const selEvs=new Set(sel.map(x=>x.ev));
        if (!evSub.every(ev=>selEvs.has(ev))) continue;
        if (total>best){best=total;bestSel=sel;}
      }
    }
  }
  if (_ownCache){
    _evCands = null;
    console.log(`[searchOptimal] staff=[${inclStaff.map(r=>r.ev).join(',')||'—'}] eventi=${evList.length} assignBest=${_assignBestCalls} tempo=${Date.now()-_t0}ms best=${best}`);
  }
  return {total:best,sel:bestSel};
}

function buildOptDiagnostic(){
  const C=getC();
  const all=activeAll();
  const allEvs=[...new Set(all.map(r=>r.ev))];
  const indEvs=[...new Set(all.filter(r=>!r.isStaffetta).map(r=>r.ev))];
  const nDblNeeded=C.nSel-C.minEv;
  const dblEvs=allEvs.filter(ev=>all.filter(r=>r.ev===ev).length>=2);
  const lanciEvs=indEvs.filter(ev=>isLancio(ev));
  const saltiEvs=indEvs.filter(ev=>isSalto(ev));

  function row(ok, label, evList, needed, have){
    const ico=ok?'✅':'❌';
    const cnt=`<strong>${have}/${needed}</strong>`;
    const evStr=evList.length?`<span class="diag-ev">${evList.join(' · ')}</span>`:'';
    const miss=!ok?` — mancano ${needed-have}`:'';
    return `<div class="opt-diag-row"><span class="diag-ico">${ico}</span>${label}: ${cnt}${miss}${evStr}</div>`;
  }

  return `<div class="opt-diag">`+
    row(allEvs.length>=C.minEv,   'Gare disponibili', [], C.minEv, allEvs.length)+
    row(lanciEvs.length>=C.minLanci, 'Lanci', lanciEvs, C.minLanci, lanciEvs.length)+
    row(saltiEvs.length>=C.minSalti, 'Salti', saltiEvs, C.minSalti, saltiEvs.length)+
    row(dblEvs.length>=nDblNeeded,
        `Gare con ≥2 risultati (per ${nDblNeeded} doppiata${nDblNeeded===1?'':'e'})`,
        dblEvs, nDblNeeded, dblEvs.length)+
    `</div>`;
}

function setNoteEst(msg, isError=false){
  const errBanner=document.getElementById('calcola-err');
  if (isError){
    errBanner.innerHTML=msg;
    errBanner.style.display='block';
  } else {
    errBanner.style.display='none';
    errBanner.innerHTML='';
  }
}

async function _ensureManualEntries(){
  if (!currentCategoria) return;
  try {
    const resp = await fetch(`/api/manual?categoria=${encodeURIComponent(currentCategoria)}`);
    const json = await resp.json();
    if (!json.ok || !json.data.length) return;
    // Filtra per società corrente (stessa logica di checkSavedManualEntries)
    const relevant = json.data.filter(e =>
      !e.soc_cod || !currentSocieta || e.soc_cod === currentSocieta
    );
    if (!relevant.length) return;
    let added = false;
    for (const entry of relevant) {
      if (ALL.some(r => r.savedId === entry.savedId)) continue;
      const r = {
        id: manualIdCounter++,
        ev: entry.ev, type: entry.type,
        athlete: entry.athlete, athlete_url: '',
        perf: entry.perf, wind: entry.wind||'', piazz: entry.piazz||'',
        citta: entry.citta||'', data: entry.data||'', anno: entry.anno||'',
        pts: entry.pts||0, pts_ok: entry.pts_ok||false,
        isStaffetta: entry.isStaffetta||false,
        rawStaff: entry.rawStaff||'',
        staffAthl: entry.staffAthl||undefined,
        isManual: true, savedId: entry.savedId,
      };
      if (r.isStaffetta && !r.staffAthl)
        r.staffAthl = resolveStaffettaAthletes(r.rawStaff);
      ALL.push(r);
      added = true;
    }
    if (added) {
      computeBests(); buildEvFilterPanel();
      renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
      document.getElementById('manual-reload-bar').style.display = 'none';
      savedManualEntries = [];
    }
  } catch(e) {}
}

async function computeOptimal(){
  await _ensureManualEntries();
  const missing=activeAll().filter(r=>userPts[r.id]===undefined&&!r.pts_ok);
  if (missing.length>0){
    setNoteEst(`⚠ ${missing.length} risultat${missing.length===1?'o':'i'} senza punteggio — inserisci i punti FIDAL per tutti prima di calcolare.`, true);
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
      
      const _p = getFormParams();
      const payload = {
          categoria: currentCategoria,
          soc_cod:  _p.societa || '',
          soc_nome: document.getElementById('f-societa-name')?.value?.trim() || '',
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
          setNoteEst(`⚠ Impossibile trovare ${C.nSel} risultati validi che incastrino tutti i vincoli.`, true);
          staffAnalysis = [];
      } else {
          setNoteEst('');
          res.optimal.sel.forEach(r => selectedIds.add(Number(r.id)));

          // Costruisce analisi per ogni staffetta in activeAll() con pts_ok
          const baselineScore = res.baseline_score || 0;
          const staffScores   = res.staff_scores   || {};
          const staffette = activeAll().filter(r => r.isStaffetta && r.pts_ok);
          staffAnalysis = staffette.map(staff => {
              const inOpt  = selectedIds.has(staff.id);
              const sid    = String(staff.id);
              // tCon: se nell'ottimale usiamo il punteggio esatto; altrimenti il
              // best calcolato dall'ottimizzatore per quella combo (0 se non trovato).
              const tCon   = inOpt ? res.optimal.score : (staffScores[sid] || 0);
              const tSenza = baselineScore;
              const delta  = tCon - tSenza;
              return {staff, tCon, tSenza, delta, inOpt};
          });
      }

      renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
      renderStaffettaAnalysis();
      
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

// ── CLASSIFICA REGIONALE ──────────────────────────────────
async function computeClassifica(){
  if (!isProiezione || !ALL.length){
    console.log('[Classifica] Proiezione non caricata — carico da cache...');
    await fetchProiezione(false);
    return; // fetchProiezione chiama computeClassifica di nuovo dopo aver caricato
  }

  const content = document.getElementById('clas-screen-content');
  const title   = document.getElementById('clas-screen-title');

  // ── Percorso veloce: usa societies_meta pre-calcolata dal build ──────────
  if (Object.keys(_societiesMeta).length > 0){
    console.log('[Classifica] Uso societies_meta pre-calcolata dal JSON.');
    const data = Object.entries(_societiesMeta).map(([cod, m]) => ({...m, cod}));
    _renderClassifica(data, title, content);
    return;
  }

  // ── Fallback: calcola da ALL client-side (somma punti, no optimizer) ─────
  content.innerHTML = '<div style="color:var(--muted);font-size:.82rem;padding:.4rem 0">Calcolo classifica…</div>';
  await new Promise(r=>setTimeout(r,0));

  const _lKw=['peso','martello','giavellotto','disco','lancio','vortex','palla'];
  const _sKw=['lungo','triplo','alto','asta','salto'];
  const _cdsProgClas = CDS_PROGRAMS[currentCategoria];
  const bySOC={};
  for (const r of ALL){
    if (_cdsProgClas && !_cdsProgClas(r.ev)) continue;
    const key=r.soc_cod||r.soc_nome; if (!key) continue;
    if (!bySOC[key]) bySOC[key]={nome:r.soc_nome||key, results:[]};
    bySOC[key].results.push(r);
  }

  const data = Object.values(bySOC).map(({nome, results})=>{
    const evs=new Set(results.map(r=>r.ev));
    const evLow=[...evs].map(e=>e.toLowerCase());
    const n_lanci=evLow.filter(e=>_lKw.some(k=>e.includes(k))).length;
    const n_salti=evLow.filter(e=>_sKw.some(k=>e.includes(k))).length;
    const n_ev=evs.size;
    let total_pts=0,pts_corsa=0,pts_lanci=0,pts_salti=0,pts_staffette=0;
    for (const r of results){
      const p=pts(r)||0; total_pts+=p;
      const t=r.type||'corsa';
      if (t==='corsa'||t==='ostacoli') pts_corsa+=p;
      else if (t==='lancio')           pts_lanci+=p;
      else if (t==='salto')            pts_salti+=p;
      else if (t==='staffetta')        pts_staffette+=p;
    }
    const _Cc=CONSTRAINTS[currentCategoria]||CONSTRAINTS.default;
    return {nome,total_pts,pts_corsa,pts_lanci,pts_salti,pts_staffette,
            num_gare:n_ev,n_lanci,n_salti,can_compete:n_ev>=_Cc.minEv&&n_lanci>=_Cc.minLanci&&n_salti>=_Cc.minSalti};
  });
  console.log(`[Classifica] Calcolate ${data.length} società da ALL.`);
  _renderClassifica(data, title, content);
}

function _renderClassifica(data, title, content){
  const p=getFormParams();
  // Ordina per score ottimale (se calcolato) altrimenti per total_pts
  const _score = s => (s.optimal && s.optimal.score > 0) ? s.optimal.score : (s.total_pts || 0);
  const eligible=data.filter(s=>s.can_compete).sort((a,b)=>_score(b)-_score(a));
  const notElig=data.filter(s=>!s.can_compete).length;
  console.log(`[Classifica] ${eligible.length} competitive, ${notElig} non eleggibili. Top 5:`,
    eligible.slice(0,5).map(s=>({nome:s.nome,pts:s.total_pts})));

  document.getElementById('clas-screen-title').textContent=`Proiezione Regionale — ${p.regione}`;
  const _pending = eligible.filter(s=>!s.optimal||!s.optimal.score).length;
  const _subParts = [`CdS ${p.categoria} · ${p.tipo_attivita==='P'?'Outdoor':'Indoor'} ${p.anno}`];
  _subParts.push(`${eligible.filter(s=>s.optimal&&s.optimal.score>0).length} in classifica`);
  if (_pending) _subParts.push(`${_pending} in attesa calcolo`);
  if (notElig)  _subParts.push(`${notElig} non eleggibili`);
  document.getElementById('clas-screen-sub').textContent=_subParts.join(' · ');

  if (!eligible.length){
    content.innerHTML='<div style="color:var(--muted);font-size:.82rem">Nessuna società con requisiti CdS soddisfatti.</div>';
    return;
  }
  // Solo le società con optimal calcolato entrano in classifica
  // (tot. disponibile non rispetta i vincoli CdS → non confrontabile)
  const ranked = eligible.filter(s => s.optimal && s.optimal.score > 0);
  const pendingOpt = eligible.length - ranked.length;

  if (ranked.length === 0){
    const msg = pendingOpt > 0
      ? `Nessuna società ha ancora il punteggio CdS ottimale calcolato (${pendingOpt} competitive trovate). Rigenera il DB regionale per calcolare le schede ottimali.`
      : 'Nessuna società competitiva trovata.';
    content.innerHTML = `<div style="color:var(--muted);font-size:.85rem;padding:.5rem 0">${msg}</div>`;
    return;
  }

  _classificaRanked = ranked;
  _clasDataByRid = {};   // reset mappa rid → {s, maxPts, cat}
  const maxPts = ranked[0].optimal.score;
  const rows = ranked.flatMap((s,i)=>{
    const barW = Math.round(s.optimal.score/maxPts*100);
    const medal = i===0?'🥇':i===1?'🥈':i===2?'🥉':`${i+1}.`;
    const rid = `clas-row-${i}`;
    _clasDataByRid[rid] = {s, maxPts, cat: p.categoria};

    // Punteggio: "scheda ottimale" o "Σ totale disponibile"
    const scoreCell = `<span style="font-weight:700">${s.optimal.score.toLocaleString('it')}</span>
      <span title="Punteggio scheda CdS ottimale: selezione automatica dei 13 migliori risultati rispettando i vincoli (min 10 gare, min 2 lanci, min 2 salti, max 2 atlete per gara)" style="font-size:.66rem;color:var(--green);margin-left:.3rem;cursor:help">CdS ottimale</span>`;

    // Breakdown: Corsa (corse+ostacoli) · Salti · Lanci · Staffette
    const bk = [
      `🏃 ${(s.pts_corsa||0).toLocaleString('it')} cors.`,
      `↑ ${(s.pts_salti||0).toLocaleString('it')} salt.`,
      `⭕ ${(s.pts_lanci||0).toLocaleString('it')} lanc.`,
    ];
    if (s.pts_staffette) bk.push(`🔄 ${s.pts_staffette.toLocaleString('it')} staff.`);
    const breakdown = bk.join(' · ');

    // Bottone expand dettaglio
    const viewBtn = s.optimal.sel && s.optimal.sel.length
      ? `<button class="clas-expand" onclick="toggleClasDetail('${rid}')">+</button>`
      : '';

    // Bottone "Apri scheda" — carica la società nella vista tool
    // Usa virgolette singole per l'attributo onclick così JSON.stringify (doppie) non rompe l'HTML
    const openBtn = `<button class="btn-open-soc" title="Apri scheda"
        onclick='loadSocFromClassifica(${JSON.stringify(s.cod)})'>📂 Apri</button>`;

    // Badge gap al primo posto
    const gap = maxPts - s.optimal.score;
    const gapCell = i===0
      ? `<span class="clas-gap-zero">—</span>`
      : `<span class="clas-gap-badge">−${gap.toLocaleString('it')} pt</span>`;

    // Riga principale
    const mainRow = `<tr>
      <td class="clas-rank">${medal}</td>
      <td>${s.nome}</td>
      <td class="clas-score">${scoreCell}</td>
      <td style="color:var(--muted);font-size:.71rem;white-space:nowrap">${breakdown}</td>
      <td>${gapCell}</td>
      <td class="clas-bar-cell"><div class="clas-bar" style="width:${barW}%"></div></td>
      <td style="width:28px">${viewBtn}</td>
      <td style="width:70px">${openBtn}</td>
    </tr>`;

    // Riga dettaglio (scheda ottimale espandibile)
    let detailRow = '';
    if (s.optimal.sel && s.optimal.sel.length) {
      const detailRows = s.optimal.sel
        .sort((a,b)=>(b.pts||0)-(a.pts||0))
        .map(r=>{
          const typeLbl = r.isStaffetta ? '<span style="color:#1565c0;font-size:.68rem;font-weight:700">STAFF</span>'
                        : '<span style="color:var(--muted);font-size:.68rem">IND</span>';
          return `<tr>
            <td>${typeLbl}</td>
            <td style="font-weight:600">${r.ev}</td>
            <td>${r.athlete||''}</td>
            <td>${r.perf||''}</td>
            <td style="font-family:var(--mono);font-weight:700;color:var(--blue);text-align:right">${(r.pts||0).toLocaleString('it')}</td>
          </tr>`;
        }).join('');
      detailRow = `<tr class="clas-detail" id="${rid}">
        <td colspan="7">
          <table class="clas-detail-table">
            <thead><tr style="color:var(--muted);font-size:.67rem">
              <th></th><th>Disciplina</th><th>Atleta</th><th>Prestazione</th><th style="text-align:right">Punti</th>
            </tr></thead>
            <tbody>${detailRows}</tbody>
          </table>
          <div class="gap-panel" id="gap-panel-${rid}"></div>
        </td>
      </tr>`;
    }
    return [mainRow, detailRow].filter(Boolean);
  }).join('');

  content.innerHTML=`<table class="clas-table">
    <thead><tr>
      <th>#</th><th>Società</th>
      <th title="'CdS ottimale' = scheda calcolata con vincoli (13 ris., min 10 gare, min 2 lanci+salti); 'tot. disponibile' = somma grezza, rigenera il DB per il valore esatto">Punti</th>
      <th style="font-size:.67rem">Corsa · Salti · Lanci · Staff</th>
      <th title="Distanza in punti dal primo classificato">Gap al 1°</th>
      <th></th><th></th><th></th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function loadSocFromClassifica(cod){
  const meta = _societiesMeta[cod];
  if (!meta || !_currentProiezioneP) {
    alert('Dati non disponibili. Ricarica la proiezione regionale.');
    return;
  }

  // Aggiorna i campi del form così l'URL preview e getFormParams() sono coerenti
  const socInput = document.getElementById('f-societa');
  if (socInput) { socInput.value = cod; updateUrlPreview(); }
  const socNameEl = document.getElementById('f-societa-name');
  if (socNameEl) socNameEl.value = meta.nome || '';

  const p = { ..._currentProiezioneP, societa: cod, societa_nome: meta.nome || cod };

  document.getElementById('loading').classList.remove('hidden');
  _setLoadingMsg('Caricamento dati FIDAL…');

  try {
    // Chiamata FIDAL identica a fetchData() ma con la società specifica
    const params = new URLSearchParams({
      anno:          p.anno,
      tipo_attivita: p.tipo_attivita,
      sesso:         p.sesso,
      categoria:     p.categoria,
      regione:       p.regione,
      nazionalita:   p.nazionalita || '0',
      vento:         p.vento       || '2',
      limite:        p.limite      || '100',
      societa:       cod,
    });
    const resp = await fetch('/api/fetch?' + params);
    const json = await resp.json();
    if (!json.ok) throw new Error(json.error);

    ALL = json.data;
    selectedIds.clear(); userPts = {}; staffAnalysis = []; excludedEvs = new Set();
    unavailableAthletes = new Set(); minDateFilter = null; isProiezione = false;
    document.getElementById('staff-panel').style.display = 'none';

    computeBests();
    ALL.filter(r => r.isStaffetta).forEach(r => {
      r.staffAthl = resolveStaffettaAthletes(r.rawStaff);
    });

    // Applica la selezione ottimale pre-calcolata abbinando per (ev, athlete, perf)
    // Gli id cambiano a ogni fetch fresco, quindi non si può usare id numerico
    if (meta.optimal && meta.optimal.sel && meta.optimal.sel.length) {
      meta.optimal.sel.forEach(optR => {
        const match = ALL.find(r =>
          r.ev === optR.ev &&
          r.athlete === optR.athlete &&
          String(r.perf) === String(optR.perf)
        );
        if (match) selectedIds.add(match.id);
      });
    }

    setupToolScreen(p);
    show('scr-tool');
    renderProspetto(); renderAll(); updateConstraints(); renderAthleteTracker();
    await _applyAutoOpts();

  } catch(e) {
    alert('Errore caricamento società: ' + e.message);
    // Ripristina il LED se l'errore è di rete
    try { _setFidalStatus('error', `Errore — ${e.message.slice(0,60)}`); } catch(_){}
  } finally {
    document.getElementById('loading').classList.add('hidden');
  }
}

let _clasDataByRid = {};

function _nextLevel(tabEv, curPts) {
  // Trova il livello immediatamente superiore (minor delta pts) nella tabella
  let best = null;
  for (const [perf, pts] of Object.entries(tabEv || {})) {
    if (pts <= curPts) continue;
    const delta = pts - curPts;
    if (!best || delta < best.delta) best = {perf, pts, delta};
  }
  return best; // {perf, pts, delta} oppure null
}

async function _loadGapPanel(rid) {
  const panel = document.getElementById(`gap-panel-${rid}`);
  if (!panel || panel.dataset.loaded) return;
  panel.dataset.loaded = '1';

  const d = _clasDataByRid[rid];
  if (!d) { panel.style.display='none'; return; }
  const {s, maxPts, cat} = d;
  const gap = maxPts - s.optimal.score;

  panel.innerHTML = `<div class="gap-panel-title">Opportunità di miglioramento</div>
    <div style="font-size:.72rem;color:var(--muted);margin-bottom:.4rem">Caricamento tabelle…</div>`;

  const tabella = await _getTabellaCategoria(cat);

  const sel = (s.optimal.sel || []).filter(r => !r.isStaffetta && r.pts > 0);
  const improvements = [];
  for (const r of sel) {
    const tabEv = tabella[r.ev];
    if (!tabEv) continue;
    const next = _nextLevel(tabEv, r.pts);
    if (!next) continue;
    improvements.push({ev: r.ev, athlete: r.athlete||'', perf_cur: r.perf||'',
      pts_cur: r.pts, perf_next: next.perf, pts_next: next.pts, delta: next.delta});
  }
  improvements.sort((a, b) => b.delta - a.delta);

  if (!improvements.length) {
    panel.innerHTML = `<div class="gap-panel-title">Opportunità di miglioramento</div>
      <div style="font-size:.73rem;color:var(--muted)">Nessun livello superiore trovato in tabella per le gare selezionate.</div>`;
    return;
  }

  const totRec = improvements.reduce((s,r)=>s+r.delta, 0);
  const newTot  = s.optimal.score + totRec;
  const gapStr  = gap > 0 ? `<span style="font-size:.73rem;color:var(--muted)"> — gap al 1°: <strong style="color:#c0392b">−${gap.toLocaleString('it')} pt</strong></span>` : '';

  const trows = improvements.map(r =>
    `<tr>
      <td style="font-weight:600">${r.ev}</td>
      <td style="color:var(--muted);font-size:.72rem">${r.athlete}</td>
      <td style="font-family:var(--mono)">${r.perf_cur}</td>
      <td style="font-family:var(--mono)"><span class="gap-perf-arr">→</span>${r.perf_next}</td>
      <td style="text-align:right"><span class="gap-delta">+${r.delta.toLocaleString('it')}</span></td>
    </tr>`
  ).join('');

  panel.innerHTML = `
    <div class="gap-panel-title">Opportunità di miglioramento${gapStr}</div>
    <table class="gap-impr-table">
      <thead><tr>
        <th>Disciplina</th><th>Atleta</th><th>Prest. attuale</th><th>Target tabella</th>
        <th style="text-align:right">Guadagno</th>
      </tr></thead>
      <tbody>${trows}</tbody>
      <tfoot><tr class="gap-tot-row">
        <td colspan="4">Totale recuperabile (tutti i livelli)</td>
        <td style="text-align:right;font-family:var(--mono)">+${totRec.toLocaleString('it')} pt → ${newTot.toLocaleString('it')}</td>
      </tr></tfoot>
    </table>`;
}

async function toggleClasDetail(rid){
  const row = document.getElementById(rid);
  if (!row) return;
  const isOpen = row.classList.contains('open');
  row.classList.toggle('open', !isOpen);
  const btn = row.previousElementSibling?.querySelector('.clas-expand');
  if (btn) btn.textContent = isOpen ? '+' : '−';
  if (!isOpen) await _loadGapPanel(rid);
}

// ── CLASSIFICA EXPORT ────────────────────────────────────
function downloadClassificaCSV(){
  if (!_classificaRanked || !_classificaRanked.length){ alert('Nessuna classifica disponibile. Carica prima la proiezione regionale.'); return; }
  const p = getFormParams();
  const hdr = ['Rank','Società','Punti_CdS','Corsa','Salti','Lanci','Staffette','Gare_CdS','N_Lanci','N_Salti'];
  const rows = [hdr];
  _classificaRanked.forEach((s,i)=>{
    rows.push([
      i+1, s.nome, s.optimal.score,
      s.pts_corsa||0, s.pts_salti||0, s.pts_lanci||0, s.pts_staffette||0,
      s.num_gare||0, s.n_lanci||0, s.n_salti||0,
    ]);
    if (s.optimal.sel && s.optimal.sel.length){
      rows.push(['','— Scheda ottimale —','','','','','','','','','']);
      s.optimal.sel.slice().sort((a,b)=>(b.pts||0)-(a.pts||0)).forEach(r=>{
        rows.push(['', '', r.pts||0, '', '', '', '', r.ev, r.athlete||'', r.anno||'', r.perf||'']);
      });
    }
  });
  const csv = rows.map(r=>r.map(v=>`"${String(v).replace(/"/g,'""')}"`).join(',')).join('\r\n');
  const blob = new Blob(['﻿'+csv],{type:'text/csv;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  const tag = `Classifica_${p.categoria}_${p.tipo_attivita==='P'?'Outdoor':'Indoor'}_${p.anno}_${p.regione}`;
  a.download = `${tag}.csv`;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}

function printClassificaPDF(){
  if (!_classificaRanked || !_classificaRanked.length){ alert('Nessuna classifica disponibile. Carica prima la proiezione regionale.'); return; }
  const p = getFormParams();
  const today = new Date().toLocaleDateString('it-IT',{day:'2-digit',month:'2-digit',year:'numeric'});
  const tipoLbl = p.tipo_attivita==='P' ? 'Outdoor' : 'Indoor';
  const titleStr = `Proiezione Regionale — ${p.regione}`;
  const subStr   = `CdS ${p.categoria} · ${tipoLbl} ${p.anno}`;

  const medalStr = i => i===0?'🥇':i===1?'🥈':i===2?'🥉':`${i+1}.`;

  const mainRows = _classificaRanked.map((s,i)=>{
    const bk = [
      `🏃 ${(s.pts_corsa||0).toLocaleString('it')}`,
      `↑ ${(s.pts_salti||0).toLocaleString('it')}`,
      `⭕ ${(s.pts_lanci||0).toLocaleString('it')}`,
    ];
    if (s.pts_staffette) bk.push(`🔄 ${s.pts_staffette.toLocaleString('it')}`);
    const rowBg = i%2===0 ? '' : 'background:#f8fafc';
    return `<tr style="border-bottom:1px solid #e2e8f0;${rowBg}">
      <td style="padding:6px 8px;font-size:13pt;text-align:center;width:36px">${medalStr(i)}</td>
      <td style="padding:6px 8px;font-weight:700;font-size:9.5pt">${s.nome}</td>
      <td style="padding:6px 8px;font-family:monospace;font-weight:800;font-size:11pt;color:#054FAE;text-align:right;white-space:nowrap">${s.optimal.score.toLocaleString('it')}</td>
      <td style="padding:6px 8px;font-size:7.5pt;color:#555;white-space:nowrap">${bk.join(' · ')}</td>
    </tr>`;
  }).join('');

  const html = `<!DOCTYPE html>
<html lang="it"><head>
<meta charset="UTF-8">
<title>${titleStr}</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Arial',sans-serif;padding:14mm 18mm;color:#0d1f3c;font-size:10pt}
  @page{size:A4;margin:0}
  @media print{body{padding:10mm 14mm}}
</style>
</head><body>

<div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:14px;border-bottom:3px solid #054FAE;padding-bottom:10px">
  <div>
    <div style="font-size:7pt;text-transform:uppercase;letter-spacing:.1em;color:#6b82a0;margin-bottom:3px">Campionato di Società — Proiezione Regionale</div>
    <div style="font-size:18pt;font-weight:800;color:#054FAE;line-height:1.1">${titleStr}</div>
    <div style="font-size:10pt;color:#444;margin-top:3px">${subStr}</div>
  </div>
  <div style="text-align:right;font-size:8pt;color:#6b82a0">
    <div>Generato il ${today}</div>
    <div style="margin-top:4px;font-size:7pt">FIDAL CdS Tool</div>
  </div>
</div>

<table style="width:100%;border-collapse:collapse">
  <thead>
    <tr style="background:#054FAE;color:#fff">
      <th style="padding:6px 8px;text-align:center;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;width:36px">#</th>
      <th style="padding:6px 8px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase">Società</th>
      <th style="padding:6px 8px;text-align:right;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;width:90px">Punti CdS</th>
      <th style="padding:6px 8px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase">Corsa · Salti · Lanci · Staff</th>
    </tr>
  </thead>
  <tbody>${mainRows}</tbody>
</table>

<div style="margin-top:10px;font-size:7pt;color:#999">Punti CdS = scheda ottimale calcolata con vincoli CdS (13 risultati, min 10 gare, min 2 lanci, min 2 salti)</div>

<script>window.onload=function(){window.print();}<\/script>
</body></html>`;

  const w = window.open('','_blank','width=860,height=700');
  w.document.open(); w.document.write(html); w.document.close();
}

// ── STAFFETTA ANALYSIS ────────────────────────────────────
function renderStaffettaAnalysis(){
  const panel=document.getElementById('staff-panel');
  const container=document.getElementById('staff-cards');
  if (!staffAnalysis.length){
    container.innerHTML='';
    panel.style.display='none';
    return;
  }
  panel.style.display='';
  container.innerHTML=staffAnalysis.map(({staff,tCon,tSenza,delta,inOpt})=>{
    const p=pts(staff);
    const chips=(staff.staffAthl||[staff.athlete]).map(a=>`<span class="chip">${a}</span>`).join('');
    let cardCls, verdictCls, verdictTxt, conLabel;
    if (inOpt){
      cardCls='ok'; verdictCls='ok';
      verdictTxt=`✅ Nell'ottimale · +${delta} pt vs. nessuna staffetta`;
      conLabel=`<strong>${tCon} pt</strong>`;
    } else if (tCon===0){
      // Combo potata interamente dal B&B prima della valutazione: punteggio non disponibile
      cardCls='no'; verdictCls='no';
      verdictTxt=`❌ Esclusa — combinazione non valutata (superata dall'ottimale)`;
      conLabel=`<span style="color:var(--muted)">n/d</span>`;
    } else if (delta>0){
      cardCls='warn'; verdictCls='warn';
      const v2=validate();
      const athls=staff.staffAthl||[staff.athlete];
      const bloccate=athls.filter(a=>(v2.atlCount[a]||0)>=2);
      const motivo=bloccate.length
        ? `${bloccate.join(', ')} ${bloccate.length===1?'è già':'sono già'} a 2 gare nell'ottimale`
        : 'le atlete hanno più valore nelle gare individuali dell\'ottimale';
      verdictTxt=`⚠ Conviene da sola (+${delta} pt) ma esclusa — ${motivo}`;
      conLabel=`<strong>${tCon} pt</strong>`;
    } else {
      cardCls='no'; verdictCls='no';
      verdictTxt=`❌ Non conviene (${delta} pt) — le atlete valgono di più individualmente`;
      conLabel=`<strong>${tCon} pt</strong>`;
    }
    const manBadge=staff.isManual?'<span class="manual-badge">manuale</span>':'';
    return `<div class="staff-card ${cardCls}">
      <div class="scard-head">
        <span class="scard-ev">${staff.ev}${manBadge}</span>
        <span class="scard-perf">${staff.perf}</span>
        <span class="scard-pts">${p} pt${staff.est&&userPts[staff.id]===undefined?' ~':''}</span>
        <span style="margin-left:auto;font-size:.68rem;color:var(--muted)">${(staff.staffAthl||[]).length} atlete</span>
      </div>
      <div class="chips">${chips}</div>
      <div style="font-size:.7rem;color:var(--muted)">
        Con: ${conLabel} &nbsp;|&nbsp; Senza: <strong>${tSenza} pt</strong>
      </div>
      <div class="scard-verdict ${verdictCls}">${verdictTxt}</div>
    </div>`;
  }).join('');
}

// ── CSV EXPORT ────────────────────────────────────────────
function downloadCSV(){
  if (!topCombinations.length){ alert('Esegui prima ⚡ Calcola Ottimale per generare le combinazioni.'); return; }
  const hdr=['Rank','Totale_pt','Configurazione_staffette','Posizione','Gara','Tipo','Atleta_e','Prestazione','Punti_FIDAL'];
  const rows=[hdr];
  topCombinations.forEach(({total,inclStaff,sel},ci)=>{
    const sorted=[...sel].sort((a,b)=>pts(b)-pts(a));
    sorted.forEach((r,i)=>{
      const athlStr=r.isStaffetta?(r.staffAthl||[r.athlete]).join(' / '):r.athlete;
      rows.push([ci+1, total, inclStaff, i+1, r.ev, r.type, athlStr, r.perf, pts(r)]);
    });
  });
  const csv=rows.map(r=>r.map(v=>`"${String(v).replace(/"/g,'""')}"`).join(',')).join('\r\n');
  const blob=new Blob(['﻿'+csv],{type:'text/csv;charset=utf-8'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  const soc=document.getElementById('f-societa').value||'CdS';
  a.download=`${soc}_combinazioni.csv`;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}

// ── STAMPA / PDF ──────────────────────────────────────────
function printPDF(){
  const sel=ALL.filter(r=>selectedIds.has(r.id)).sort((a,b)=>pts(b)-pts(a));
  if (!sel.length){ alert('Nessun risultato selezionato. Seleziona i risultati prima di stampare.'); return; }

  const v=validate();
  const total=sel.reduce((s,r)=>s+pts(r),0);
  const title=document.getElementById('tool-title').textContent;
  const sub=document.getElementById('tool-sub').textContent;
  const today=new Date().toLocaleDateString('it-IT',{day:'2-digit',month:'2-digit',year:'numeric'});

  const ec={};
  sel.forEach(r=>ec[r.ev]=(ec[r.ev]||0)+1);

  const rows=sel.map((r,i)=>{
    const p=pts(r);
    const athlD=r.isStaffetta?(r.staffAthl||[r.athlete]).join(' / '):r.athlete;
    const dbl=ec[r.ev]===2?' <span style="background:#fef3c7;color:#92400e;font-size:7pt;padding:1px 4px;border-radius:3px">×2</span>':'';
    const best=r.isBest?'<span style="color:#c0392b;font-weight:800">*</span> ':'';
    const typeCols={corsa:'#dbeafe',ostacoli:'#ede9fe',salto:'#d1fae5',lancio:'#fef3c7',staffetta:'#fce7f3'};
    const typeClr=typeCols[r.type]||'#f0f4f9';
    return `<tr style="border-bottom:1px solid #e2e8f0;${i%2===0?'':'background:#f8fafc'}">
      <td style="padding:5px 7px;color:#6b82a0;font-size:8pt;text-align:center">${i+1}</td>
      <td style="padding:5px 7px"><span style="background:${typeClr};font-size:7pt;font-weight:700;text-transform:uppercase;letter-spacing:.05em;padding:2px 5px;border-radius:3px">${TYPE_LBL[r.type]}</span></td>
      <td style="padding:5px 7px;font-weight:600;font-size:9pt">${r.ev}${dbl}</td>
      <td style="padding:5px 7px;font-size:8.5pt">${athlD}</td>
      <td style="padding:5px 7px;font-family:monospace;font-size:8pt;color:#6b82a0;text-align:center">${r.anno||''}</td>
      <td style="padding:5px 7px;font-family:monospace;font-weight:600;color:#054FAE;font-size:9pt">${best}${r.perf}${r.wind?' <span style="font-size:7pt;color:#999">'+r.wind+'</span>':''}</td>
      <td style="padding:5px 7px;font-size:8pt;color:#6b82a0">${r.piazz||''}</td>
      <td style="padding:5px 7px;font-size:8pt;color:#6b82a0">${r.citta||''}</td>
      <td style="padding:5px 7px;font-family:monospace;font-size:8pt;color:#6b82a0">${r.data||''}</td>
      <td style="padding:5px 7px;text-align:right;font-family:monospace;font-weight:700;font-size:9.5pt;color:${p>0?'#054FAE':'#999'}">${p||'—'}</td>
    </tr>`;
  }).join('');

  const vincoli=`
    <span style="${v.nSel===13?'color:#1a7f3c':'color:#d46b08'}">● ${v.nSel}/13 risultati</span>
    &nbsp;&nbsp;
    <span style="${v.nEv>=10?'color:#1a7f3c':'color:#d46b08'}">● ${v.nEv} gare</span>
    &nbsp;&nbsp;
    <span style="${v.nLanci>=2?'color:#1a7f3c':'color:#d46b08'}">● ${v.nLanci}/2 lanci</span>
    &nbsp;&nbsp;
    <span style="${v.nSalti>=2?'color:#1a7f3c':'color:#d46b08'}">● ${v.nSalti}/2 salti</span>
    &nbsp;&nbsp;
    <span style="${v.evOk&&v.atlOk?'color:#1a7f3c':'color:#c0392b'}">● Vincoli ${v.evOk&&v.atlOk?'OK':'VIOLATI'}</span>`;

  const html=`<!DOCTYPE html>
<html lang="it"><head>
<meta charset="UTF-8">
<title>Scheda CdS — ${title}</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Arial',sans-serif;padding:15mm 18mm;color:#0d1f3c;font-size:10pt}
  @page{size:A4;margin:0}
  @media print{body{padding:10mm 14mm}}
</style>
</head><body>

<div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:14px;border-bottom:3px solid #054FAE;padding-bottom:10px">
  <div>
    <div style="font-size:7pt;text-transform:uppercase;letter-spacing:.1em;color:#6b82a0;margin-bottom:3px">Scheda Campionato di Società — Fase Provinciale</div>
    <div style="font-size:18pt;font-weight:800;color:#054FAE;line-height:1.1">${title}</div>
    <div style="font-size:10pt;color:#444;margin-top:3px">${sub}</div>
  </div>
  <div style="text-align:right;font-size:8pt;color:#6b82a0">
    <div>Generato il ${today}</div>
    <div style="margin-top:4px;font-size:7pt">FIDAL CdS Tool</div>
  </div>
</div>

<div style="margin-bottom:10px;font-size:8pt">${vincoli}</div>

<table style="width:100%;border-collapse:collapse">
  <thead>
    <tr style="background:#054FAE;color:#fff">
      <th style="padding:6px 7px;text-align:center;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;width:28px">#</th>
      <th style="padding:6px 7px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;width:70px">Tipo</th>
      <th style="padding:6px 7px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase">Disciplina</th>
      <th style="padding:6px 7px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase">Atleta/e</th>
      <th style="padding:6px 7px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;width:38px">Anno</th>
      <th style="padding:6px 7px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase">Prestazione</th>
      <th style="padding:6px 7px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;width:60px">Piazz.</th>
      <th style="padding:6px 7px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;width:65px">Città</th>
      <th style="padding:6px 7px;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;width:45px">Data</th>
      <th style="padding:6px 7px;text-align:right;font-size:7.5pt;font-weight:700;letter-spacing:.05em;text-transform:uppercase;width:55px">Punti</th>
    </tr>
  </thead>
  <tbody>${rows}</tbody>
  <tfoot>
    <tr style="background:#054FAE;color:#fff">
      <td colspan="9" style="padding:7px 7px;font-size:9pt;font-weight:700;text-transform:uppercase;letter-spacing:.05em">Totale scheda</td>
      <td style="padding:7px 7px;text-align:right;font-family:monospace;font-size:13pt;font-weight:800;color:#00C9FF">${total.toLocaleString('it')}</td>
    </tr>
  </tfoot>
</table>

<div style="margin-top:10px;font-size:7pt;color:#999">
  * = miglior prestazione nella disciplina &nbsp;|&nbsp; ×2 = gara con doppio risultato
</div>

<script>window.onload=function(){window.print();}<\/script>
</body></html>`;

  const w=window.open('','_blank','width=900,height=750');
  w.document.open(); w.document.write(html); w.document.close();
}
