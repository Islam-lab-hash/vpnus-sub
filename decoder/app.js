const $=s=>document.querySelector(s);let last={text:'',format:'json'};
const source=$('#source'),backend=$('#backend'),status=$('#status');
backend.value=localStorage.getItem('happ-backend')||'';
function base(){return backend.value.trim().replace(/\/$/,'')}
function setStatus(t,bad=false){status.innerHTML=`<i style="background:${bad?'#e04848':'#2eb872'}"></i> ${t}`}
function normalize(u){u=u.trim();return u.startsWith('happ://add/')?u.slice(11):u}
function parseText(text){
  let decoded=text.trim();try{if(!decoded.includes('://')&&!decoded.startsWith('{')&&!decoded.startsWith('['))decoded=atob(decoded.replace(/\s/g,''))}catch{}
  let data;try{data=JSON.parse(decoded)}catch{}
  const servers=[];
  const addLink=line=>{try{const u=new URL(line);servers.push({name:decodeURIComponent(u.hash.slice(1))||u.hostname,protocol:u.protocol.slice(0,-1),host:u.hostname,port:u.port})}catch{}};
  decoded.split(/\r?\n/).filter(x=>/^(vless|vmess|trojan|ss|hy2|hysteria2):\/\//.test(x)).forEach(addLink);
  const profiles=Array.isArray(data)?data:[data];profiles.filter(Boolean).forEach(p=>(p.outbounds||[]).forEach(o=>{if(['freedom','blackhole','dns'].includes(o.protocol))return;const v=o.settings?.vnext?.[0];servers.push({name:p.remarks||o.tag||v?.address||'Сервер',protocol:o.protocol||'xray',host:v?.address||o.settings?.address||'',port:v?.port||o.settings?.port||''})}));
  return {decoded,data,servers};
}
function render(text,format){const p=parseText(text);last={text,format};$('#results').classList.remove('hidden');$('#serverCount').textContent=p.servers.length;$('#protocols').textContent=[...new Set(p.servers.map(x=>x.protocol))].join(', ')||'—';$('#payloadSize').textContent=`${(new Blob([text]).size/1024).toFixed(1)} КБ`;$('#rawOutput').textContent=text;$('#serverList').innerHTML=p.servers.slice(0,200).map(s=>`<div class="server"><div><b>${escapeHtml(s.name)}</b><small>${escapeHtml(s.host)}${s.port?':'+escapeHtml(String(s.port)):''}</small></div><span class="pill">${escapeHtml(s.protocol)}</span></div>`).join('')||'<div class="server"><div><b>Структура получена</b><small>Серверы не распознаны автоматически — откройте исходный ответ.</small></div></div>';$('#results').scrollIntoView({behavior:'smooth'})}
function escapeHtml(s){return String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function request(privateSub=false){if(!base())throw new Error('Укажите URL развернутого Cloudflare Worker');localStorage.setItem('happ-backend',base());const format=$('#format').value;const u=privateSub?'':normalize(source.value);if(!privateSub&&!u)throw new Error('Введите ссылку подписки');const url=privateSub?`${base()}/subscription?format=${format}`:`${base()}/p/?u=${encodeURIComponent(u)}&format=${format}`;setStatus('Загрузка…');const r=await fetch(url);const text=await r.text();if(!r.ok)throw new Error(text||`HTTP ${r.status}`);render(text,format);setStatus('Готово')}
$('#analyze').onclick=()=>request().catch(e=>setStatus(e.message,true));$('#loadPrivate').onclick=()=>request(true).catch(e=>setStatus(e.message,true));$('#clear').onclick=()=>{source.value='';$('#results').classList.add('hidden');setStatus('Готов')};
document.querySelectorAll('.nav').forEach(b=>b.onclick=()=>{document.querySelectorAll('.nav').forEach(x=>x.classList.remove('active'));b.classList.add('active');$('#decoderView').classList.toggle('hidden',b.dataset.view!=='decoder');$('#proxyView').classList.toggle('hidden',b.dataset.view!=='proxy')});
$('#buildLink').onclick=()=>{const u=normalize(source.value);if(!base()||!u)return setStatus('Укажите backend и подписку',true);const q=new URLSearchParams({u,ua:$('#ua').value,os:$('#os').value});if($('#hwid').value)q.set('hwid',$('#hwid').value);if($('#model').value)q.set('model',$('#model').value);$('#proxyLink').textContent=`${base()}/p/?${q}`};
async function copy(t){await navigator.clipboard.writeText(t);setStatus('Скопировано')}
$('#copyLink').onclick=()=>copy($('#proxyLink').textContent);$('#copy').onclick=()=>copy(last.text);$('#download').onclick=()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([last.text],{type:'text/plain'}));a.download=`subscription.${last.format==='raw'?'txt':last.format}`;a.click();URL.revokeObjectURL(a.href)};
