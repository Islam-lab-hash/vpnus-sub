const MAX_BYTES=2*1024*1024;
const CORS={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'Content-Type','Access-Control-Allow-Methods':'GET,OPTIONS'};
const reply=(body,status=200,type='text/plain; charset=utf-8')=>new Response(body,{status,headers:{...CORS,'Content-Type':type,'Cache-Control':'no-store'}});
export default{async fetch(request,env){
  if(request.method==='OPTIONS')return new Response(null,{status:204,headers:CORS});
  const url=new URL(request.url);
  if(url.pathname==='/health')return reply(JSON.stringify({status:'ok'}),200,'application/json');
  if(url.pathname==='/subscription'){if(!env.SUBSCRIPTION_URL)return reply('SUBSCRIPTION_URL secret is not configured',503);return proxy(env.SUBSCRIPTION_URL,url,request)}
  if(url.pathname==='/p/'||url.pathname==='/p'){const target=url.searchParams.get('u');if(!target)return reply("missing 'u' parameter",400);return proxy(unwrap(target),url,request)}
  return reply('Happ Toolkit Worker',200);
}};
function unwrap(value){if(value.startsWith('happ://add/'))return value.slice(11);return value}
async function proxy(target,requestUrl,request){
  let upstream;try{upstream=new URL(target)}catch{return reply('invalid URL',400)}
  if(!['http:','https:'].includes(upstream.protocol))return reply('encrypted happ://crypt links require a compatible private-key service',400);
  if(isLocal(upstream.hostname))return reply('private/local address blocked',403);
  const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),10000);
  const ua=requestUrl.searchParams.get('ua')||'Happ/4.3.0';const headers=new Headers({'Accept':'*/*'});if(ua!=='none')headers.set('User-Agent',ua);
  const hwid=requestUrl.searchParams.get('hwid');if(hwid)headers.set('X-HWID',hwid);
  try{const r=await fetch(upstream,{headers,redirect:'follow',signal:controller.signal});if(!r.ok)return reply(`upstream returned HTTP ${r.status}`,502);const buf=await r.arrayBuffer();if(buf.byteLength>MAX_BYTES)return reply('upstream response too large',502);const raw=new TextDecoder().decode(buf);return format(raw,requestUrl.searchParams.get('format')||'raw',r.headers)}catch(e){return reply(`upstream: ${e.name==='AbortError'?'timeout':'request failed'}`,502)}finally{clearTimeout(timer)}
}
function format(raw,kind,headers){let text=raw.trim();if(!text.includes('://')&&!text.startsWith('{')&&!text.startsWith('[')){try{text=atob(text.replace(/\s/g,''))}catch{}}
  if(kind==='txt')return reply(extractLinks(text).join('\n')+'\n');
  if(kind==='base64')return reply(btoa(unescape(encodeURIComponent(extractLinks(text).join('\n')+'\n'))));
  if(kind==='json'){try{return reply(JSON.stringify(JSON.parse(text),null,2)+'\n',200,'application/json')}catch{return reply(JSON.stringify({links:extractLinks(text)},null,2)+'\n',200,'application/json')}}
  return new Response(raw,{headers:{...CORS,'Content-Type':headers.get('Content-Type')||'text/plain; charset=utf-8','Cache-Control':'no-store'}})
}
function extractLinks(text){return text.split(/\r?\n/).map(x=>x.trim()).filter(x=>/^(vless|vmess|trojan|ss|hy2|hysteria2):\/\//.test(x))}
function isLocal(host){const h=host.toLowerCase();if(h==='localhost'||h.endsWith('.local')||h==='::1')return true;const m=h.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);if(!m)return false;const a=+m[1],b=+m[2];return a===10||a===127||a===0||(a===169&&b===254)||(a===172&&b>=16&&b<=31)||(a===192&&b===168)}
