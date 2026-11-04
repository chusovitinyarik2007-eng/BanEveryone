(()=>{'use strict';
const $=id=>document.getElementById(id),base=location.pathname.replace(/\/+$/,''),
  esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state=null, csrf='', busy=false;
function info(message,error=false){const el=$('toast');el.textContent=message;el.className='toast'+(error?' error':'');el.hidden=false;clearTimeout(info.timer);info.timer=setTimeout(()=>el.hidden=true,6000)}
async function api(path,method='GET',body=null){const headers={'Accept':'application/json'};
 if(method!=='GET'){headers['Content-Type']='application/json';headers['X-CSRF-Token']=csrf;}
 const r=await fetch(base+'/api/vless'+path,{method,credentials:'same-origin',cache:'no-store',headers,body:body===null?undefined:JSON.stringify(body)});
 let obj={};try{obj=await r.json()}catch{}if(!r.ok)throw new Error(typeof obj.detail==='string'?obj.detail:'HTTP '+r.status);return obj;}
async function load(){state=await api('/state');csrf=state.csrf;render();}
async function change(path,method,body){if(busy)return;busy=true;try{await api(path,method,body);await load();info('Изменения сохранены, JSON пересобран.');}catch(e){info(e.message,true)}finally{busy=false}}
function selectTab(vless){$('basePanel').hidden=vless;$('vlessPanel').hidden=!vless;
 $('tabBase').className='btn '+(vless?'outline':'primary');$('tabVless').className='btn '+(vless?'primary':'outline');
 $('tabBase').setAttribute('aria-selected',String(!vless));$('tabVless').setAttribute('aria-selected',String(vless));
 if(vless)load().catch(e=>info('Не удалось получить Vless config: '+e.message,true));}
function render(){if(!state)return;
 $('vEnabled').checked=state.enabled;$('vName').value=state.name;$('vStrategy').value=state.strategy;$('vInterval').value=state.interval;$('vProbe').value=state.probe_url;
 $('vRules').value=state.direct_rules.join('\n');$('vlessStatus').textContent=state.enabled?(state.compiled?'● Активен':'○ Нет активных серверов'):'○ Выключен';
 $('vCount').textContent=state.nodes.length+' всего · '+state.active_nodes+' включены';
 $('vCopyAll').disabled=state.nodes.length===0;
 $('vPreview').href=base+'/api/vless/profile';
 $('vNodeList').innerHTML=state.nodes.map((n,i)=>`<article class="v-node" data-node-id="${esc(n.id)}">
 <div class="v-node-head"><strong>Сервер ${i+1} · ${esc(n.name)}</strong><label class="switch-line"><input class="v-node-enabled" type="checkbox" ${n.enabled?'checked':''}>Активен</label></div>
 <label>Название<input class="v-node-name" maxlength="100" value="${esc(n.name)}"></label>
 <label>Ссылка VLESS<textarea class="v-uri" spellcheck="false" rows="2">${esc(n.uri)}</textarea></label>
 <div class="v-actions"><button class="btn outline v-node-save" type="button">Сохранить</button><button class="btn danger v-node-delete" type="button">Удалить</button></div>
 </article>`).join('')||'<div class="v-empty">Ключей пока нет. Вставь одну или несколько ссылок выше.</div>';
}
$('tabBase').addEventListener('click',()=>selectTab(false));$('tabVless').addEventListener('click',()=>selectTab(true));
window.addEventListener('auto-admin-ready',()=>{if(!$('vlessPanel').hidden)load().catch(e=>info(e.message,true));});
$('vSaveSettings').addEventListener('click',()=>change('/settings','PATCH',{enabled:$('vEnabled').checked,name:$('vName').value.trim(),
 strategy:$('vStrategy').value,interval:$('vInterval').value,probe_url:$('vProbe').value.trim(),direct_rules:$('vRules').value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean)}));
$('vEnabled').addEventListener('change',e=>change('/settings','PATCH',{enabled:e.target.checked}));
$('vAdd').addEventListener('click',async()=>{const links=$('vNewLinks').value.trim();if(!links){info('Вставь VLESS-ссылки',true);return;}if(busy)return;
 busy=true;try{const result=await api('/keys','POST',{links});$('vNewLinks').value='';await load();
  const skipped=result.skipped||[];const msg='Добавлено '+(result.added_count??'?')+' VLESS-ключей, JSON пересобран';
  info(skipped.length?msg+'. Пропущено '+skipped.length+': '+skipped.map(x=>'строка '+x.line+' ('+x.reason+')').join(', '):msg,!!skipped.length);
 }catch(e){info(e.message,true)}finally{busy=false}});
$('vCopyAll').addEventListener('click',async()=>{
 // The authenticated state already contains the original URIs in DB order.
 // Include disabled nodes too; never copy generated JSON or secret-less labels.
 if(!state || !state.nodes.length){info('Нет ключей для копирования',true);return;}
 const content=state.nodes.map(node=>node.uri).join('\n');
 try{
   let copied=false;
   if(navigator.clipboard && navigator.clipboard.writeText){
     try{await navigator.clipboard.writeText(content);copied=true;}catch(_e){}
   }
   if(!copied){
     const el=document.createElement('textarea');
     el.value=content;el.setAttribute('readonly','');
     el.style.cssText='position:fixed;left:-9999px;top:0';
     document.body.appendChild(el);el.focus();el.select();
     copied=!!document.execCommand('copy');el.remove();
   }
   if(!copied)throw new Error('Браузер не разрешил автоматическое копирование');
   info('Скопировано '+state.nodes.length+' VLESS-ключей, по одному на строку');
 }catch(e){info('Не удалось скопировать ключи: '+e.message,true);}
});
$('vClear').addEventListener('click',()=>{if(confirm('Удалить ВСЕ вручную добавленные VLESS-ключи? Действие необратимо.'))change('/keys','DELETE',{});});
$('vNodeList').addEventListener('click',e=>{const item=e.target.closest('[data-node-id]');if(!item)return;
 const id=encodeURIComponent(item.dataset.nodeId);
 if(e.target.closest('.v-node-delete')){if(confirm('Удалить этот VLESS-ключ?'))change('/keys/'+id,'DELETE',{});}
 else if(e.target.closest('.v-node-save'))change('/keys/'+id,'PATCH',{uri:item.querySelector('.v-uri').value.trim(),
 name:item.querySelector('.v-node-name').value.trim(),enabled:item.querySelector('.v-node-enabled').checked});});
$('vNodeList').addEventListener('change',e=>{const item=e.target.closest('[data-node-id]');if(item&&e.target.matches('.v-node-enabled'))change('/keys/'+encodeURIComponent(item.dataset.nodeId),'PATCH',{enabled:e.target.checked});});
})();
