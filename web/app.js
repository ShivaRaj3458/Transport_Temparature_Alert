const $ = (id) => document.getElementById(id);
let state;
let incidentFilter = 'all';
const fmtTime = (value) => value ? new Date(value).toLocaleString([], {month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '—';
const fmtTemp = (value) => `${Number(value).toFixed(1)}°C`;
const fmtPercent = (value) => `${Number(value).toFixed(1)}%`;
const metricValue = (value, metric) => metric === 'HUMIDITY' ? fmtPercent(value) : fmtTemp(value);
const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const policyName = (id) => state?.policies.find(p => p.product_id === id)?.product_name || id;
async function loadDashboard(){
  try {const res=await fetch('/api/dashboard'); if(!res.ok) throw new Error('API returned '+res.status); state=await res.json(); render();}
  catch(err){document.querySelectorAll('.loading').forEach(e=>e.textContent='Unable to load dashboard. Is the API running?'); showToast(err.message);}
}
function stateClass(value){return String(value||'UNKNOWN').toLowerCase().replace(/[^a-z]+/g,'-')}
function render(){
  const d=state, active=d.incidents.filter(i=>['ACTIVE','MONITORING','RECOVERY_CHECK'].includes(i.status)).length;
  const stale=d.trucks.filter(t=>t.freshness_state==='STALE').length;
  $('as-of').textContent='· REPLAY THROUGH '+fmtTime(d.as_of); $('truck-count').textContent=d.trucks.length;
  const activeAlerts=d.alerts.filter(a=>a.status==='ACTIVE').length;
  $('active-count').textContent=activeAlerts; $('resolved-note').textContent=`${active} open episode${active===1?'':'s'} · ${d.alerts.length} simulated alert${d.alerts.length===1?'':'s'} total`;
  $('quality-count').textContent=stale; $('reading-count').textContent=d.reading_count;
  $('nav-count').textContent=active;
  renderFleet(); renderAttention(); renderSensors(); renderIncidents(); renderAlerts(); renderPolicies();
}
function renderFleet(){
  const rows=[...state.trucks].sort((a,b)=>a.truck_id.localeCompare(b.truck_id,undefined,{numeric:true}));
  $('fleet-rows').innerHTML=rows.map(t=>{
    const tempState=t.current_state||t.band; const fresh=t.freshness_state!=='STALE';
    const productPolicy=state.policies.find(p=>p.product_id===t.product_id)||{};
    const humidityBand=t.humidity_pct==null?'UNKNOWN':t.humidity_pct>productPolicy.max_humidity_pct?'HIGH':t.humidity_pct<productPolicy.min_humidity_pct?'LOW':'NORMAL';
    const label=t.sensor_health==='UNKNOWN'?'UNKNOWN · no sensor ID':t.sensor_health;
    return `<tr><td><strong class="primary-cell">${escapeHtml(t.truck_id)}</strong><small>${escapeHtml(t.shipment_id)}</small></td>
      <td><strong class="primary-cell">${escapeHtml(policyName(t.product_id))}</strong><small>v${t.policy_version} · ${escapeHtml(t.product_id)}</small></td>
      <td><strong class="primary-cell ${t.band==='NORMAL'?'':'temp-alert'}">${fmtTemp(t.temperature_c)}</strong><small>${t.humidity_pct==null?'RH unavailable':`RH ${fmtPercent(t.humidity_pct)}`} · Event ${fmtTime(t.event_timestamp)}</small><small>Received ${fmtTime(t.received_timestamp)}</small></td>
      <td>${fmtTemp(t.range_min_c)} – ${fmtTemp(t.range_max_c)}<small>Humidity ${fmtPercent(productPolicy.min_humidity_pct??0)} – ${fmtPercent(productPolicy.max_humidity_pct??100)}</small></td>
      <td><span class="state ${stateClass(tempState)}">${escapeHtml(tempState)}</span><small><span class="state ${humidityBand==='NORMAL'?'normal':humidityBand==='UNKNOWN'?'unknown':'active'}">RH ${humidityBand}</span></small></td>
      <td><span class="quality-pill ${fresh?'fresh':'stale'}">${fresh?'FRESH · REPLAY':'STALE / UNKNOWN'}</span><small>Max gap ${Number(t.max_gap_min).toFixed(0)} min</small></td>
      <td><span class="sensor-state ${stateClass(t.sensor_health)}">${escapeHtml(label)}</span></td></tr>`;
  }).join('');
}
function renderAttention(){
  const open=state.incidents.filter(i=>['ACTIVE','MONITORING','RECOVERY_CHECK','UNKNOWN'].includes(i.status));
  const flags=state.quality_events.filter(q=>!['GAP','SENSOR_HEALTH_SUMMARY'].includes(q.type));
  const items=[...open.map(i=>({kind:'incident',data:i})),...flags.map(q=>({kind:'quality',data:q}))].slice(0,8);
  $('attention-list').innerHTML=items.length?items.map(({kind,data})=>kind==='incident'?`<article class="attention-card ${stateClass(data.status)}"><div class="attention-icon">!</div><div class="attention-copy"><div class="attention-top"><strong>${escapeHtml(data.truck_id)} | ${data.metric==='HUMIDITY'?'Humidity':'Temperature'} ${data.direction==='HIGH'?'high':'low'}</strong><span class="state ${stateClass(data.status)}">${escapeHtml(data.status)}</span></div><p>${metricValue(data.latest_value??data.latest_temperature_c,data.metric)} | ${Number(data.duration_min).toFixed(0)} observed min of ${data.tolerance_min} min tolerance</p><small>${escapeHtml(data.shipment_id)} | ${escapeHtml(policyName(data.product_id))} | started ${fmtTime(data.started_at)}</small></div></article>`:`<article class="attention-card quality"><div class="attention-icon">!</div><div class="attention-copy"><div class="attention-top"><strong>${escapeHtml(data.truck_id)}${data.sensor_id?` | ${escapeHtml(data.sensor_id)}`:''} | ${escapeHtml(data.type.replace('SUSPECT_','SUSPECT '))}</strong><span class="quality-pill ${stateClass(data.type)}">DATA QUALITY</span></div><p>${escapeHtml(data.details)}</p><small>${escapeHtml(data.timestamp||'')} | temperature and humidity evaluation continue independently</small></div></article>`).join(''):'<div class="empty-state"><span>OK</span><strong>No open temperature or humidity incidents</strong><p>Current valid readings are within their assigned policy ranges.</p></div>';
}
function renderSensors(){
  const sensors=state.sensors||[];
  $('sensor-grid').innerHTML=sensors.length?sensors.map(s=>{
    const statusClass=stateClass(s.status);
    const evidence=[...(s.evidence_types||[])];
    if(s.jump_events)evidence.push(`${s.jump_events} jump${s.jump_events===1?'':'s'}`);
    if(s.stuck_runs)evidence.push(`${s.stuck_runs} stuck run${s.stuck_runs===1?'':'s'}`);
    if(s.humidity_jump_events)evidence.push(`${s.humidity_jump_events} humidity jump${s.humidity_jump_events===1?'':'s'}`);
    if(s.humidity_stuck_runs)evidence.push(`${s.humidity_stuck_runs} humidity flatline${s.humidity_stuck_runs===1?'':'s'}`);
    if(s.humidity_missing_count)evidence.push(`${s.humidity_missing_count} missing humidity samples`);
    if(s.disagreement_timestamps)evidence.push(`${s.disagreement_timestamps} cross-sensor mismatches`);
    if(s.diagnostic_status)evidence.push(`diagnostic: ${s.diagnostic_status}`);
    const explanation={HEALTHY:'No remaining fault evidence; a single spike cleared after normal follow-up readings.',SUSPECT:'Evidence needs review. More than one independent signal is required to confirm a fault.',FAULTY:'A device diagnostic or multiple independent evidence types confirmed a sensor fault.',STALE:'Telemetry stopped beyond the continuity limit. Missing readings do not prove sensor failure.'}[s.status]||s.summary;
    return `<article class="sensor-card ${statusClass}"><div class="sensor-card-top"><div><span class="eyebrow">${escapeHtml(s.truck_id)} · ${escapeHtml(s.product_id)}</span><h3>${escapeHtml(s.sensor_id)}</h3></div><span class="sensor-badge ${statusClass}">${escapeHtml(s.status)}</span></div><p>${escapeHtml(explanation)}</p><div class="sensor-tags">${evidence.length?evidence.map(tag=>`<span>${escapeHtml(tag)}</span>`).join(''):'<span>No fault indicators</span>'}</div><div class="sensor-card-foot"><span>${s.sample_count} readings · latest ${fmtTime(s.latest_timestamp)}</span><span>max gap ${Number(s.max_gap_min).toFixed(0)} min</span></div></article>`;
  }).join(''):'<div class="empty-state"><strong>No sensor IDs in the replay</strong><p>Sensor-health cases require sensor identifiers.</p></div>';
}
function renderIncidents(){
  const alertByIncident=new Map(state.alerts.map(a=>[a.incident_id,a]));
  let incidents=[...state.incidents].sort((a,b)=>b.started_at.localeCompare(a.started_at));
  if(incidentFilter!=='all') incidents=incidents.filter(i=>i.status===incidentFilter);
  $('incident-rows').innerHTML=incidents.length?incidents.map(i=>{
    const alert=alertByIncident.get(i.incident_id);
    const policy=state.policies.find(p=>p.product_id===i.product_id)||{};
    const threshold=i.threshold_value??(i.metric==='HUMIDITY'?(i.direction==='HIGH'?policy.max_humidity_pct:policy.min_humidity_pct):(i.direction==='HIGH'?policy.max_temp_c:policy.min_temp_c));
    const value=i.metric==='HUMIDITY'?i.latest_value:i.latest_temperature_c;
    const evidence=i.evidence||alert?.evidence||[];
    const details=evidence.map(r=>`<span>${fmtTime(r.timestamp)} <b>${i.metric==='HUMIDITY'?fmtPercent(r.humidity_pct):fmtTemp(r.temperature_c)}</b>${i.metric==='TEMPERATURE'&&r.humidity_pct!=null?` RH ${fmtPercent(r.humidity_pct)}`:''}</span>`).join('')||'<span>Episode did not cross alert tolerance.</span>';
    const kind=i.metric==='HUMIDITY'?'HUMIDITY':'TEMP';
    return `<tr class="incident-main"><td><strong class="primary-cell">${escapeHtml(i.incident_id)}</strong><small>${escapeHtml(i.truck_id)} | ${escapeHtml(i.shipment_id)}</small></td><td><span class="type-${i.direction.toLowerCase()}">${kind} ${i.direction}</span>${alert?.immediate?' <span class="immediate-pill">EXTREME</span>':''}<small>Limit ${metricValue(threshold,i.metric)}</small></td><td>${fmtTime(i.started_at)}<small>Ended ${fmtTime(i.ended_at)}</small></td><td><strong>${metricValue(value,i.metric)}</strong>${i.resolved_at?`<small>Resolved ${fmtTime(i.resolved_at)}</small>`:`<small>${i.recovery_confirmations||0} recovery confirmations</small>`}</td><td>${Number(i.duration_min).toFixed(0)} / ${i.tolerance_min} min<small>${alert?`Triggered ${fmtTime(alert.triggered_at)}`:'No alert emitted'}</small></td><td>${escapeHtml(policyName(i.product_id))}<small>${escapeHtml(i.product_id)} | v${i.policy_version}</small></td><td><span class="state ${stateClass(i.status)}">${escapeHtml(i.status)}</span></td></tr><tr class="evidence-row"><td colspan="7"><details><summary>View ${evidence.length||0} supporting readings${alert?` | ${escapeHtml(alert.reason)}`:''}</summary><div class="evidence-list">${details}</div></details></td></tr>`;
  }).join(''):'<tr><td colspan="7" class="loading">No incidents match this state.</td></tr>';
}
function renderAlerts(){
  const alerts=[...state.alerts].sort((a,b)=>b.triggered_at.localeCompare(a.triggered_at));
  $('alert-list').innerHTML=alerts.length?alerts.map(a=>{
    const kind=a.metric==='HUMIDITY'?'humidity':'temperature';
    const evidence=a.evidence.map(r=>`<span>${fmtTime(r.timestamp)} <b>${a.metric==='HUMIDITY'?fmtPercent(r.humidity_pct):fmtTemp(r.temperature_c)}</b>${a.metric==='TEMPERATURE'&&r.humidity_pct!=null?` RH ${fmtPercent(r.humidity_pct)}`:''}</span>`).join('');
    return `<article class="alert-card"><div class="alert-marker">!</div><div class="alert-body"><div class="alert-heading"><div><span class="severity">${escapeHtml(a.severity)} | ${a.immediate?'IMMEDIATE EXTREME':'TOLERANCE CROSSED'}</span><h2>${escapeHtml(a.truck_id)} | ${a.direction==='HIGH'?'High':'Low'}-${kind} excursion</h2></div><span class="state ${stateClass(a.status)}">${escapeHtml(a.status)}</span></div><p class="alert-reason">${escapeHtml(a.reason)}</p><div class="alert-meta"><span><small>ALERT TIME</small>${fmtTime(a.triggered_at)}</span><span><small>SHIPMENT / PRODUCT</small>${escapeHtml(a.shipment_id)} | ${escapeHtml(policyName(a.product_id))}</span><span><small>LIMIT / OBSERVED</small>${metricValue(a.threshold_value,a.metric)} limit | ${Number(a.duration_min).toFixed(0)} min observed / ${a.tolerance_min} min allowed</span><span><small>POLICY</small>${escapeHtml(a.product_id)} | v${a.policy_version}</span></div><details><summary>Evidence | ${a.evidence.length} readings</summary><div class="evidence-list">${evidence}</div></details><div class="alert-footer"><span>SIMULATED ALERT | NOT SENT</span><span>Acknowledgement unavailable in replay</span></div></div></article>`;
  }).join(''):'<div class="empty-state"><span>OK</span><strong>No alerts for the current policies</strong><p>Short excursions remain visible in the incident view without triggering repeated notifications.</p></div>';
}
function renderPolicies(){
  $('policy-cards').innerHTML=state.policies.map(p=>{
    const assigned=Object.entries(state.assignments).filter(([,a])=>a.product_id===p.product_id).map(([truck])=>truck);
    return `<article class="policy-card"><div class="policy-card-head"><div><span class="eyebrow">${escapeHtml(p.product_id)}</span><h2>${escapeHtml(p.product_name||p.product_id)}</h2></div><span class="version-chip">VERSION ${p.version}</span></div><div class="range-display"><div><small>TEMPERATURE RANGE</small><strong>${fmtTemp(p.min_temp_c)} - ${fmtTemp(p.max_temp_c)}</strong></div></div><div class="range-display humidity-range"><div><small>HUMIDITY RANGE</small><strong>${fmtPercent(p.min_humidity_pct)} - ${fmtPercent(p.max_humidity_pct)}</strong></div></div><div class="policy-facts"><span><b>High / low temp tolerance</b>${p.high_tolerance_min} / ${p.low_tolerance_min} min</span><span><b>High / low humidity tolerance</b>${p.high_humidity_tolerance_min} / ${p.low_humidity_tolerance_min} min</span><span><b>Recovery band</b>${fmtTemp(p.recovery_min_temp_c)} - ${fmtTemp(p.recovery_max_temp_c)}</span><span><b>Recovery check</b>${p.recovery_readings} readings | ${p.recovery_min_duration_min} min</span><span><b>Continuity gap</b>${p.max_gap_min} min</span><span><b>Allowed lateness</b>${p.allowed_lateness_min} min</span><span><b>Temp extremes</b>${fmtTemp(p.extreme_low_c)} / ${fmtTemp(p.extreme_high_c)}</span><span><b>Humidity extremes</b>${fmtPercent(p.extreme_low_humidity_pct)} / ${fmtPercent(p.extreme_high_humidity_pct)}</span><span><b>Effective from</b>${fmtTime(p.effective_at)}</span></div><div class="assignment-row"><small>DEMO ASSIGNMENTS</small><span>${assigned.length?assigned.map(escapeHtml).join(', '):'No trucks assigned'}</span></div><button class="secondary-button policy-edit" data-product="${escapeHtml(p.product_id)}">Edit policy - create version</button></article>`;
  }).join('');
  $('policy-history').innerHTML=state.policy_history.length?state.policy_history.map(h=>`<tr><td><strong>${escapeHtml(policyName(h.product_id))}</strong><small>${escapeHtml(h.product_id)}</small></td><td>v${h.version}</td><td>${fmtTime(h.effective_at)}</td><td>${fmtTime(h.changed_at)}</td></tr>`).join(''):'<tr><td colspan="4" class="loading">No policy changes yet.</td></tr>';
}
function showToast(text){const t=$('toast');t.textContent=text;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2800)}
function showView(name){document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id===`view-${name}`));document.querySelectorAll('.nav-item[data-view]').forEach(a=>a.classList.toggle('active',a.dataset.view===name));$('crumb-page').textContent=({overview:'Fleet overview',incidents:'Incidents',alerts:'Alerts',policies:'Product policies'})[name]||'Fleet overview';}
function navigate(name){if(!['overview','incidents','alerts','policies'].includes(name))name='overview';showView(name);history.replaceState(null,'',`#${name}`)}
function closeModal(){$('policy-modal').hidden=true}
function openPolicy(product){
  const form=$('policy-form');form.reset();$('form-error').textContent='';
  const p=state.policies.find(x=>x.product_id===product);
  if(p)Object.entries(p).forEach(([key,value])=>{if(form.elements[key])form.elements[key].value=key==='effective_at'?String(value).slice(0,16):value});
  else {const d=state.policy;Object.entries(d).forEach(([key,value])=>{if(form.elements[key])form.elements[key].value=value});form.elements.product_id.value='';form.elements.product_name.value='';form.elements.effective_at.value='';}
  $('policy-modal').hidden=false;form.elements.product_id.focus();
}
document.querySelectorAll('.nav-item[data-view],.text-link').forEach(a=>a.addEventListener('click',e=>{e.preventDefault();navigate(a.dataset.view)}));
window.addEventListener('hashchange',()=>navigate(location.hash.slice(1)));
document.querySelectorAll('.filter-chip').forEach(b=>b.addEventListener('click',()=>{incidentFilter=b.dataset.filter;document.querySelectorAll('.filter-chip').forEach(x=>x.classList.toggle('selected',x===b));renderIncidents()}));
$('refresh').addEventListener('click',loadDashboard);$('incident-refresh').addEventListener('click',loadDashboard);
$('policy-open').addEventListener('click',()=>openPolicy());$('policy-open-2').addEventListener('click',()=>openPolicy());
$('policy-cards').addEventListener('click',e=>{const button=e.target.closest('.policy-edit');if(button)openPolicy(button.dataset.product)});
$('policy-close').addEventListener('click',closeModal);$('policy-cancel').addEventListener('click',closeModal);
$('policy-modal').addEventListener('click',e=>{if(e.target===$('policy-modal'))closeModal()});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeModal()});
$('policy-form').addEventListener('submit',async e=>{e.preventDefault();$('form-error').textContent='';const form=e.currentTarget;const data=Object.fromEntries(new FormData(form));
  for(const k of Object.keys(data))if(k!=='product_id'&&k!=='product_name'&&k!=='effective_at')data[k]=Number(data[k]);
  if(data.effective_at)data.effective_at=new Date(data.effective_at).toISOString();else delete data.effective_at;
  try{const res=await fetch('/api/policies',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const body=await res.json();if(!res.ok)throw new Error(body.error||'Could not save policy');closeModal();showToast(`Policy ${body.policy.product_id} v${body.policy.version} saved`);await loadDashboard();}
  catch(err){$('form-error').textContent=err.message;}
});
showView(location.hash.slice(1)||'overview');loadDashboard();
